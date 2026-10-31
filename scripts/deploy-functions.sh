#!/usr/bin/env bash
# Ship new application code to the functions Terraform manages (infra/,
# ADR 0008): the agenda parser and the report builder get the image the backend
# runs now; the report mailer gets a fresh zip of backend/app/reports/mailer.py.
#
# Code only. Their configuration - memory, environment, roles, triggers - is
# Terraform's, which ignores the image and the zip so the two never fight.
# Run it after make deploy-backend when the parser, the builder or the mailer
# changed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Git Bash: aws.exe cannot open /c/... paths; C:/... works for it and for bash.
command -v cygpath >/dev/null 2>&1 && ROOT="$(cygpath -m "${ROOT}")"
cd "${ROOT}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -f .env ]]; then
  # Variables already exported win over .env.
  preset="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  eval "${preset}"
fi
for var in AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  [[ -n "${!var:-}" ]] || unset "${var}"
done

PROJECT_NAME="${PROJECT_NAME:-peach}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

command -v aws >/dev/null 2>&1 || die "aws cli is required"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
  || die "no usable AWS credentials"

# --- images ------------------------------------------------------------------

IMAGE_URI="$(aws lambda get-function --function-name "${PROJECT_NAME}-backend" \
  --query Code.ImageUri --output text)" || die "no ${PROJECT_NAME}-backend function - run make deploy-backend first"

for fn in "${PROJECT_NAME}-agenda-parser" "${PROJECT_NAME}-reports-builder"; do
  log "${fn} -> ${IMAGE_URI##*/}"
  aws lambda update-function-code --function-name "${fn}" --image-uri "${IMAGE_URI}" >/dev/null
  aws lambda wait function-updated-v2 --function-name "${fn}"
done

# --- mailer zip --------------------------------------------------------------

ARTIFACT_BUCKET="${PROJECT_NAME}-artifacts-${ACCOUNT_ID}-${AWS_REGION}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# A fixed timestamp makes the zip - and so its key - depend only on the code.
MAILER_HASH="$(SOURCE="backend/app/reports/mailer.py" OUT="${WORK_DIR}/mailer.zip" python3 -c '
import hashlib, os, zipfile
info = zipfile.ZipInfo("mailer.py", date_time=(2020, 1, 1, 0, 0, 0))
info.external_attr = 0o644 << 16
with zipfile.ZipFile(os.environ["OUT"], "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr(info, open(os.environ["SOURCE"], "rb").read())
print(hashlib.sha256(open(os.environ["OUT"], "rb").read()).hexdigest()[:16])')"
MAILER_KEY="mailer/mailer-${MAILER_HASH}.zip"

if ! aws s3api head-object --bucket "${ARTIFACT_BUCKET}" --key "${MAILER_KEY}" >/dev/null 2>&1; then
  aws s3 cp "${WORK_DIR}/mailer.zip" "s3://${ARTIFACT_BUCKET}/${MAILER_KEY}" --only-show-errors
fi
log "${PROJECT_NAME}-reports-mailer -> ${MAILER_KEY##*/}"
aws lambda update-function-code --function-name "${PROJECT_NAME}-reports-mailer" \
  --s3-bucket "${ARTIFACT_BUCKET}" --s3-key "${MAILER_KEY}" >/dev/null
aws lambda wait function-updated-v2 --function-name "${PROJECT_NAME}-reports-mailer"
