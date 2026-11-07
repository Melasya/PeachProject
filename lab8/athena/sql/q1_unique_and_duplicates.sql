-- Unique invoices and duplicates. A row with copies = 3 is one event delivered three times.
WITH grouped AS (
  SELECT invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
         modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device,
         count(*) AS copies
  FROM lab8.payment_events
  GROUP BY invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
           modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device
)
SELECT sum(copies)               AS total_events,
       count(*)                  AS distinct_events,
       sum(copies) - count(*)    AS exact_duplicate_rows,
       count_if(copies > 1)      AS events_delivered_more_than_once,
       max(copies)               AS max_copies_of_one_event,
       count(DISTINCT invoiceId) AS unique_invoices,
       round(100.0 * (sum(copies) - count(*)) / sum(copies), 3) AS duplicate_pct
FROM grouped;
