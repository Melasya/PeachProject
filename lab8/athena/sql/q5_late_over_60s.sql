-- Late arrivals: receivedAt minus modifiedDate, over distinct events (a duplicate is not new lateness).
WITH events AS (
  SELECT DISTINCT invoiceId, status, modifiedDate, receivedAt,
         date_diff('second', from_iso8601_timestamp(modifiedDate), from_iso8601_timestamp(receivedAt)) AS late_seconds
  FROM lab8.payment_events
)
SELECT count(*)                      AS distinct_events,
       count_if(late_seconds > 60)   AS events_over_60s_late,
       round(100.0 * count_if(late_seconds > 60) / count(*), 3) AS pct_over_60s,
       count_if(late_seconds > 3600) AS of_which_over_1h_late,
       max(late_seconds)             AS max_late_seconds,
       count_if(late_seconds < 0)    AS received_before_modified
FROM events;
