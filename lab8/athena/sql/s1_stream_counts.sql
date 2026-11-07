-- What arrived through SQS -> Lambda -> S3. rows - distinct_events = copies delivered more than once
-- or duplicates already in the source sample.
SELECT count(*)                                                    AS rows_in_s3,
       count(DISTINCT invoiceid || status || modifieddate)         AS distinct_events,
       count(DISTINCT invoiceid)                                   AS invoices,
       count(DISTINCT "$path")                                     AS s3_objects,
       min(receivedat)                                             AS first_received_at,
       max(receivedat)                                             AS last_received_at
FROM lab8_sqs.stream_events
WHERE dt >= '2026-11-01';
