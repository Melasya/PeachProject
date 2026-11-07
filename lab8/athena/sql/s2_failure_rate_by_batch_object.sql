-- Failure rate per delivered S3 object, in replay order: the second sample was sent with 30% forced failures.
WITH events AS (
  SELECT DISTINCT "$path" AS s3_object, invoiceid, status, modifieddate, receivedat
  FROM lab8_sqs.stream_events
  WHERE dt >= '2026-11-01'
)
SELECT regexp_extract(s3_object, '[^/]+$') AS s3_object,
       count(*) AS events,
       min(receivedat) AS first_received_at,
       count_if(status = 'success') AS successful,
       count_if(status = 'failure') AS failed,
       round(100.0 * count_if(status = 'failure') / nullif(count_if(status IN ('success', 'failure')), 0), 1) AS failure_rate_pct
FROM events
GROUP BY s3_object
ORDER BY first_received_at;
