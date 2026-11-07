-- Peak payment hours: when invoices are created, in Kyiv local time, over the whole month.
SELECT hour(at_timezone(from_iso8601_timestamp(createdDate), 'Europe/Kyiv')) AS kyiv_hour,
       count(*) AS invoices,
       count_if(status = 'success') AS successful
FROM {table}
GROUP BY 1
ORDER BY invoices DESC;
