#!/usr/bin/env bash
# Build the Next.js static export and put it behind CloudFront.
#
# The API URL is compiled into the bundle - NEXT_PUBLIC_* is substituted at
# build time, not read at runtime - so this builds against BACKEND_URL from
# .env, which scripts/deploy-backend.sh writes. Deploy the backend first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${ROOT}/infra/frontend.yaml"
APP="${ROOT}/frontend"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -f "${ROOT}/.env" ]]; then
  # Variables already exported win over .env: `AWS_REGION=eu-central-1 make x`
  # must not be quietly reset to the region .env names.
  preset="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
  eval "${preset}"
fi

# A blank AWS_PROFILE is read as a profile literally named "", and blank keys
# short-circuit the credential chain. Treat empty as absent.
for var in AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  [[ -n "${!var:-}" ]] || unset "${var}"
done

PROJECT_NAME="${PROJECT_NAME:-peach}"
STACK_NAME="${FRONTEND_STACK_NAME:-${PROJECT_NAME}-frontend}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

# --- preflight --------------------------------------------------------------

for tool in aws node; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required but not installed"
done
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "no usable AWS credentials - set AWS_PROFILE or the AWS_* keys in .env"

# package.json pins pnpm, which may or may not be on PATH.
if command -v pnpm >/dev/null 2>&1; then
  PM=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  PM=(corepack pnpm)
else
  PM=(npx --yes pnpm@10)
fi

# --- which API does this build talk to? -------------------------------------

# NEXT_PUBLIC_API_URL in .env points at localhost for Compose; it is not what a
# deployed bundle should be compiled against. BACKEND_URL is.
API_URL="${BACKEND_URL:-}"
API_URL="${API_URL%/}"
[[ -n "${API_URL}" ]] || die "BACKEND_URL is not set in .env - run make deploy-backend first"

log "building against ${API_URL}"

# The function URL is always HTTPS; plain HTTP here means a hand-edited .env.
[[ "${API_URL}" == https://* ]] \
  || die "BACKEND_URL must be https:// - browsers block an HTTPS page calling HTTP"

# --- infrastructure ---------------------------------------------------------

if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" >/dev/null 2>&1; then
  log "first deploy - creating ${STACK_NAME} (CloudFront takes a few minutes)"
else
  log "updating ${STACK_NAME}"
fi

if ! aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE}" \
  --parameter-overrides \
    "ProjectName=${PROJECT_NAME}" \
  --no-fail-on-empty-changeset \
  --tags "PROJECT_NAME=${PROJECT_NAME}"; then
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

BUCKET="$(outputs BucketName)"
DISTRIBUTION_ID="$(outputs DistributionId)"
SITE_URL="$(outputs SiteUrl)"

# --- build ------------------------------------------------------------------

log "installing dependencies"
(cd "${APP}" && "${PM[@]}" install --frozen-lockfile)

log "building the static export"
rm -rf "${APP}/out"
(cd "${APP}" && NEXT_OUTPUT=export NEXT_PUBLIC_API_URL="${API_URL}" "${PM[@]}" build)
[[ -f "${APP}/out/index.html" ]] || die "the export produced no out/index.html"

# --- upload -----------------------------------------------------------------

# Hashed assets first and without --delete: a client mid-navigation may still
# be asking for the previous build's chunks. They are immutable, so the edge
# and the browser may keep them forever.
log "uploading to s3://${BUCKET}"
aws s3 sync "${APP}/out/_next/static" "s3://${BUCKET}/_next/static" \
  --cache-control "public,max-age=31536000,immutable" \
  --only-show-errors

# Then everything else, which must never be cached hard or a deploy would not
# be visible until the TTL expired.
aws s3 sync "${APP}/out" "s3://${BUCKET}" \
  --delete \
  --exclude "_next/static/*" \
  --cache-control "public,max-age=0,must-revalidate" \
  --only-show-errors

log "invalidating the CloudFront cache"
INVALIDATION_ID="$(aws cloudfront create-invalidation \
  --distribution-id "${DISTRIBUTION_ID}" \
  --paths "/*" \
  --query Invalidation.Id --output text)"
aws cloudfront wait invalidation-completed \
  --distribution-id "${DISTRIBUTION_ID}" \
  --id "${INVALIDATION_ID}" 2>/dev/null || true

# --- report -----------------------------------------------------------------

echo
echo "  site       ${SITE_URL}"
echo "  items      ${SITE_URL}/items"
echo "  api        ${API_URL}"
echo "  bucket     s3://${BUCKET}"
echo

echo "Now allow the site's origin through CORS:"
echo
echo "  API_CORS_ORIGINS=${SITE_URL}   in .env, then: make deploy-backend"
echo
