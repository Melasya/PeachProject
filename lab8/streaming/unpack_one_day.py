"""Turn Athena's gzipped UNLOAD of one day back into data/events.jsonl with the original field names.

Athena lower-cases column names and drops the partition value's meaning, so this restores the
camelCase keys the rest of the code (and the raw dataset) uses. It prints the row count to compare
with the partition's count in Athena.
"""

import glob
import gzip
import json

NAMES = {
    name.lower(): name
    for name in ["invoiceId", "status", "failureReason", "amount", "finalAmount", "ccy", "reference", "createdDate", "modifiedDate", "receivedAt", "merchantId", "merchantName", "mcc", "customerId", "paymentMethod", "device"]
}

rows = 0
with open("data/events.jsonl", "w", encoding="utf-8") as out:
    for path in sorted(glob.glob("data/one-day-gz/*.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as file:
            for line in file:
                event = {NAMES[key]: value for key, value in json.loads(line).items() if key in NAMES}
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
                rows += 1
print(f"{rows} events written to data/events.jsonl")
