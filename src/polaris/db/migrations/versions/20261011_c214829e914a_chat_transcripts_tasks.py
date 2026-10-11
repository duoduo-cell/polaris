"""Separate visible transcripts from model context; persist background tasks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c214829e914a"
down_revision = "7bfcca2366d7"
branch_labels = depends_on = None


def upgrade():
    op.create_table("chat_threads",
                    sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("history_imported", sa.Boolean(), nullable=False),
                    sa.Column("approvals", JSONB(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("chat_messages",
                    sa.Column("id", sa.BigInteger(), primary_key=True),
                    sa.Column("thread_id", sa.String(64), sa.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("source_key", sa.String(192), nullable=False),
                    sa.Column("role", sa.String(16), nullable=False),
                    sa.Column("content", sa.Text(), nullable=False),
                    sa.Column("turn", JSONB()),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.UniqueConstraint("thread_id", "source_key"))
    op.create_index("ix_chat_messages_thread", "chat_messages", ["thread_id", "id"])
    op.create_table("chat_tasks",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("request_key", sa.String(64), nullable=False, unique=True),
                    sa.Column("thread_id", sa.String(64), sa.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("kind", sa.String(16), nullable=False),
                    sa.Column("payload", JSONB(), nullable=False),
                    sa.Column("state", sa.String(24), nullable=False),
                    sa.Column("activities", JSONB(), nullable=False),
                    sa.Column("result", JSONB()),
                    sa.Column("error", sa.Text()),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.Column("started_at", sa.DateTime(timezone=True)),
                    sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_chat_tasks_thread", "chat_tasks", ["thread_id", "created_at"])


def downgrade():
    op.drop_table("chat_tasks")
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
