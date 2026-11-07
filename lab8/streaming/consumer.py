"""Read payment events and report the failure rate per payment method, per event-time minute.

    python consumer.py --group payments-monitor --allowed-lateness 120

Delivery is at-least-once. The offset is committed only after the state (open windows, seen
events, watermark inputs) has been saved to --state-file. A restart loads that state and resumes
from the committed offset. If it stopped between the two steps, the re-read events are recognised
as duplicates, so no window counts anything twice.

The windowing logic itself is in windowing.py.
"""

import argparse
import json
import os
import signal
import time

from kafka import KafkaConsumer
from kafka.consumer.subscription_state import ConsumerRebalanceListener
from kafka.errors import CommitFailedError

from windowing import WindowProcessor, WindowResult, format_time

ALERT_THRESHOLD = 0.10
CHECKPOINT_EVERY = 500  # messages
# Read little from each partition at a time, so their clocks advance together. The client drains one
# partition's fetch before moving on; with the default 1 MB that is about 2,500 events, the partitions
# drift minutes apart, and during a catch-up the lowest clock lags, which acts like far more lateness
# than was asked for. The broker always returns at least one record batch, however small this is.
MAX_FETCH_BYTES = 1024


class ShowAssignment(ConsumerRebalanceListener):
    """Prints partition assignment changes: the evidence for Experiment B."""

    def __init__(self, name: str, processor: WindowProcessor) -> None:
        self.name = name
        self.processor = processor
        self.consumer = None  # set once the consumer exists

    def on_partitions_revoked(self, revoked) -> None:
        print(f"[{self.name}] revoked:  {sorted(tp.partition for tp in revoked)}", flush=True)
        if revoked and self.consumer:
            self.consumer.commit()  # the next owner continues from here, not from the last checkpoint

    def on_partitions_assigned(self, assigned) -> None:
        print(f"[{self.name}] assigned: {sorted(tp.partition for tp in assigned)}", flush=True)
        self.processor.assign({tp.partition for tp in assigned})


def report(result: WindowResult, totals: dict, windows_out: list | None) -> None:
    summary = []
    for method in sorted(result.methods):
        counts = result.methods[method]
        rate = result.failure_rate(method)
        summary.append(f"{method}={rate:.1%} ({counts['failure']}/{counts['failure'] + counts['success']})")
        totals["success"] += counts["success"]
        totals["failure"] += counts["failure"]
        totals["method_windows"] += 1
        if rate > ALERT_THRESHOLD:
            totals["alerts"] += 1
            print(f"  ALERT {format_time(result.start)} {method}: failure rate {rate:.1%} is above 10%", flush=True)
    print(f"window {format_time(result.start)}  {' '.join(summary) or 'no terminal events'}", flush=True)
    if windows_out is not None:
        windows_out.append({"start": format_time(result.start), "methods": result.methods})


def save_state(path: str, processor: WindowProcessor, totals: dict) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump({"processor": processor.to_state(), "totals": totals}, file)
    os.replace(temporary, path)  # atomic: a crash leaves the old checkpoint or the new one


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="payments-monitor")
    parser.add_argument("--name", default="consumer-1", help="label in the log")
    parser.add_argument("--allowed-lateness", type=int, default=120, help="seconds; 0 for Experiment D")
    parser.add_argument("--state-file", help="checkpoint file; omit to keep state in memory only")
    parser.add_argument("--idle-exit", type=float, help="exit after this many seconds without messages")
    parser.add_argument("--windows-out", help="write every closed window to this JSON file")
    parser.add_argument("--quiet-windows", action="store_true", help="print alerts and totals only")
    parser.add_argument("--topic", default="payments")
    parser.add_argument("--brokers", default="localhost:19092")
    args = parser.parse_args()

    processor = WindowProcessor(allowed_lateness=args.allowed_lateness)
    totals = {"success": 0, "failure": 0, "alerts": 0, "method_windows": 0}
    if args.state_file and os.path.exists(args.state_file):
        with open(args.state_file, encoding="utf-8") as file:
            saved = json.load(file)
        processor.load_state(saved["processor"])
        totals = saved["totals"]
        print(f"[{args.name}] resumed from {args.state_file}: {processor.stats.consumed} events so far", flush=True)

    consumer = KafkaConsumer(
        bootstrap_servers=args.brokers,
        group_id=args.group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=json.loads,
        api_version=(2, 8, 0),  # Redpanda speaks this; skips the version probe, which can time out
        max_partition_fetch_bytes=MAX_FETCH_BYTES,
    )
    listener = ShowAssignment(args.name, processor)
    listener.consumer = consumer
    consumer.subscribe([args.topic], listener=listener)

    stopping = []
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    windows_out = [] if args.windows_out else None
    partition_counts: dict[int, int] = {}
    since_checkpoint = 0
    last_message = time.monotonic()

    def checkpoint() -> None:
        if args.state_file:
            save_state(args.state_file, processor, totals)
        try:
            consumer.commit()
        except CommitFailedError:
            # A rebalance took these partitions away; their new owner re-reads from the last commit.
            print(f"[{args.name}] commit refused during a rebalance", flush=True)

    print(f"[{args.name}] group={args.group} allowed lateness={args.allowed_lateness}s", flush=True)
    while not stopping:
        batches = consumer.poll(timeout_ms=1000)
        if not batches:
            if args.idle_exit and time.monotonic() - last_message > args.idle_exit:
                break
            continue
        last_message = time.monotonic()
        for partition, messages in batches.items():
            for message in messages:
                partition_counts[partition.partition] = partition_counts.get(partition.partition, 0) + 1
                for result in processor.process(partition.partition, message.value):
                    if args.quiet_windows:
                        report_quiet(result, totals, windows_out)
                    else:
                        report(result, totals, windows_out)
                since_checkpoint += 1
        if since_checkpoint >= CHECKPOINT_EVERY:
            checkpoint()
            since_checkpoint = 0

    if not stopping:  # the input really ended: report the windows still open
        for result in processor.flush():
            report(result, totals, windows_out)
    checkpoint()
    consumer.close()

    stats = processor.stats
    print(f"[{args.name}] messages per partition this run: {dict(sorted(partition_counts.items()))}", flush=True)
    print(
        f"[{args.name}] consumed={stats.consumed} duplicates={stats.duplicates} accepted={stats.accepted} "
        f"late_accepted={stats.late_accepted} late_rejected={stats.late_rejected} "
        f"max_rejected_lateness={stats.max_rejected_lateness}s windows={stats.windows_closed} "
        f"terminal_success={totals['success']} terminal_failure={totals['failure']} alerts={totals['alerts']} of {totals['method_windows']} method-windows",
        flush=True,
    )
    if args.windows_out:
        with open(args.windows_out, "w", encoding="utf-8") as file:
            json.dump(windows_out, file)


def report_quiet(result: WindowResult, totals: dict, windows_out: list | None) -> None:
    """The same accounting as report(), without a line per window: for replays of a whole day."""
    for method, counts in result.methods.items():
        totals["success"] += counts["success"]
        totals["failure"] += counts["failure"]
        totals["method_windows"] += 1
        if result.failure_rate(method) > ALERT_THRESHOLD:
            totals["alerts"] += 1
    if windows_out is not None:
        windows_out.append({"start": format_time(result.start), "methods": result.methods})


if __name__ == "__main__":
    main()
