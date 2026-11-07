#!/usr/bin/env bash
# Lab 8, Part 2: the isolated AWS resources. Nothing here touches Peach.
#
#   setup_part2.sh bucket     private, encrypted bucket for all Lab 8 data
#   setup_part2.sh copy       server-side copy of the whole payments/ prefix
#   setup_part2.sh workgroup  Athena workgroup with a results location and a scan limit
#   setup_part2.sh crawler    Glue role and crawler on the raw data, then run it
#   setup_part2.sh schema     print the schema the crawler inferred
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION=us-east-1
BUCKET="${LAB8_BUCKET:-ooad-lab8-melasya}"
SOURCE=s3://ucu-ooad-lab8-payments/payments/
ROLE=ooad-lab8-glue-crawler
CRAWLER=ooad-lab8-raw-payments
CRAWLER_DB=lab8_crawled
TAGS='Project=ooad-lab8'

case "${1:-}" in
  bucket)
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
    aws s3api put-public-access-block --bucket "${BUCKET}" --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    aws s3api put-bucket-encryption --bucket "${BUCKET}" --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    aws s3api put-bucket-tagging --bucket "${BUCKET}" --tagging 'TagSet=[{Key=Project,Value=ooad-lab8}]'
    ;;
  copy)
    # --copy-props none: the source bucket does not let us read object tags, and we need none.
    aws s3 sync "${SOURCE}" "s3://${BUCKET}/payments/" --copy-props none --only-show-errors
    echo "source:" && aws s3 ls "${SOURCE}" --recursive --summarize --no-sign-request | tail -2
    echo "copy:" && aws s3 ls "s3://${BUCKET}/payments/" --recursive --summarize | tail -2
    ;;
  workgroup)
    # 10 GiB per query is $0.05: a runaway query stops there. Not enforced, because a CTAS with
    # external_location (curated/) is refused by a workgroup that enforces its output location.
    aws athena create-work-group --name lab8 --tags "Key=Project,Value=ooad-lab8" --configuration \
      "ResultConfiguration={OutputLocation=s3://${BUCKET}/athena-results/},EnforceWorkGroupConfiguration=false,PublishCloudWatchMetricsEnabled=false,BytesScannedCutoffPerQuery=10737418240,EngineVersion={SelectedEngineVersion=Athena engine version 3}"
    ;;
  crawler)
    aws iam create-role --role-name "${ROLE}" --tags "Key=Project,Value=ooad-lab8" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"glue.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    aws iam attach-role-policy --role-name "${ROLE}" --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole
    aws iam put-role-policy --role-name "${ROLE}" --policy-name read-lab8-payments --policy-document \
      "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],\"Resource\":[\"arn:aws:s3:::${BUCKET}\",\"arn:aws:s3:::${BUCKET}/payments/*\"]}]}"
    sleep 15 # a new IAM role takes a moment to be usable
    aws glue create-database --database-input "Name=${CRAWLER_DB}"
    aws glue create-crawler --name "${CRAWLER}" --role "${ROLE}" --database-name "${CRAWLER_DB}" \
      --targets "S3Targets=[{Path=s3://${BUCKET}/payments/}]" --tags "${TAGS}"
    aws glue start-crawler --name "${CRAWLER}"
    until [[ "$(aws glue get-crawler --name "${CRAWLER}" --query Crawler.State --output text)" == "READY" ]]; do sleep 15; done
    aws glue get-crawler-metrics --crawler-name-list "${CRAWLER}" --query 'CrawlerMetricsList[0]'
    aws glue get-crawler --name "${CRAWLER}" --query 'Crawler.LastCrawl.{Status:Status,Start:StartTime}'
    ;;
  schema)
    aws glue get-tables --database-name "${CRAWLER_DB}" --query 'TableList[].{Table:Name,Columns:StorageDescriptor.Columns[].[Name,Type],Partitions:PartitionKeys[].[Name,Type],SerDe:StorageDescriptor.SerdeInfo.SerializationLibrary,Classification:Parameters.classification}'
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 2
    ;;
esac
