-- The curated table must hold exactly one row per invoice.
SELECT count(*) AS rows_in_curated, count(DISTINCT invoiceId) AS unique_invoices FROM lab8.payments_curated;
