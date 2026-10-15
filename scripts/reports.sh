#!/usr/bin/env bash
# Operate and inspect the weekly-report stack (infra/reports.yml).
#
#   reports.sh now [WEEK]          queue a report request on SQS - the normal path.
#                                  No WEEK: the builder picks the last complete week.
#   reports.sh ls                  list reports/ in the bucket
#   reports.sh get WEEK            download reports/WEEK.csv into .reports/
#   reports.sh logs [builder|mailer] [SINCE]   tail a function's logs (default 1h)
#   reports.sh evidence [SINCE]    trigger lines (schedule / sqs / s3) from both logs
#   reports.sh queues              messages waiting / in flight, main queue and DLQ
#
# Test helpers - explicit, never run by anything else:
#   reports.sh invoke-direct [WEEK]   invoke the builder directly with a
#                                     schedule-shaped event (intermediate test only)
#   reports.sh schedule-once          one-time schedule -> SQS in ~2 minutes, same
#                                     target, role and input as the weekly one
#   reports.sh poison                 queue a request that always fails (retries -> DLQ)
#   reports.sh dlq                    show messages in the DLQ (without deleting them)
#   reports.sh dlq-purge --yes        delete everything in the DLQ
#   reports.sh seed WEEK --yes        replace the demo meetings of WEEK and the week
#                                     before in the PRODUCTION database (through the
#                                     API function's direct-invoke action, inside the
#                                     VPC). Only rows titled "[demo] ..." change.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Git Bash: aws.exe cannot open /c/... paths; C:/... works for it and for bash.
command -v cygpath >/dev/null 2>&1 && ROOT="$(cygpath -m "${ROOT}")"
cd "${ROOT}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -f .env ]]; then
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
STACK_NAME="${REPORTS_STACK_NAME:-${PROJECT_NAME}-reports}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

command -v aws >/dev/null 2>&1 || die "aws cli is required"

output() {
  local value
  value="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null)" \
    || die "stack ${STACK_NAME} not found - run make deploy-reports first"
  [[ -n "${value}" && "${value}" != "None" ]] || die "no $1 output on ${STACK_NAME}"
  printf '%s' "${value}"
}

check_week() {
  [[ "$1" =~ ^[0-9]{4}-W[0-9]{2}$ ]] || die "WEEK must look like 2026-W39, got '$1'"
}

# Log group names start with "/", which Git Bash would rewrite into a Windows
# path before aws.exe sees it.
aws_nopath() { MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' aws "$@"; }

COMMAND="${1:-}"
shift || true

case "${COMMAND}" in
  now)
    WEEK="${1:-${WEEK:-}}"
    if [[ -n "${WEEK}" ]]; then
      check_week "${WEEK}"
      BODY="{\"week\":\"${WEEK}\",\"source\":\"manual\"}"
    else
      BODY='{"week":null,"source":"manual"}'
    fi
    ID="$(aws sqs send-message --queue-url "$(output QueueUrl)" --message-body "${BODY}" \
      --query MessageId --output text)"
    log "queued ${BODY} (message ${ID})"
    echo "The builder picks it up within seconds; then: make reports-ls, make reports-logs"
    ;;

  ls)
    aws s3 ls "s3://$(output BucketName)/reports/"
    ;;

  get)
    WEEK="${1:-}"
    check_week "${WEEK}"
    mkdir -p .reports
    aws s3 cp "s3://$(output BucketName)/reports/${WEEK}.csv" ".reports/${WEEK}.csv" --only-show-errors
    log "saved .reports/${WEEK}.csv"
    cat ".reports/${WEEK}.csv"
    ;;

  logs)
    WHICH="${1:-builder}"
    SINCE="${2:-1h}"
    case "${WHICH}" in
      builder) GROUP="$(output BuilderLogGroupName)" ;;
      mailer) GROUP="$(output MailerLogGroupName)" ;;
      *) die "logs builder|mailer [SINCE]" ;;
    esac
    aws_nopath logs tail "${GROUP}" --since "${SINCE}" --format short
    ;;

  evidence)
    SINCE="${1:-24h}"
    for GROUP in "$(output BuilderLogGroupName)" "$(output MailerLogGroupName)"; do
      echo "--- ${GROUP}"
      aws_nopath logs tail "${GROUP}" --since "${SINCE}" --format short \
        | grep -E '"event": "(build_started|report_uploaded|mail_started|mail_sent|skipped)"' || true
    done
    ;;

  queues)
    for NAME in QueueUrl DeadLetterQueueUrl; do
      URL="$(output "${NAME}")"
      printf '%-20s ' "${URL##*/}"
      aws sqs get-queue-attributes --queue-url "${URL}" \
        --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
        --query 'Attributes.[ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible]' \
        --output text | awk '{print "waiting=" $1 "  in-flight=" $2}'
    done
    ;;

  invoke-direct)
    WEEK="${1:-}"
    if [[ -n "${WEEK}" ]]; then
      check_week "${WEEK}"
      PAYLOAD="{\"source\":\"schedule\",\"week\":\"${WEEK}\"}"
    else
      PAYLOAD='{"source":"schedule"}'
    fi
    OUT="$(mktemp)"
    command -v cygpath >/dev/null 2>&1 && OUT="$(cygpath -m "${OUT}")"
    trap 'rm -f "${OUT}"' EXIT
    log "direct invoke of the builder with ${PAYLOAD} (intermediate test only)"
    ERROR="$(aws lambda invoke --function-name "$(output BuilderFunctionName)" \
      --cli-binary-format raw-in-base64-out --payload "${PAYLOAD}" \
      --cli-read-timeout 120 --query FunctionError --output text "${OUT}")"
    cat "${OUT}"
    echo
    [[ -z "${ERROR}" || "${ERROR}" == "None" ]] || die "the builder failed: ${ERROR}"
    ;;

  schedule-once)
    command -v python3 >/dev/null 2>&1 || die "python3 is required"
    AT="$(python3 -c 'from datetime import datetime, timedelta, UTC; print((datetime.now(UTC) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S"))')"
    NAME="${PROJECT_NAME}-weekly-report-once-$(date -u +%Y%m%d%H%M%S)"
    TARGET="$(QUEUE_ARN="$(output QueueArn)" ROLE_ARN="$(output SchedulerRoleArn)" python3 -c '
