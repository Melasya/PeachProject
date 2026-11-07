"""Run the four Part 3 experiments against the local Redpanda and save the real logs.

    python experiments.py A    # stop the consumer for a minute, restart, compare with a control
    python experiments.py B    # two consumers in one group
    python experiments.py C    # 30% of payments fail
    python experiments.py D    # allowed lateness 0 vs 120 seconds (and 600) over one whole day

Each run starts from an empty topic and writes evidence/<experiment>_*.log.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
EVIDENCE = HERE / "evidence"
PYTHON = sys.executable


def banner(text: str) -> None:
    print(f"\n=== {time.strftime('%H:%M:%S')} {text}", flush=True)


def rpk(*args: str) -> str:
    command = ["docker", "compose", "exec", "-T", "redpanda", "rpk", *args]
    done = subprocess.run(command, cwd=HERE, capture_output=True, text=True, check=False)
    return done.stdout + done.stderr


def reset_topic() -> None:
    rpk("topic", "delete", "payments")
    print(rpk("topic", "create", "payments", "--partitions", "3", "--replicas", "1").strip(), flush=True)
    print(rpk("topic", "list").strip(), flush=True)
    time.sleep(3)  # let the new partitions settle before clients connect


def start(log_name: str, script: str, *args: str) -> subprocess.Popen:
    log = open(EVIDENCE / log_name, "a", encoding="utf-8")  # noqa: SIM115 - closed with the process
    return subprocess.Popen(
        [PYTHON, "-u", script, *args],
        cwd=HERE,
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def kill_hard(process: subprocess.Popen) -> None:
    """Like a crash or a pulled plug: no goodbye, nothing saved on the way out."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    process.wait()


def show(label: str, text: str, log_name: str) -> None:
    print(f"--- {label}\n{text.strip()}", flush=True)
    with open(EVIDENCE / log_name, "a", encoding="utf-8") as file:
        file.write(f"\n--- {time.strftime('%H:%M:%S')} {label}\n{text.strip()}\n")


def last_line(log_name: str) -> str:
    lines = (EVIDENCE / log_name).read_text(encoding="utf-8").strip().splitlines()
    return next(line for line in reversed(lines) if "consumed=" in line)


def experiment_a() -> None:
    limit = "3600"  # three minutes at 20 events/s
    state = str(EVIDENCE / "A_state.json")
    pathlib.Path(state).unlink(missing_ok=True)
    reset_topic()

    banner("consumer starts, producer replays 3600 events at 20/s")
    consumer = start("A_consumer.log", "consumer.py", "--group", "exp-a", "--state-file", state, "--idle-exit", "20")
    time.sleep(8)
    producer = start("A_producer.log", "producer.py", "--rate", "20", "--limit", limit)
    time.sleep(40)

    banner("killing the consumer (hard stop) while the producer keeps sending")
    kill_hard(consumer)
    show("group right after the stop", rpk("group", "describe", "exp-a"), "A_consumer.log")
    time.sleep(60)
    show("group after 60 s without a consumer: the lag has grown", rpk("group", "describe", "exp-a"), "A_consumer.log")

    banner("restarting the consumer with the same group and state file")
    consumer = start("A_consumer.log", "consumer.py", "--group", "exp-a", "--state-file", state, "--idle-exit", "20")
    producer.wait()
    consumer.wait()
    show("group after the catch-up", rpk("group", "describe", "exp-a"), "A_consumer.log")

    banner("control: a second group reads the same retained messages, never interrupted")
    control = start("A_control.log", "consumer.py", "--group", "exp-a-control", "--idle-exit", "15")
    control.wait()
    print("interrupted:", last_line("A_consumer.log"), flush=True)
    print("control:    ", last_line("A_control.log"), flush=True)


def experiment_b() -> None:
    reset_topic()
    banner("consumer-1 alone, then consumer-2 joins the same group after 20 s")
    first = start("B_consumer1.log", "consumer.py", "--group", "exp-b", "--name", "consumer-1", "--idle-exit", "20")
    time.sleep(8)
    producer = start("B_producer.log", "producer.py", "--rate", "20", "--limit", "2400")
    time.sleep(20)
    show("group with one consumer", rpk("group", "describe", "exp-b"), "B_group.log")
    second = start("B_consumer2.log", "consumer.py", "--group", "exp-b", "--name", "consumer-2", "--idle-exit", "20")
    time.sleep(30)
    show("group with two consumers", rpk("group", "describe", "exp-b"), "B_group.log")
    producer.wait()
    first.wait()
    second.wait()
    show("group at the end", rpk("group", "describe", "exp-b"), "B_group.log")


