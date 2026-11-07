-- One row per invoice: its latest state.
-- Step 1 removes exact duplicates: GROUP BY every payload column. dt is not part of the payload, so
--        a copy delivered into another partition is still a duplicate; min(dt) keeps the first one.
-- Step 2 keeps the latest event per invoice by modifiedDate (not receivedAt, which a late arrival
--        would distort). Ties on modifiedDate are broken deterministically: the further status in
--        the lifecycle wins, then receivedAt, then the status text.
CREATE OR REPLACE VIEW lab8.payments AS
WITH distinct_events AS (
  SELECT invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
         modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device,
         min(dt) AS dt
  FROM lab8.payment_events
  GROUP BY invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
           modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device
),
ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY invoiceId
           ORDER BY from_iso8601_timestamp(modifiedDate) DESC,
                    CASE status WHEN 'success' THEN 3 WHEN 'failure' THEN 3 WHEN 'processing' THEN 2 ELSE 1 END DESC,
                    receivedAt DESC,
                    status DESC
         ) AS rn
  FROM distinct_events
)
SELECT invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
       modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device, dt
FROM ranked
WHERE rn = 1;