import json, os
print(json.dumps({"Arn": os.environ["QUEUE_ARN"], "RoleArn": os.environ["ROLE_ARN"],
                  "Input": json.dumps({"week": None, "source": "schedule"}, separators=(",", ":"))}))')"
    aws scheduler create-schedule --name "${NAME}" \
      --schedule-expression "at(${AT})" --schedule-expression-timezone UTC \
      --flexible-time-window Mode=OFF --action-after-completion DELETE \
      --target "${TARGET}" >/dev/null
    log "one-time schedule ${NAME} fires at ${AT} UTC -> SQS, then deletes itself"
    ;;

  poison)
    ID="$(aws sqs send-message --queue-url "$(output QueueUrl)" \
      --message-body '{"week":"not-a-week","source":"dlq-test"}' \
      --query MessageId --output text)"
    log "queued a failing request (message ${ID}): 3 receives, 360 s apart, then the DLQ"
    ;;

  dlq)
    aws sqs receive-message --queue-url "$(output DeadLetterQueueUrl)" \
      --max-number-of-messages 10 --visibility-timeout 0 \
      --attribute-names ApproximateReceiveCount SentTimestamp \
      --query 'Messages[].[MessageId,Body,Attributes.ApproximateReceiveCount]' --output table
    ;;

  dlq-purge)
    [[ "${1:-}" == "--yes" ]] || die "this deletes every message in the DLQ - add --yes"
    aws sqs purge-queue --queue-url "$(output DeadLetterQueueUrl)"
    log "DLQ purged"
    ;;

  seed)
    WEEK="${1:-}"
    check_week "${WEEK}"
    [[ "${2:-}" == "--yes" ]] \
      || die "this writes demo meetings for ${WEEK} and the week before to PRODUCTION - add --yes"
    FUNCTION="$(aws cloudformation describe-stacks \
      --stack-name "${BACKEND_STACK_NAME:-${PROJECT_NAME}-backend}" \
      --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" --output text)"
    [[ -n "${FUNCTION}" && "${FUNCTION}" != "None" ]] || die "no backend function found"
    OUT="$(mktemp)"
    command -v cygpath >/dev/null 2>&1 && OUT="$(cygpath -m "${OUT}")"
    trap 'rm -f "${OUT}"' EXIT
    log "seeding demo meetings for ${WEEK} and the week before (direct invoke of the API function)"
    ERROR="$(aws lambda invoke --function-name "${FUNCTION}" \
      --cli-binary-format raw-in-base64-out \
      --payload "{\"action\":\"seed_demo_meetings\",\"week\":\"${WEEK}\",\"confirm\":true}" \
      --cli-read-timeout 120 --query FunctionError --output text "${OUT}")"
    # The result is {"status","action","weeks","seeded"} - no connection details.
    cat "${OUT}"
    echo
    [[ -z "${ERROR}" || "${ERROR}" == "None" ]] || die "the seed action failed: ${ERROR}"
    ;;

  *)
    sed -n '2,27p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
