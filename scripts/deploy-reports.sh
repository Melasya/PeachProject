#!/usr/bin/env bash
# Create or update the weekly-report stack defined in infra/reports.yml:
# scheduler -> SQS (+ DLQ) -> builder Lambda (backend image, in the VPC) ->
# S3 reports/<week>.csv -> mailer Lambda (zip, outside the VPC) -> SES.
#
#   scripts/deploy-reports.sh               full deploy (needs REPORT_SENDER and
#                                           REPORT_RECIPIENTS in .env)
#   scripts/deploy-reports.sh --image-only  point the builder at peach-backend's
#                                           current image, nothing else
#
# Network, image and database settings come from the peach-backend stack, so
# deploy the backend (which also applies migrations) first. The database URL is
# read from the backend's secret into a 0600 parameter file that is deleted on
# exit; it is never printed or put on the command line. Nothing here touches
# the database, the backend stack or SES identities.
set -euo pipefail

MODE="full"
case "${1:-}" in
  "") ;;
  --image-only) MODE="image" ;;
  *) echo "usage: $0 [--image-only]" >&2; exit 2 ;;
esac

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
STACK_NAME="${REPORTS_STACK_NAME:-${PROJECT_NAME}-reports}"
BACKEND_STACK="${BACKEND_STACK_NAME:-${PROJECT_NAME}-backend}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

# --- preflight --------------------------------------------------------------

for tool in aws python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required but not installed"
done
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "no usable AWS credentials - set AWS_PROFILE or the AWS_* keys in .env"

aws cloudformation describe-stacks --stack-name "${BACKEND_STACK}" >/dev/null 2>&1 \
  || die "stack ${BACKEND_STACK} does not exist - run make deploy-backend first"

backend_param() {
  aws cloudformation describe-stacks --stack-name "${BACKEND_STACK}" \
    --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue" --output text
}
backend_output() {
  aws cloudformation describe-stacks --stack-name "${BACKEND_STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

IMAGE_URI="$(backend_param ImageUri)"
[[ -n "${IMAGE_URI}" && "${IMAGE_URI}" != "None" ]] || die "no ImageUri on ${BACKEND_STACK}"

# --- image only: follow the backend's image, keep every other value ------------

if [[ "${MODE}" == "image" ]]; then
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" >/dev/null 2>&1 \
    || { log "no ${STACK_NAME} stack - nothing to update"; exit 0; }
  CURRENT="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Parameters[?ParameterKey=='ImageUri'].ParameterValue" --output text)"
  if [[ "${CURRENT}" == "${IMAGE_URI}" ]]; then
    log "${STACK_NAME} already uses the backend's image"
    exit 0
  fi
  log "pointing the report builder at ${IMAGE_URI##*/}"
  read -r -a KEYS <<<"$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Parameters[].ParameterKey" --output text)"
  PARAMS=()
  for key in "${KEYS[@]}"; do
    if [[ "${key}" == "ImageUri" ]]; then
      PARAMS+=("ParameterKey=ImageUri,ParameterValue=${IMAGE_URI}")
    else
      PARAMS+=("ParameterKey=${key},UsePreviousValue=true")
    fi
  done
  aws cloudformation update-stack --stack-name "${STACK_NAME}" \
    --use-previous-template --capabilities CAPABILITY_IAM \
    --parameters "${PARAMS[@]}" >/dev/null
  aws cloudformation wait stack-update-complete --stack-name "${STACK_NAME}"
  log "builder updated"
  exit 0
fi

# --- full deploy: settings ------------------------------------------------------

[[ -n "${REPORT_SENDER:-}" ]] || die "REPORT_SENDER is not set in .env (a verified SES identity)"
[[ -n "${REPORT_RECIPIENTS:-}" ]] || die "REPORT_RECIPIENTS is not set in .env (comma-separated)"

