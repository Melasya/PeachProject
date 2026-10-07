#!/usr/bin/env bash
# Create or update the Cognito sign-in stack defined in infra/auth.yaml: user
# pool, Google identity provider, public web client, managed login domain and
# its branding. Then write the non-secret outputs into .env, where Compose and
# scripts/deploy-frontend.sh (via the stack) pick them up.
#
# The Google client secret is read from the gitignored .env and handed to
# CloudFormation as a NoEcho parameter through a 0600 file that is deleted on
# exit. It is never printed, never passed on the command line, and never
# reaches the frontend: the build only reads the stack's public outputs.
#
# Run it locally, after deploy-frontend has created the site (the callback URL
# needs its address). CI does not run it - CI has no Google secret.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Git Bash: aws.exe cannot open /c/... paths; C:/... works for it and for bash.
command -v cygpath >/dev/null 2>&1 && ROOT="$(cygpath -m "${ROOT}")"
ENV_FILE="${ROOT}/.env"
cd "${ROOT}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -f "${ENV_FILE}" ]]; then
  # Variables already exported win over .env: `AWS_REGION=eu-central-1 make x`
  # must not be quietly reset to the region .env names.
  preset="$(export -p)"
  set -a
  # shellcheck disable=SC1090,SC1091
  source "${ENV_FILE}"
  set +a
  eval "${preset}"
fi

# A blank AWS_PROFILE is read as a profile literally named "", and blank keys
# short-circuit the credential chain. Treat empty as absent.
for var in AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  [[ -n "${!var:-}" ]] || unset "${var}"
done

PROJECT_NAME="${PROJECT_NAME:-peach}"
STACK_NAME="${AUTH_STACK_NAME:-${PROJECT_NAME}-auth}"
FRONTEND_STACK="${FRONTEND_STACK_NAME:-${PROJECT_NAME}-frontend}"
# Cognito's managed login domain and this stack live in one region.
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"
DOMAIN_PREFIX="${COGNITO_DOMAIN_PREFIX:-peach-melasya}"
LOCAL_URL="${LOCAL_URL:-http://localhost:${FRONTEND_PORT:-3000}}"

# --- preflight --------------------------------------------------------------

for tool in aws python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required but not installed"
done
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "no usable AWS credentials - set AWS_PROFILE or the AWS_* keys in .env"

[[ -n "${GOOGLE_CLIENT_ID:-}" ]] \
  || die "GOOGLE_CLIENT_ID is not set in .env - create the Google OAuth client first (README section 14)"
[[ -n "${GOOGLE_CLIENT_SECRET:-}" ]] \
  || die "GOOGLE_CLIENT_SECRET is not set in .env - create the Google OAuth client first (README section 14)"

# The callback URLs point at the deployed site, so it has to exist first.
SITE_URL="${SITE_URL:-$(aws cloudformation describe-stacks --stack-name "${FRONTEND_STACK}" \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text 2>/dev/null || true)}"
[[ "${SITE_URL}" == https://* ]] \
  || die "no SiteUrl from stack ${FRONTEND_STACK} - run make deploy-frontend first"
SITE_URL="${SITE_URL%/}"

log "stack ${STACK_NAME} in ${AWS_REGION}"
log "managed login  https://${DOMAIN_PREFIX}.auth.${AWS_REGION}.amazoncognito.com"
log "callbacks      ${SITE_URL}/login/  ${LOCAL_URL}/login/"

# --- deploy -----------------------------------------------------------------

# Parameters go through a 0600 file rather than argv, so the secret never
# shows up in `ps`, and the file is gone when the script exits.
PARAMS_FILE="auth-params.json"
( umask 077 && : > "${PARAMS_FILE}" )
chmod 600 "${PARAMS_FILE}"
trap 'rm -f "${PARAMS_FILE}"' EXIT

PROJECT_NAME="${PROJECT_NAME}" \
DOMAIN_PREFIX="${DOMAIN_PREFIX}" \
SITE_URL="${SITE_URL}" \
LOCAL_URL="${LOCAL_URL}" \
python3 - "${PARAMS_FILE}" <<'PY'
import json, os, sys

params = {
    "ProjectName": os.environ["PROJECT_NAME"],
    "DomainPrefix": os.environ["DOMAIN_PREFIX"],
    "SiteUrl": os.environ["SITE_URL"],
    "LocalUrl": os.environ["LOCAL_URL"],
    "GoogleClientId": os.environ["GOOGLE_CLIENT_ID"].strip(),
    "GoogleClientSecret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
}
with open(sys.argv[1], "w") as fh:
    json.dump([{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()], fh)
PY

if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" >/dev/null 2>&1; then
  log "first deploy - creating ${STACK_NAME}"
else
  log "updating ${STACK_NAME}"
fi

if ! aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file infra/auth.yaml \
  --parameter-overrides "file://${PARAMS_FILE}" \
  --no-fail-on-empty-changeset \
  --tags "PROJECT_NAME=${PROJECT_NAME}"; then
  warn "deploy failed - most recent failure reasons:"
  aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" \
    --max-items 30 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`||ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table >&2 || true
  exit 1
fi
rm -f "${PARAMS_FILE}"

outputs() {
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

AUTHORITY="$(outputs Authority)"
CLIENT_ID="$(outputs ClientId)"
HOSTED_DOMAIN="$(outputs HostedDomainUrl)"

# --- report -----------------------------------------------------------------

# Rewrite one KEY=VALUE in .env, leaving every other line exactly as it was.
# Only public values go here; the Compose dev server reads them.
env_set() {
  KEY="$1" VALUE="$2" ENV_FILE="${ENV_FILE}" python3 - <<'PY'
import os, re

key, value, path = os.environ["KEY"], os.environ["VALUE"], os.environ["ENV_FILE"]
lines = open(path).read().splitlines() if os.path.exists(path) else []
pattern = re.compile(rf"^{re.escape(key)}=")

for i, line in enumerate(lines):
    if pattern.match(line):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")

open(path, "w").write("\n".join(lines) + "\n")
PY
  log "wrote ${1}=${2} to .env"
}

env_set COGNITO_AUTHORITY "${AUTHORITY}"
env_set COGNITO_CLIENT_ID "${CLIENT_ID}"
env_set COGNITO_DOMAIN "${HOSTED_DOMAIN}"

echo
echo "  login page     ${SITE_URL}/login/"
echo "  managed login  ${HOSTED_DOMAIN}"
echo "  authority      ${AUTHORITY}"
echo "  client id      ${CLIENT_ID}"
echo
echo "Next: make deploy-frontend, so the site is built with these values."
