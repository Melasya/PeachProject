#!/usr/bin/env bash
# Build the backend image, push it to ECR, roll the Lambda function defined in
# infra/backend.yaml (function URL in front, Aurora Serverless v2 behind), run
# migrations, and write the API URL into .env for deploy-frontend.sh to build
# against.
#
# Safe to re-run: the CloudFormation stack is the source of truth, so every run
# after the first is an in-place update with the new image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${ROOT}/infra/backend.yaml"
ENV_FILE="${ROOT}/.env"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- configuration ----------------------------------------------------------

# .env is the same file Compose reads; anything already exported wins over it.
if [[ -f "${ENV_FILE}" ]]; then
  # Variables already exported win over .env: `AWS_REGION=eu-central-1 make x`
  # must not be quietly reset to the region .env names.
  preset="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  source "${ENV_FILE}"
  set +a
  eval "${preset}"
fi

# A blank AWS_PROFILE is read as a profile literally named "", and blank keys
# short-circuit the credential chain - which is exactly what a .env full of
# empty placeholders hands us. Treat empty as absent.
for var in AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  [[ -n "${!var:-}" ]] || unset "${var}"
done

PROJECT_NAME="${PROJECT_NAME:-peach}"
STACK_NAME="${STACK_NAME:-${PROJECT_NAME}-backend}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

ECR_REPOSITORY="${ECR_REPOSITORY:-${PROJECT_NAME}-backend}"
LAMBDA_ARCHITECTURE="${LAMBDA_ARCHITECTURE:-arm64}"
case "${LAMBDA_ARCHITECTURE}" in
  arm64) CFN_ARCHITECTURE=arm64 ;;
  amd64) CFN_ARCHITECTURE=x86_64 ;;
  *) die "LAMBDA_ARCHITECTURE must be arm64 or amd64, got '${LAMBDA_ARCHITECTURE}'" ;;
esac

# CloudFormation copies stack tags onto every resource it can tag; the template
# also sets the same tag on each resource explicitly.
TAGS=("PROJECT_NAME=${PROJECT_NAME}")

# --- helpers ----------------------------------------------------------------

# Rewrite one KEY=VALUE in .env, leaving every other line - credentials very
# much included - exactly as it was.
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

# --- preflight --------------------------------------------------------------

for tool in aws docker python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required but not installed"
done
docker info >/dev/null 2>&1 || die "docker daemon is not running"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "no usable AWS credentials - set AWS_PROFILE or the AWS_* keys in .env"
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
log "account ${ACCOUNT_ID} in ${AWS_REGION} as ${CALLER}"

# --- network ----------------------------------------------------------------

if [[ -z "${AWS_VPC_ID:-}" ]]; then
  AWS_VPC_ID="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
    --query 'Vpcs[0].VpcId' --output text)"
  [[ "${AWS_VPC_ID}" != "None" && -n "${AWS_VPC_ID}" ]] \
    || die "no default VPC in ${AWS_REGION} - set AWS_VPC_ID and AWS_SUBNET_IDS"
  warn "AWS_VPC_ID unset, using the default VPC ${AWS_VPC_ID}"
fi

if [[ -z "${AWS_SUBNET_IDS:-}" ]]; then
  # Lambda cannot place network interfaces in every AZ (us-east-1's use1-az3
  # is the known case), so those subnets are left out.
  AWS_SUBNET_IDS="$(EXCLUDED="${LAMBDA_UNSUPPORTED_AZ_IDS:-use1-az3}" python3 -c '
import json, os, subprocess, sys
excluded = set(os.environ["EXCLUDED"].split(","))
subnets = json.loads(subprocess.check_output([
    "aws", "ec2", "describe-subnets", "--output", "json",
    "--filters", "Name=vpc-id,Values=" + sys.argv[1], "Name=default-for-az,Values=true",
]))["Subnets"]
print(",".join(s["SubnetId"] for s in subnets if s["AvailabilityZoneId"] not in excluded))
' "${AWS_VPC_ID}")"
  [[ -n "${AWS_SUBNET_IDS}" ]] || die "no default subnets in ${AWS_VPC_ID}"
  warn "AWS_SUBNET_IDS unset, using ${AWS_SUBNET_IDS}"
fi

if [[ "${AWS_SUBNET_IDS}" != *,* ]]; then
  die "Aurora needs subnets in at least two availability zones"
fi