VPC_ID="$(backend_param VpcId)"
SUBNET_IDS="$(backend_param SubnetIds)"
ARCHITECTURE="$(backend_param Architecture)"
SECRET_ARN="$(backend_output DatabaseUrlSecretArn)"
FUNCTION_SG="$(aws cloudformation describe-stack-resource --stack-name "${BACKEND_STACK}" \
  --logical-resource-id FunctionSecurityGroup \
  --query StackResourceDetail.PhysicalResourceId --output text)"
[[ "${FUNCTION_SG}" == sg-* ]] || die "could not read the backend's function security group"

# The gateway endpoint goes on the route tables the builder's subnets use: an
# explicit association if a subnet has one, the VPC's main table otherwise.
ROUTE_TABLES="$(VPC_ID="${VPC_ID}" SUBNETS="${SUBNET_IDS}" python3 -c '
import json, os, subprocess
vpc, subnets = os.environ["VPC_ID"], os.environ["SUBNETS"].split(",")
tables = json.loads(subprocess.check_output(["aws", "ec2", "describe-route-tables", "--output", "json",
    "--filters", "Name=vpc-id,Values=" + vpc]))["RouteTables"]
explicit = {a["SubnetId"]: t["RouteTableId"] for t in tables for a in t["Associations"] if a.get("SubnetId")}
main = [t["RouteTableId"] for t in tables if any(a.get("Main") for a in t["Associations"])]
print(",".join(sorted({explicit.get(s) or main[0] for s in subnets})))
')"
[[ -n "${ROUTE_TABLES}" ]] || die "could not work out the subnets' route tables"

SUBNET_COUNT="$(tr ',' '\n' <<<"${SUBNET_IDS}" | grep -c .)"
RECIPIENT_COUNT="$(tr ',' '\n' <<<"${REPORT_RECIPIENTS}" | grep -c '[^[:space:]]')"
log "stack ${STACK_NAME} in ${AWS_REGION}: ${SUBNET_COUNT} subnets, image ${IMAGE_URI##*/}, ${RECIPIENT_COUNT} recipient(s)"

# SES is configured separately and verified by hand; only warn here.
SENDER_STATUS="$(aws sesv2 get-email-identity --email-identity "${REPORT_SENDER}" \
  --query VerificationStatus --output text 2>/dev/null || echo "NOT_FOUND")"
if [[ "${SENDER_STATUS}" != "SUCCESS" ]]; then
  warn "REPORT_SENDER is not a verified SES identity yet (${SENDER_STATUS}); the mailer will fail until it is"
fi

# --- mailer zip -------------------------------------------------------------------

