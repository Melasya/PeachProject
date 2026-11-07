"""Event-time windows with a watermark. No Kafka here, so it can be unit-tested and explained alone.

The idea, in three rules:

1. An event belongs to the one-minute window of its modifiedDate (event time), not of the moment
   we happen to read it.

2. The watermark says "I no longer expect events older than this":

       watermark = (lowest of the partitions' newest event times) - allowed lateness

   Each partition is ordered on its own, so a partition's newest event time only says something
   about that partition. Taking the lowest means a partition that is behind holds the clock back,
   instead of a fast partition declaring the slow one's events late. This is what makes a consumer
   safe when it catches up after a restart. For the same reason there is no watermark at all until
   every assigned partition has delivered at least one event.

3. A window [start, start + 60s) closes as soon as watermark >= its end. Its result is final and is
   printed once. An event for a window that has already closed is too late: it is rejected and
   counted, never silently added to a result that has already been reported.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

WINDOW_SECONDS = 60
TERMINAL = ("success", "failure")


def parse_time(value: str) -> int:
    """'2026-10-15T19:48:01Z' -> epoch seconds."""
    return int(datetime.fromisoformat(value).timestamp())


def format_time(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M:%S")


def event_identity(event: dict) -> str:
    """Two deliveries of one status change are the same event."""
    return f'{event["invoiceId"]}|{event["status"]}|{event["modifiedDate"]}'


@dataclass
class WindowResult:
    start: int
    # {paymentMethod: {"success": n, "failure": n}}
    methods: dict[str, dict[str, int]]

    def failure_rate(self, method: str) -> float:
        counts = self.methods[method]
        return counts["failure"] / (counts["success"] + counts["failure"])


@dataclass
class Stats:
    consumed: int = 0
    duplicates: int = 0
    accepted: int = 0
    late_accepted: int = 0  # older than its partition's newest event, but its window was still open
    late_rejected: int = 0  # its window had already closed
    max_rejected_lateness: int = 0
    windows_closed: int = 0


@dataclass
class WindowProcessor:
    allowed_lateness: int = 120
    open_windows: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)  # event identity -> event time
    partition_times: dict = field(default_factory=dict)  # partition -> newest event time
    assigned: set | None = None  # None: whichever partitions have been seen (unit tests)
    stats: Stats = field(default_factory=Stats)

    def assign(self, partitions: set[int]) -> None:
        """The broker told us which partitions are ours; forget the clocks of the ones we lost."""
        self.assigned = set(partitions)
        self.partition_times = {p: when for p, when in self.partition_times.items() if p in self.assigned}

    def watermark(self) -> int | None:
        if not self.partition_times:
            return None
        if self.assigned is not None and not self.assigned <= set(self.partition_times):
            return None  # a partition we have not heard from yet might hold older events
        return min(self.partition_times.values()) - self.allowed_lateness

    def process(self, partition: int, event: dict) -> list[WindowResult]:
        """Take one event; return the windows it caused to close."""
        self.stats.consumed += 1

        identity = event_identity(event)
        if identity in self.seen:
            self.stats.duplicates += 1
            return []

        event_time = parse_time(event["modifiedDate"])
        window_start = event_time - event_time % WINDOW_SECONDS

        # Decided before this event moves the clock: its window may be closed already.
        watermark = self.watermark()
        if watermark is not None and window_start + WINDOW_SECONDS <= watermark:
            self.stats.late_rejected += 1
            behind_clock = watermark + self.allowed_lateness - event_time
            self.stats.max_rejected_lateness = max(self.stats.max_rejected_lateness, behind_clock)
        else:
            self.seen[identity] = event_time
            self.stats.accepted += 1
            # Measured within this event's own partition: only there is the order meaningful.
            if event_time < self.partition_times.get(partition, event_time):
                self.stats.late_accepted += 1
            self._count(window_start, event)

        previous = self.partition_times.get(partition, event_time)
        self.partition_times[partition] = max(previous, event_time)
        return self._close_finished_windows() if self.watermark() is not None else []

    def flush(self) -> list[WindowResult]:
        """End of the input: nothing else is coming, so report whatever is still open."""
        return [self._close(start) for start in sorted(self.open_windows)]

    def _count(self, window_start: int, event: dict) -> None:
        methods = self.open_windows.setdefault(window_start, {})
        if event["status"] in TERMINAL:
            counts = methods.setdefault(event["paymentMethod"], {"success": 0, "failure": 0})
            counts[event["status"]] += 1

    def _close_finished_windows(self) -> list[WindowResult]:
        watermark = self.watermark()
        finished = sorted(s for s in self.open_windows if s + WINDOW_SECONDS <= watermark)
        closed = [self._close(start) for start in finished]
        if closed:
            # A later copy of these events falls in a closed window and is rejected anyway,
            # so their identities need not be kept. This is what keeps memory bounded.
            self.seen = {key: when for key, when in self.seen.items() if when >= watermark}
        return closed

    def _close(self, start: int) -> WindowResult:
        self.stats.windows_closed += 1
        return WindowResult(start=start, methods=self.open_windows.pop(start))

    # --- checkpoint: the whole state as plain JSON-able data ---------------------------------

    def to_state(self) -> dict:
        return {
            "open_windows": {str(k): v for k, v in self.open_windows.items()},
            "seen": self.seen,
            "partition_times": {str(k): v for k, v in self.partition_times.items()},
            "stats": vars(self.stats),
        }

    def load_state(self, state: dict) -> None:
        self.open_windows = {int(k): v for k, v in state["open_windows"].items()}
        self.seen = state["seen"]
        self.partition_times = {int(k): v for k, v in state["partition_times"].items()}
        self.stats = Stats(**state["stats"])