def experiment_c() -> None:
    for name, extra in (("baseline", []), ("failures30", ["--failure-rate", "0.3"])):
        reset_topic()
        banner(f"whole day, {name}")
        producer = start(f"C_{name}_producer.log", "producer.py", "--rate", "2000", *extra)
        producer.wait()
        consumer = start(
            f"C_{name}_consumer.log", "consumer.py", "--group", f"exp-c-{name}", "--idle-exit", "15",
            "--windows-out", str(EVIDENCE / f"C_{name}_windows.json"),
        )  # fmt: skip
        consumer.wait()
        print(name, last_line(f"C_{name}_consumer.log"), flush=True)


def experiment_d() -> None:
    """All consumers read one live replay side by side, so each sees the same stream at the same pace."""
    reset_topic()
    consumers = []
    for lateness in ("120", "0", "600"):
        consumers.append(
            start(
                f"D_lateness{lateness}.log", "consumer.py", "--group", f"exp-d-{lateness}",
                "--allowed-lateness", lateness, "--idle-exit", "30", "--quiet-windows",
                "--windows-out", str(EVIDENCE / f"D_lateness{lateness}_windows.json"),
            )
        )
    time.sleep(8)
    banner("whole day, live at 100 events/s, with allowed lateness 120 s, 0 s and 600 s reading together")
    start("D_producer.log", "producer.py", "--rate", "100").wait()
    for consumer in consumers:
        consumer.wait()
    for lateness in ("120", "0", "600"):
        print(lateness, last_line(f"D_lateness{lateness}.log"), flush=True)
    compare_windows()


def compare_windows() -> None:
    def load(lateness: str) -> dict:
        windows = json.loads((EVIDENCE / f"D_lateness{lateness}_windows.json").read_text(encoding="utf-8"))
        return {window["start"]: window["methods"] for window in windows}

    patient, strict = load("120"), load("0")
    differing = [start for start in patient if patient[start] != strict.get(start)]

    def finished(windows: dict) -> int:
        return sum(c["success"] + c["failure"] for methods in windows.values() for c in methods.values())

    def rate(counts: dict) -> float:
        return counts["failure"] / (counts["success"] + counts["failure"])

    # Where the two settings disagree about the alert itself, window by window, method by method.
    only_patient = only_strict = 0
    for start in set(patient) & set(strict):
        for method in set(patient[start]) & set(strict[start]):
            alert_patient = rate(patient[start][method]) > 0.10
            alert_strict = rate(strict[start][method]) > 0.10
            only_patient += alert_patient and not alert_strict
            only_strict += alert_strict and not alert_patient

    report = (
        f"windows: 120s={len(patient)} 0s={len(strict)}; windows whose numbers differ: {len(differing)}\n"
        f"finished payments counted: 120s={finished(patient)} 0s={finished(strict)}\n"
        f"alert decisions that flip: fires only with 120s={only_patient}, fires only with 0s={only_strict}"
    )
    show("comparison", report, "D_comparison.log")


def main() -> None:
    EVIDENCE.mkdir(exist_ok=True)
    experiments = {"A": experiment_a, "B": experiment_b, "C": experiment_c, "D": experiment_d}
    if len(sys.argv) != 2 or sys.argv[1] not in experiments:
        sys.exit(__doc__)
    for old_file in EVIDENCE.glob(f"{sys.argv[1]}_*"):  # a re-run must not append to an earlier run's logs
        old_file.unlink()
    experiments[sys.argv[1]]()
    # A consumer that died at startup would silently turn this experiment into a different one.
    crashed = [log.name for log in EVIDENCE.glob(f"{sys.argv[1]}_*.log") if "Traceback" in log.read_text(encoding="utf-8")]
    if crashed:
        sys.exit(f"FAILED: a process crashed, see {crashed}")
    banner("done")


if __name__ == "__main__":
    main()
