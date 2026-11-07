-- The deduplicated dataset as Parquet, partitioned by dt, under curated/. Athena allows 100
-- partitions per CTAS; October has 31 days. dt must be the last column.
CREATE TABLE lab8.payments_curated
WITH (
  format = 'PARQUET',
  write_compression = 'SNAPPY',
  external_location = 's3://{bucket}/curated/',
  partitioned_by = ARRAY['dt']
) AS
SELECT invoiceId, status, failureReason, amount, finalAmount, ccy, reference, createdDate,
       modifiedDate, receivedAt, merchantId, merchantName, mcc, customerId, paymentMethod, device, dt
FROM lab8.payments;
