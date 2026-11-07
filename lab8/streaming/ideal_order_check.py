"""The reference for Experiment D: the same window code, no Kafka, events in strict receivedAt order.

A real consumer reads partitions slightly out of step, so it can only approach these numbers.
"""

import hashlib
import json

from windowing import WindowProcessor, parse_time

with open("data/events.jsonl", encoding="utf-8") as file:
    events = sorted((json.loads(line) for line in file), key=lambda e: e["receivedAt"])
lateness = [parse_time(e["receivedAt"]) - parse_time(e["modifiedDate"]) for e in events]
print(f"{len(events)} events; late by more than: " + ", ".join(f"{s}s={sum(x > s for x in lateness)}" for s in (0, 60, 120, 300, 600)))

for allowed in (0, 120, 600):
    processor = WindowProcessor(allowed_lateness=allowed)
    processor.assign({0, 1, 2})
    for event in events:
        processor.process(hashlib.md5(event["invoiceId"].encode()).digest()[0] % 3, event)
    stats = processor.stats
    print(f"allowed lateness {allowed:>3}s: accepted={stats.accepted} late_accepted={stats.late_accepted} late_rejected={stats.late_rejected} duplicates={stats.duplicates}")
