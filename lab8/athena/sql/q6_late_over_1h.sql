-- Events more than one hour late, by status and by how late they were.
WITH events AS (
  SELECT DISTINCT invoiceId, status, modifiedDate, receivedAt, dt,
         date_diff('second', from_iso8601_timestamp(modifiedDate), from_iso8601_timestamp(receivedAt)) AS late_seconds
  FROM lab8.payment_events
)
SELECT status,
       count(*)                          AS events_over_1h_late,
       min(late_seconds)                 AS min_late_seconds,
       cast(avg(late_seconds) AS bigint) AS avg_late_seconds,
       max(late_seconds)                 AS max_late_seconds,
       count_if(substr(modifiedDate, 1, 10) <> dt) AS stored_in_another_days_partition
FROM events
WHERE late_seconds > 3600
GROUP BY status
ORDER BY events_over_1h_late DESC;
