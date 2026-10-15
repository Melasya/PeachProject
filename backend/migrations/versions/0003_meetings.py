"""add meetings and meeting_participants for the weekly report

Adds two new tables and touches nothing else: items and its data are left as
they are, and downgrade drops only what this revision created.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("organiser", sa.String(length=254), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_meetings_ends_after_starts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_starts_at", "meetings", ["starts_at"])

    op.create_table(
        "meeting_participants",
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "email"),
    )


def downgrade() -> None:
    op.drop_table("meeting_participants")
    op.drop_index("ix_meetings_starts_at", table_name="meetings")
    op.drop_table("meetings")
