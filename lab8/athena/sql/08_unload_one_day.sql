-- One day of raw events (duplicates included), gzipped, for the local streaming part. Only because
-- this machine's connection to S3 is slow: the same rows as dt=2026-10-15/events.jsonl, one tenth the bytes.
UNLOAD (SELECT * FROM lab8.payment_events WHERE dt = '2026-10-15')
TO 's3://{bucket}/one-day-gz/'
WITH (format = 'JSON', compression = 'GZIP');
