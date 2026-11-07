-- The Part 3 question over the stream's data: failure rate by payment method, deduplicated.
WITH events AS (
  SELECT DISTINCT invoiceid, status, modifieddate, paymentmethod
  FROM lab8_sqs.stream_events
  WHERE dt >= '2026-11-01'
)
SELECT paymentmethod,
       count_if(status = 'success') AS successful,
       count_if(status = 'failure') AS failed,
       round(100.0 * count_if(status = 'failure') / nullif(count_if(status IN ('success', 'failure')), 0), 2) AS failure_rate_pct
FROM events
GROUP BY paymentmethod
ORDER BY failure_rate_pct DESC;
