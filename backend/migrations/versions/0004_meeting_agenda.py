"""agenda and action items on meetings, plus the AI suggestion they came from

Additive only: two new tables and four nullable columns on meetings. items,
existing meetings and the weekly report's columns are untouched, and
downgrade drops only what this revision created.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Provenance: what the parser suggested, before the person edited it.
    op.add_column("meetings", sa.Column("ai_suggestion", postgresql.JSONB(), nullable=True))
    op.add_column("meetings", sa.Column("ai_model", sa.String(length=100), nullable=True))
    op.add_column("meetings", sa.Column("ai_prompt_version", sa.String(length=32), nullable=True))
    # The Cognito user id (sub) of whoever saved it through the API.
    op.add_column("meetings", sa.Column("created_by", sa.String(length=128), nullable=True))

    op.create_table(
        "meeting_agenda_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_meeting_agenda_items_duration_positive",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "position", name="uq_meeting_agenda_items_position"),
    )
    op.create_table(
        "meeting_action_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "position", name="uq_meeting_action_items_position"),
    )


def downgrade() -> None:
    op.drop_table("meeting_action_items")
    op.drop_table("meeting_agenda_items")
    op.drop_column("meetings", "created_by")
    op.drop_column("meetings", "ai_prompt_version")
    op.drop_column("meetings", "ai_model")
    op.drop_column("meetings", "ai_suggestion")
