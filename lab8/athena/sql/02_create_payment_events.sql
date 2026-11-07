-- The raw JSONL events. Amounts are integers in minor units (kopiykas, cents); ccy is the numeric
-- ISO 4217 code. Dates stay strings: they are ISO 8601 with a 'Z', which the Hive TIMESTAMP type
-- cannot parse, so queries convert them with from_iso8601_timestamp().
CREATE EXTERNAL TABLE IF NOT EXISTS lab8.payment_events (
  invoiceId     string,
  status        string,
  failureReason string,
  amount        bigint,
  finalAmount   bigint,
  ccy           int,
  reference     string,
  createdDate   string,
  modifiedDate  string,
  receivedAt    string,
  merchantId    string,
  merchantName  string,
  mcc           int,
  customerId    string,
  paymentMethod string,
  device        string
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://{bucket}/payments/';
