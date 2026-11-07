-- Failure rate of finished payments. Invoices still in created/processing have no outcome yet, so
-- they are counted but left out of the rate.
SELECT paymentMethod,
       device,
       count_if(status = 'success') AS successful,
       count_if(status = 'failure') AS failed,
       count_if(status NOT IN ('success', 'failure')) AS unfinished,
       round(100.0 * count_if(status = 'failure')
             / nullif(count_if(status IN ('success', 'failure')), 0), 2) AS failure_rate_pct
FROM {table}
GROUP BY paymentMethod, device
ORDER BY failure_rate_pct DESC, paymentMethod, device;
