#!/usr/bin/env bash
# Deploy the Lab 8 SQS alternative: zip the function, upload it to the Lab 8 bucket, deploy the stack.
# Touches only ooad-lab8-* resources. Tear down with: aws cloudformation delete-stack --stack-name ooad-lab8-sqs
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION=us-east-1
BUCKET="${LAB8_BUCKET:-ooad-lab8-melasya}"
STACK=ooad-lab8-sqs
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -m "${HERE}")"
cd "${HERE}"

KEY="$(python - <<'EOF'
import hashlib, zipfile
info = zipfile.ZipInfo("handler.py", date_time=(2020, 1, 1, 0, 0, 0))
info.external_attr = 0o644 << 16
source = open("lambda/handler.py", "rb").read()
with zipfile.ZipFile("lambda/function.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(info, source)
print("lambda/sqs-to-s3-" + hashlib.sha256(source).hexdigest()[:16] + ".zip")
EOF
)"
aws s3 cp lambda/function.zip "s3://${BUCKET}/${KEY}" --only-show-errors
rm -f lambda/function.zip

aws cloudformation deploy --region "${REGION}" --stack-name "${STACK}" --template-file stack.yml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --tags Project=ooad-lab8 --parameter-overrides "DataBucket=${BUCKET}" "CodeKey=${KEY}"

aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK}" \
  --query 'Stacks[0].[StackStatus, Outputs[].[OutputKey, OutputValue]]' --output json
