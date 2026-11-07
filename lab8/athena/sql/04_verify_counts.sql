-- The actual event count, per partition; the row dt = 'ALL' is the whole table.
SELECT coalesce(dt, 'ALL') AS dt, count(*) AS events
FROM lab8.payment_events
GROUP BY ROLLUP (dt)
ORDER BY dt;
