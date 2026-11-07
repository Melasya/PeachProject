-- Turnover: successful payments only, in major units (minor units / 100). Never summed across
-- currencies. q7_data_profile lists the currencies present, to confirm /100 applies to all of them.
SELECT merchantId,
       merchantName,
       ccy,
       CASE ccy WHEN 980 THEN 'UAH' WHEN 840 THEN 'USD' WHEN 978 THEN 'EUR' WHEN 985 THEN 'PLN' WHEN 826 THEN 'GBP'
                ELSE cast(ccy AS varchar) END AS currency,
       count(*) AS successful_payments,
       cast(sum(finalAmount) AS decimal(20, 2)) / 100 AS turnover_major_units
FROM {table}
WHERE status = 'success'
GROUP BY merchantId, merchantName, ccy
ORDER BY turnover_major_units DESC;