# Outside the stack, like the ECR repository: the function cannot be created
# until its code is in S3. Private, encrypted, tagged.
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-${PROJECT_NAME}-artifacts-${ACCOUNT_ID}-${AWS_REGION}}"
if ! aws s3api head-bucket --bucket "${ARTIFACT_BUCKET}" >/dev/null 2>&1; then
  log "creating the artifact bucket"
  if [[ "${AWS_REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${ARTIFACT_BUCKET}" >/dev/null
  else
    aws s3api create-bucket --bucket "${ARTIFACT_BUCKET}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "${ARTIFACT_BUCKET}" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "${ARTIFACT_BUCKET}" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-bucket-tagging --bucket "${ARTIFACT_BUCKET}" \
    --tagging "TagSet=[{Key=PROJECT_NAME,Value=${PROJECT_NAME}}]"
fi

WORK_DIR="$(mktemp -d)"
command -v cygpath >/dev/null 2>&1 && WORK_DIR="$(cygpath -m "${WORK_DIR}")"
PARAMS_FILE="reports-params.json"
trap 'rm -rf "${WORK_DIR}"; rm -f "${PARAMS_FILE}"' EXIT

# A fixed timestamp makes the zip - and so its key - depend only on the code.
MAILER_ZIP="$(SOURCE="backend/app/reports/mailer.py" OUT="${WORK_DIR}/mailer.zip" python3 -c '
import hashlib, os, zipfile
info = zipfile.ZipInfo("mailer.py", date_time=(2020, 1, 1, 0, 0, 0))
info.external_attr = 0o644 << 16
with zipfile.ZipFile(os.environ["OUT"], "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr(info, open(os.environ["SOURCE"], "rb").read())
print(hashlib.sha256(open(os.environ["OUT"], "rb").read()).hexdigest()[:16])
')"
MAILER_KEY="mailer/mailer-${MAILER_ZIP}.zip"
if ! aws s3api head-object --bucket "${ARTIFACT_BUCKET}" --key "${MAILER_KEY}" >/dev/null 2>&1; then
  log "uploading the mailer (${MAILER_KEY##*/})"
  aws s3 cp "${WORK_DIR}/mailer.zip" "s3://${ARTIFACT_BUCKET}/${MAILER_KEY}" --only-show-errors
fi

# --- deploy -----------------------------------------------------------------------

( umask 077 && : > "${PARAMS_FILE}" )
chmod 600 "${PARAMS_FILE}"

DATABASE_URL_VALUE="$(aws secretsmanager get-secret-value --secret-id "${SECRET_ARN}" \
  --query SecretString --output text)" \
  || die "could not read the database URL from the backend's secret"

PROJECT_NAME="${PROJECT_NAME}" VPC_ID="${VPC_ID}" SUBNETS="${SUBNET_IDS}" \
ROUTE_TABLES="${ROUTE_TABLES}" FUNCTION_SG="${FUNCTION_SG}" IMAGE_URI="${IMAGE_URI}" \
ARCHITECTURE="${ARCHITECTURE}" DATABASE_URL_VALUE="${DATABASE_URL_VALUE}" \
REPORT_TIMEZONE="${REPORT_TIMEZONE:-Europe/Kyiv}" ARTIFACT_BUCKET="${ARTIFACT_BUCKET}" \
MAILER_KEY="${MAILER_KEY}" SCHEDULE_STATE="${REPORT_SCHEDULE_STATE:-ENABLED}" \
python3 - "${PARAMS_FILE}" <<'PY'
import json, os, sys

params = {
    "ProjectName": os.environ["PROJECT_NAME"],
    "VpcId": os.environ["VPC_ID"],
    "SubnetIds": os.environ["SUBNETS"],
    "RouteTableIds": os.environ["ROUTE_TABLES"],
    "FunctionSecurityGroupId": os.environ["FUNCTION_SG"],
    "ImageUri": os.environ["IMAGE_URI"],
    "Architecture": os.environ["ARCHITECTURE"],
    "DatabaseUrl": os.environ["DATABASE_URL_VALUE"].strip(),
    "ReportTimezone": os.environ["REPORT_TIMEZONE"],
    "ReportSender": os.environ["REPORT_SENDER"].strip(),
    "ReportRecipients": ",".join(
        a.strip() for a in os.environ["REPORT_RECIPIENTS"].split(",") if a.strip()
    ),
    "ArtifactBucket": os.environ["ARTIFACT_BUCKET"],
    "MailerCodeKey": os.environ["MAILER_KEY"],
    "ScheduleState": os.environ["SCHEDULE_STATE"],
}
with open(sys.argv[1], "w") as fh:
    json.dump([{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()], fh)
PY
unset DATABASE_URL_VALUE

if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" >/dev/null 2>&1; then
  log "first deploy - creating ${STACK_NAME}"
else
  log "updating ${STACK_NAME}"
fi

if ! aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file infra/reports.yml \
  --parameter-overrides "file://${PARAMS_FILE}" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --tags "PROJECT_NAME=${PROJECT_NAME}"; then
  warn "deploy failed - most recent failure reasons:"
  aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" \
    --max-items 40 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`||ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table >&2 || true
  exit 1
fi
rm -f "${PARAMS_FILE}"

outputs() {
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo
echo "  builder    $(outputs BuilderFunctionName)   (logs: $(outputs BuilderLogGroupName))"
echo "  mailer     $(outputs MailerFunctionName)   (logs: $(outputs MailerLogGroupName))"
echo "  schedule   $(outputs ScheduleName)   Monday 07:00 Europe/Kyiv -> SQS"
echo
echo "Next: make report-now WEEK=2026-W39, then make reports-ls and make reports-logs."
