#!/usr/bin/env bash
# Create or update the agenda-parser stack (infra/ai.yml): a Lambda outside the
# VPC that suggests meeting fields with Google's Gemini API, behind its own
# function URL. Everything it needs is read from the other stacks - the
# image from peach-backend, the issuer and client from peach-auth, the site
# origin from peach-frontend - so nothing is copied by hand. Writes PARSER_URL
# to .env for deploy-frontend.sh and Compose.
#
# The Gemini API key must already be in SSM Parameter Store as a SecureString
# named GEMINI_API_KEY_PARAMETER (default /peach/gemini-api-key) - see README
# section 16. This script checks the parameter exists without reading its
# value, never handles the key itself, and makes no model call.
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
  preset="$(export -p)"
  set -a
  # shellcheck disable=SC1090,SC1091
  source "${ENV_FILE}"
  set +a
  eval "${preset}"
fi
for var in AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  [[ -n "${!var:-}" ]] || unset "${var}"
done

PROJECT_NAME="${PROJECT_NAME:-peach}"
STACK_NAME="${AI_STACK_NAME:-${PROJECT_NAME}-ai}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"
PARSER_MODEL="${PARSER_MODEL:-gemini-3.5-flash-lite}"
PARSER_EFFORT="${PARSER_EFFORT:-low}"
KEY_PARAMETER="${GEMINI_API_KEY_PARAMETER:-/${PROJECT_NAME}/gemini-api-key}"
LOCAL_URL="${LOCAL_URL:-http://localhost:${FRONTEND_PORT:-3000}}"

command -v aws >/dev/null 2>&1 || die "aws cli is required"
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "no usable AWS credentials - set AWS_PROFILE or the AWS_* keys in .env"

stack_value() {  # stack, kind (Outputs|Parameters), key
  local field="OutputKey" value="OutputValue"
  [[ "$2" == "Parameters" ]] && field="ParameterKey" value="ParameterValue"
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].$2[?${field}=='$3'].${value}" --output text 2>/dev/null || true
}
need() { [[ -n "$1" && "$1" != "None" ]] || die "$2"; }

IMAGE_URI="$(stack_value "${PROJECT_NAME}-backend" Parameters ImageUri)"
ARCHITECTURE="$(stack_value "${PROJECT_NAME}-backend" Parameters Architecture)"
ISSUER="$(stack_value "${PROJECT_NAME}-auth" Outputs Authority)"
CLIENT_ID="$(stack_value "${PROJECT_NAME}-auth" Outputs ClientId)"
SITE_URL="$(stack_value "${PROJECT_NAME}-frontend" Outputs SiteUrl)"
need "${IMAGE_URI}" "no ImageUri on ${PROJECT_NAME}-backend - run make deploy-backend first"
need "${ISSUER}" "no Authority on ${PROJECT_NAME}-auth - run make deploy-auth first"
need "${CLIENT_ID}" "no ClientId on ${PROJECT_NAME}-auth - run make deploy-auth first"
need "${SITE_URL}" "no SiteUrl on ${PROJECT_NAME}-frontend - run make deploy-frontend first"
SITE_URL="${SITE_URL%/}"

# describe-parameters returns metadata only - never the value.
KEY_TYPE="$(MSYS_NO_PATHCONV=1 aws ssm describe-parameters \
  --parameter-filters "Key=Name,Values=${KEY_PARAMETER}" \
  --query 'Parameters[0].Type' --output text 2>/dev/null || true)"
[[ "${KEY_TYPE}" == "SecureString" ]] \
  || die "no SecureString ${KEY_PARAMETER} in SSM (found: ${KEY_TYPE:-nothing}) - create it first (README section 16)"

log "stack ${STACK_NAME} in ${AWS_REGION}: image ${IMAGE_URI##*/}, model ${PARSER_MODEL}"
log "CORS origins ${SITE_URL}, ${LOCAL_URL}"
log "API key from SSM ${KEY_PARAMETER} (read by the function at run time)"

# MSYS_NO_PATHCONV: Git Bash would otherwise rewrite /peach/... into a Windows path.
if ! MSYS_NO_PATHCONV=1 aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file infra/ai.yml \
  --parameter-overrides \
    "ProjectName=${PROJECT_NAME}" \
    "ImageUri=${IMAGE_URI}" \
    "Architecture=${ARCHITECTURE:-arm64}" \
    "ParserModel=${PARSER_MODEL}" \
    "ParserEffort=${PARSER_EFFORT}" \
    "GeminiApiKeyParameter=${KEY_PARAMETER}" \
    "CognitoIssuer=${ISSUER}" \
    "CognitoClientId=${CLIENT_ID}" \
    "AllowedOrigins=${SITE_URL},${LOCAL_URL}" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --tags "PROJECT_NAME=${PROJECT_NAME}"; then
  warn "deploy failed - most recent failure reasons:"
  aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" --max-items 30 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`||ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table >&2 || true
  exit 1
fi

PARSER_URL="$(stack_value "${STACK_NAME}" Outputs ParserUrl)"
PARSER_URL="${PARSER_URL%/}"

KEY=PARSER_URL VALUE="${PARSER_URL}" ENV_FILE="${ENV_FILE}" python3 - <<'PY'
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
log "wrote PARSER_URL to .env"

echo
echo "  parser     ${PARSER_URL}"
echo "  logs       aws logs tail $(stack_value "${STACK_NAME}" Outputs ParserLogGroupName) --follow"
echo
echo "Next: make deploy-frontend, so the site is built with NEXT_PUBLIC_PARSER_URL."