# --- ecr --------------------------------------------------------------------

# The repository lives outside the stack: the function cannot be created until
# there is an image to run, so the push has to happen first.
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
if ! aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" >/dev/null 2>&1; then
  log "creating ECR repository ${ECR_REPOSITORY}"
  aws ecr create-repository \
    --repository-name "${ECR_REPOSITORY}" \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability MUTABLE \
    --tags "Key=PROJECT_NAME,Value=${PROJECT_NAME}" >/dev/null
  aws ecr put-lifecycle-policy \
    --repository-name "${ECR_REPOSITORY}" \
    --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"keep the last 3 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}' \
    >/dev/null
fi

if [[ -z "${IMAGE_TAG:-}" || "${IMAGE_TAG}" == "latest" ]]; then
  if git -C "${ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    IMAGE_TAG="$(git -C "${ROOT}" rev-parse --short=12 HEAD)"
    # A dirty tree gets a unique tag, or CloudFormation would see the same
    # ImageUri as last time and leave the function on the old image.
    git -C "${ROOT}" diff --quiet HEAD -- backend \
      || IMAGE_TAG="${IMAGE_TAG}-dirty-$(date -u +%Y%m%d%H%M%S)"
  else
    IMAGE_TAG="$(date -u +%Y%m%d%H%M%S)"
  fi
fi
IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

log "building ${IMAGE_URI} for linux/${LAMBDA_ARCHITECTURE}"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}" >/dev/null

# Lambda accepts a single-platform image manifest only. buildx otherwise adds
# provenance and SBOM attestations, which turn the push into a manifest list
# that CreateFunction rejects.
docker buildx build \
  --platform "linux/${LAMBDA_ARCHITECTURE}" \
  --target lambda \
  --provenance=false \
  --sbom=false \
  --tag "${IMAGE_URI}" \
  --push \
  "${ROOT}/backend"

# --- database password ------------------------------------------------------

# CloudFormation composes DATABASE_URL from this password and the Aurora
# endpoint, so it has to stay the same across deploys. Read it back from the
# secret the stack already owns; only mint a new one on the very first run.
DB_PASSWORD=""
SECRET_ARN="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseUrlSecretArn'].OutputValue" \
  --output text 2>/dev/null || true)"

if [[ -n "${SECRET_ARN}" && "${SECRET_ARN}" != "None" ]]; then
  DB_PASSWORD="$(aws secretsmanager get-secret-value --secret-id "${SECRET_ARN}" \
    --query SecretString --output text 2>/dev/null \
    | python3 -c 'import sys,urllib.parse; print(urllib.parse.urlsplit(sys.stdin.read().strip()).password or "")')"
fi

if [[ -z "${DB_PASSWORD}" ]]; then
  log "generating the database password"
  # No /, ", @ or space: RDS rejects those, and it keeps the URL parseable.
  DB_PASSWORD="$(python3 -c '
import secrets, string
alphabet = string.ascii_letters + string.digits + "-_.~"
print("".join(secrets.choice(alphabet) for _ in range(40)))')"
fi

# --- deploy -----------------------------------------------------------------

# Parameters go through a 0600 file rather than argv, so the password never
# shows up in `ps`.
PARAMS_FILE="$(mktemp)"
chmod 600 "${PARAMS_FILE}"
RESULT_FILE="$(mktemp)"
trap 'rm -f "${PARAMS_FILE}" "${RESULT_FILE}"' EXIT

PROJECT_NAME="${PROJECT_NAME}" \
VPC_ID="${AWS_VPC_ID}" \
SUBNETS="${AWS_SUBNET_IDS}" \
IMAGE_URI="${IMAGE_URI}" \
CFN_ARCHITECTURE="${CFN_ARCHITECTURE}" \
MEMORY_SIZE="${LAMBDA_MEMORY_SIZE:-}" \
TIMEOUT_SECONDS="${LAMBDA_TIMEOUT_SECONDS:-}" \
DB_NAME="${DB_NAME:-${POSTGRES_DB:-peach}}" \
DB_USERNAME="${DB_USERNAME:-${POSTGRES_USER:-peach}}" \
DB_PASSWORD="${DB_PASSWORD}" \
DB_ENGINE_VERSION="${DB_ENGINE_VERSION:-}" \
DB_MIN_CAPACITY="${DB_MIN_CAPACITY:-}" \
DB_MAX_CAPACITY="${DB_MAX_CAPACITY:-}" \
DB_SECONDS_UNTIL_AUTO_PAUSE="${DB_SECONDS_UNTIL_AUTO_PAUSE:-}" \
APP_ENV="${APP_ENV_AWS:-production}" \
LOG_LEVEL="${LOG_LEVEL:-info}" \
CORS_ORIGINS="${API_CORS_ORIGINS:-}" \
python3 - "${PARAMS_FILE}" <<'PY'
import json, os, sys

