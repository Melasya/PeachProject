"""The watermark rules, checked without Kafka: python -m pytest test_windowing.py"""

from windowing import WindowProcessor, format_time, parse_time


def event(modified: str, status: str = "success", invoice: str = "i1", method: str = "card") -> dict:
    return {"invoiceId": invoice, "status": status, "modifiedDate": f"2026-10-15T{modified}Z", "paymentMethod": method}


def test_exact_duplicate_is_counted_once():
    processor = WindowProcessor()
    processor.process(0, event("10:00:10"))
    processor.process(0, event("10:00:10"))
    assert processor.stats.duplicates == 1
    assert processor.stats.accepted == 1
    assert processor.open_windows[parse_time("2026-10-15T10:00:00Z")]["card"]["success"] == 1


def test_window_waits_for_the_allowed_lateness():
    processor = WindowProcessor(allowed_lateness=120)
    assert processor.process(0, event("10:00:10", invoice="a")) == []
    # Watermark 10:01:59: the 10:00 window ends at 10:01:00, so it closes here...
    closed = processor.process(0, event("10:03:59", invoice="b"))
    assert [format_time(result.start) for result in closed] == ["2026-10-15 10:00:00"]


def test_window_stays_open_until_the_watermark_passes_its_end():
    processor = WindowProcessor(allowed_lateness=120)
    processor.process(0, event("10:00:10", invoice="a"))
    # ...but not one second earlier: watermark 10:00:59 is before the window's end.
    assert processor.process(0, event("10:02:59", invoice="b")) == []


def test_late_event_inside_allowed_lateness_is_kept():
    processor = WindowProcessor(allowed_lateness=120)
    processor.process(0, event("10:02:30", invoice="a"))
    processor.process(0, event("10:00:30", invoice="b"))  # two minutes behind, window still open
    assert processor.stats.late_accepted == 1
    assert processor.stats.late_rejected == 0


def test_event_after_its_window_closed_is_rejected_and_not_counted():
    processor = WindowProcessor(allowed_lateness=120)
    processor.process(0, event("10:00:10", invoice="a"))
    closed = processor.process(0, event("10:05:00", invoice="b"))
    assert closed[0].methods["card"]["success"] == 1
    processor.process(0, event("10:00:20", invoice="late"))
    assert processor.stats.late_rejected == 1
    assert processor.stats.max_rejected_lateness == 280


def test_zero_lateness_rejects_what_two_minutes_accepts():
    events = [event("10:00:10", invoice="a"), event("10:01:30", invoice="b"), event("10:00:50", invoice="c")]
    strict, patient = WindowProcessor(allowed_lateness=0), WindowProcessor(allowed_lateness=120)
    for item in events:
        strict.process(0, item)
        patient.process(0, item)
    assert strict.stats.late_rejected == 1
    assert patient.stats.late_rejected == 0


def test_slow_partition_holds_the_watermark_back():
    processor = WindowProcessor(allowed_lateness=0)
    processor.process(0, event("10:00:10", invoice="a"))
    processor.process(1, event("10:00:20", invoice="b"))
    # Partition 0 races ahead, as it can when a consumer catches up. Partition 1 is still at 10:00.
    assert processor.process(0, event("10:30:00", invoice="c")) == []
    assert processor.watermark() == parse_time("2026-10-15T10:00:20Z")
    processor.process(1, event("10:00:40", invoice="d"))
    assert processor.stats.late_rejected == 0


def test_failure_rate_uses_terminal_events_only():
    processor = WindowProcessor(allowed_lateness=0)
    processor.process(0, event("10:00:01", status="created", invoice="a"))
    processor.process(0, event("10:00:02", status="success", invoice="a"))
    processor.process(0, event("10:00:03", status="failure", invoice="b"))
    closed = processor.process(0, event("10:01:00", status="created", invoice="c"))
    assert closed[0].methods == {"card": {"success": 1, "failure": 1}}
    assert closed[0].failure_rate("card") == 0.5


def test_state_survives_a_restart():
    before = WindowProcessor(allowed_lateness=120)
    before.process(0, event("10:00:10", invoice="a"))
    after = WindowProcessor(allowed_lateness=120)
    after.load_state(before.to_state())
    after.process(0, event("10:00:10", invoice="a"))  # re-read after the restart
    assert after.stats.duplicates == 1
    assert after.flush()[0].methods["card"]["success"] == 1


def test_no_watermark_until_every_assigned_partition_has_spoken():
    processor = WindowProcessor(allowed_lateness=0)
    processor.assign({0, 1, 2})
    processor.process(0, event("10:00:10", invoice="a"))
    # A consumer may read a long batch of partition 0 before it sees the others.
    assert processor.process(0, event("15:00:00", invoice="b")) == []
    assert processor.watermark() is None
    processor.process(1, event("10:00:20", invoice="c"))
    processor.process(2, event("10:00:30", invoice="d"))
    assert processor.stats.late_rejected == 0
    assert processor.watermark() == parse_time("2026-10-15T10:00:20Z")


def test_rebalance_forgets_the_clock_of_a_lost_partition():
    processor = WindowProcessor(allowed_lateness=0)
    processor.assign({0, 1})
    processor.process(0, event("10:30:00", invoice="a"))
    processor.process(1, event("10:00:00", invoice="b"))
    processor.assign({0})  # partition 1 moved to another consumer; it must not hold us back
    assert processor.watermark() == parse_time("2026-10-15T10:30:00Z")
