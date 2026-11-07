"""Draw the two Part 4 architecture diagrams as SVG: what was deployed, and what was requested."""

import pathlib
import sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
BOX_W, BOX_H = 190, 74


def box(x: int, y: int, title: str, sub: str = "", fill: str = "#eaf3fb", edge: str = "#2c6da4", dash: bool = False) -> str:
    style = ' stroke-dasharray="6 4"' if dash else ""
    return (
        f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="8" fill="{fill}" stroke="{edge}" stroke-width="2"{style}/>'
        f'<text x="{x + BOX_W / 2}" y="{y + 30}" text-anchor="middle" font-size="16" font-weight="bold" font-family="Helvetica" fill="#14202b">{title}</text>'
        f'<text x="{x + BOX_W / 2}" y="{y + 52}" text-anchor="middle" font-size="12" font-family="Helvetica" fill="#3c4a57">{sub}</text>'
    )


def arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    # The head is a small triangle, drawn by hand so every renderer shows it.
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    bx, by = x2 - ux * 11, y2 - uy * 11
    head = f"{x2},{y2} {bx - uy * 6},{by + ux * 6} {bx + uy * 6},{by - ux * 6}"
    text = ""
    if label:
        text = (
            f'<text x="{(x1 + x2) / 2 + (10 if dx == 0 else 0)}" y="{(y1 + y2) / 2 - 8}" '
            f'text-anchor="{"start" if dx == 0 else "middle"}" font-size="12" font-family="Helvetica" fill="#3c4a57">{label}</text>'
        )
    return f'<line x1="{x1}" y1="{y1}" x2="{bx}" y2="{by}" stroke="#14202b" stroke-width="2"/><polygon points="{head}" fill="#14202b"/>{text}'


def page(title: str, note: str, body: list[str], width: int = 1280, height: int = 500) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="white"/>'
        f'<text x="30" y="38" font-size="22" font-weight="bold" font-family="Helvetica" fill="#14202b">{title}</text>'
        f'<text x="30" y="62" font-size="14" font-family="Helvetica" fill="#a13a1f">{note}</text>' + "".join(body) + "</svg>"
    )


Y, X = 130, [30, 280, 530, 780, 1030]
deployed = [
    box(X[0], Y, "Python producer", "producer_sqs.py, 20 events/s", "#f3f3f3", "#555555"),
    box(X[1], Y, "Amazon SQS", "ooad-lab8-events (standard)", "#fdf0dd", "#b5730f"),
    box(X[2], Y, "AWS Lambda", "ooad-lab8-sqs-to-s3", "#fbe8dc", "#c2591a"),
    box(X[3], Y, "Amazon S3", "ooad-lab8-melasya/sqs-stream/", "#e6f3e0", "#3f8a2c"),
    box(X[4], Y, "Amazon Athena", "SQL over the delivered events", "#eaf3fb", "#2c6da4"),
    arrow(X[0] + BOX_W, Y + 37, X[1], Y + 37),
    arrow(X[1] + BOX_W, Y + 37, X[2], Y + 37, "trigger"),
    arrow(X[2] + BOX_W, Y + 37, X[3], Y + 37, "JSONL"),
    arrow(X[3] + BOX_W, Y + 37, X[4], Y + 37),
    box(X[1], 290, "SQS dead-letter queue", "ooad-lab8-events-dlq", "#fdf0dd", "#b5730f"),
    arrow(X[1] + BOX_W // 2, Y + BOX_H, X[1] + BOX_W // 2, 290),
    box(X[2], 290, "Amazon SNS", "ooad-lab8-failure-rate", "#f6e6f3", "#94307f"),
    arrow(X[2] + BOX_W // 2, Y + BOX_H, X[2] + BOX_W // 2, 290, "rate above 10%"),
    box(X[2], 400, "SQS alert sink", "ooad-lab8-alert-sink", "#fdf0dd", "#b5730f"),
    arrow(X[2] + BOX_W // 2, 290 + BOX_H, X[2] + BOX_W // 2, 400),
    box(X[4], 290, "Glue Data Catalog", "lab8_sqs.stream_events", "#eef0fb", "#4a55a8"),
    arrow(X[4] + BOX_W // 2, 290, X[4] + BOX_W // 2, Y + BOX_H, "table definition"),
]
requested = [
    box(X[0], Y, "Python producer", "producer_kinesis.py", "#f3f3f3", "#555555", True),
    box(X[1], Y, "Kinesis Data Streams", "ooad-lab8-payments, 1 shard", "#fdf0dd", "#b5730f", True),
    box(X[2], Y, "Amazon Data Firehose", "buffer 60 s / 1 MB", "#fbe8dc", "#c2591a", True),
    box(X[3], Y, "Amazon S3", "stream/dt=.../", "#e6f3e0", "#3f8a2c", True),
    box(X[4], Y, "Amazon Athena", "SQL over the delivered events", "#eaf3fb", "#2c6da4", True),
    arrow(X[0] + BOX_W, Y + 37, X[1], Y + 37),
    arrow(X[1] + BOX_W, Y + 37, X[2], Y + 37),
    arrow(X[2] + BOX_W, Y + 37, X[3], Y + 37),
    arrow(X[3] + BOX_W, Y + 37, X[4], Y + 37),
    box(X[1], 290, "AWS Lambda", "failure-rate check", "#fbe8dc", "#c2591a", True),
    arrow(X[1] + BOX_W // 2, Y + BOX_H, X[1] + BOX_W // 2, 290),
    box(X[2], 290, "Amazon SNS", "alert", "#f6e6f3", "#94307f", True),
    arrow(X[1] + BOX_W, 327, X[2], 327),
    box(X[4], 290, "Glue Data Catalog", "table definition", "#eef0fb", "#4a55a8", True),
    arrow(X[4] + BOX_W // 2, 290, X[4] + BOX_W // 2, Y + BOX_H),
]

(OUT / "architecture_deployed_sqs.svg").write_text(
    page(
        "Lab 8 Part 4: the deployed architecture (SQS alternative)",
        "An alternative, not an equivalent: Kinesis and Firehose are unavailable on this AWS Free Plan account.",
        deployed,
    ),
    encoding="utf-8",
)
(OUT / "architecture_requested_kinesis_not_deployed.svg").write_text(
    page(
        "Lab 8 Part 4: the requested architecture - NOT DEPLOYED",
        "Reference only (dashed): the code is in lab8/cloud/, but these calls return SubscriptionRequiredException on this account.",
        requested,
        height=400,
    ),
    encoding="utf-8",
)