params = {
    "ProjectName": os.environ["PROJECT_NAME"],
    "VpcId": os.environ["VPC_ID"],
    "SubnetIds": os.environ["SUBNETS"],
    "ImageUri": os.environ["IMAGE_URI"],
    "Architecture": os.environ["CFN_ARCHITECTURE"],
    "MemorySize": os.environ["MEMORY_SIZE"],
    "TimeoutSeconds": os.environ["TIMEOUT_SECONDS"],
    "DbName": os.environ["DB_NAME"],
    "DbUsername": os.environ["DB_USERNAME"],
    "DbPassword": os.environ["DB_PASSWORD"],
    "DbEngineVersion": os.environ["DB_ENGINE_VERSION"],
    "DbMinCapacity": os.environ["DB_MIN_CAPACITY"],
    "DbMaxCapacity": os.environ["DB_MAX_CAPACITY"],
    "DbSecondsUntilAutoPause": os.environ["DB_SECONDS_UNTIL_AUTO_PAUSE"],
    "AppEnv": os.environ["APP_ENV"],
    "LogLevel": os.environ["LOG_LEVEL"],
    "CorsOrigins": os.environ["CORS_ORIGINS"],
}
# An empty value means "leave this alone": CloudFormation reuses the stack's
# existing value for any parameter the deploy does not mention, and falls back
# to the template default on a brand new stack. Without this, a deploy from CI -
# which has no .env - would quietly reset CORS and capacity settings on a
# stack that already had them.
with open(sys.argv[1], "w") as fh:
    json.dump(
        [
            {"ParameterKey": k, "ParameterValue": v}
            for k, v in params.items()
            if v != ""
        ],
        fh,
    )
PY

if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" >/dev/null 2>&1; then
  log "first deploy - creating ${STACK_NAME} (Aurora takes around 10 minutes)"
else
  log "updating ${STACK_NAME}"
fi

if ! aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE}" \
  --parameter-overrides "file://${PARAMS_FILE}" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --tags "${TAGS[@]}"; then
  warn "deploy failed - most recent failure reasons:"
  aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" \
    --max-items 40 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`||ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table >&2 || true
  exit 1
fi

outputs() {
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

FUNCTION_NAME="$(outputs FunctionName)"

# --- migrate ----------------------------------------------------------------

# A direct invoke, not a request through the URL - see app/lambda_handler.py.
# If Aurora is paused, this is also what wakes it.
log "applying migrations"
aws lambda wait function-updated-v2 --function-name "${FUNCTION_NAME}"
FUNCTION_ERROR="$(aws lambda invoke \
  --function-name "${FUNCTION_NAME}" \
  --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 900 \
  --payload '{"action":"migrate"}' \
  --query FunctionError --output text \
  "${RESULT_FILE}")"
if [[ -n "${FUNCTION_ERROR}" && "${FUNCTION_ERROR}" != "None" ]]; then
  cat "${RESULT_FILE}" >&2
  echo >&2
  die "migrations failed - see: make logs-backend"
fi

# --- report -----------------------------------------------------------------

API_URL="$(outputs ApiUrl)"
API_URL="${API_URL%/}"

# deploy-frontend.sh compiles the bundle against this.
env_set BACKEND_URL "${API_URL}"

echo
echo "  api        ${API_URL}"
echo "  health     ${API_URL}/health"
echo "  docs       ${API_URL}/docs"
echo "  database   $(outputs DatabaseEndpoint)"
echo "  logs       aws logs tail $(outputs LogGroupName) --follow"
echo

if curl -fsS --max-time 30 "${API_URL}/health" >/dev/null 2>&1; then
  log "GET /health answered"
else
  warn "GET /health did not answer yet - check: make logs-backend"
fi

echo "Next: make deploy-frontend (it builds against BACKEND_URL), then set"
echo "API_CORS_ORIGINS to the frontend's origin and re-run this to narrow CORS."
