-- Facts the other queries rely on: which currencies and statuses exist, and how finalAmount relates to amount.
SELECT status, ccy, count(*) AS events,
       count_if(finalAmount = amount) AS final_equals_amount,
       count_if(finalAmount = 0) AS final_is_zero,
       count_if(failureReason IS NOT NULL) AS has_failure_reason,
       count_if(amount IS NULL) AS amount_null
FROM lab8.payment_events
GROUP BY status, ccy
ORDER BY status, ccy;
