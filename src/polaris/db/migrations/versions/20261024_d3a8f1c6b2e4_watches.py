"""Watches: long-running follow-up goals, their target jobs with snapshots, detected events and situation reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d3a8f1c6b2e4"
down_revision = "c41d7e9a2b10"
branch_labels = depends_on = None

NOW = sa.text("now()")
EMPTY = sa.text("'{}'::jsonb")


def upgrade():
    op.create_table(
        "watches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("criteria", JSONB(), server_default=EMPTY, nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("until", sa.Date()),
        sa.Column("cadence_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("report_hour", sa.Integer(), server_default="21", nullable=False),
        sa.Column("notify", sa.String(16), server_default="desktop", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_report_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("state", JSONB(), server_default=EMPTY, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_table(
        "watch_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("watch_id", sa.BigInteger(), sa.ForeignKey("watches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), server_default=EMPTY, nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("watch_id", "job_id", name="uq_watch_targets_job"),
    )
    op.create_index("ix_watch_targets_watch_id", "watch_targets", ["watch_id"])
    op.create_table(
        "watch_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("watch_id", sa.BigInteger(), sa.ForeignKey("watches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("watch_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("importance", sa.String(8), nullable=False),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(32)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", JSONB(), server_default=EMPTY, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("watch_id", "fingerprint", name="uq_watch_events_fingerprint"),
    )
    op.create_index("ix_watch_events_watch_created", "watch_events", ["watch_id", "created_at"])
    op.create_table(
        "watch_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("watch_id", sa.BigInteger(), sa.ForeignKey("watches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facts", JSONB(), server_default=EMPTY, nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("fallback", sa.String(64)),
        sa.Column("llm_call_id", sa.BigInteger(), sa.ForeignKey("llm_calls.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_watch_reports_watch_id", "watch_reports", ["watch_id"])


def downgrade():
    op.drop_index("ix_watch_reports_watch_id", "watch_reports")
    op.drop_table("watch_reports")
    op.drop_index("ix_watch_events_watch_created", "watch_events")
    op.drop_table("watch_events")
    op.drop_index("ix_watch_targets_watch_id", "watch_targets")
    op.drop_table("watch_targets")
    op.drop_table("watches")
