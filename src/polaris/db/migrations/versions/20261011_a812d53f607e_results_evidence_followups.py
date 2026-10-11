"""Structured interview evidence and independent per-job followups."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a812d53f607e"
down_revision = "c214829e914a"
branch_labels = depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("evidence_profile", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_table("interview_questions",
                    sa.Column("id", sa.BigInteger(), primary_key=True),
                    sa.Column("key", sa.String(80), nullable=False, unique=True),
                    sa.Column("post_id", sa.BigInteger(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("question", sa.Text(), nullable=False),
                    sa.Column("company", sa.String(200)), sa.Column("role", sa.String(200)),
                    sa.Column("team", sa.String(200)), sa.Column("job_type", sa.String(16)),
                    sa.Column("round", sa.String(50)), sa.Column("category", sa.String(32), nullable=False),
                    sa.Column("material", sa.String(16), nullable=False), sa.Column("evidence", sa.Text()),
                    sa.Column("attribution", sa.String(16), nullable=False), sa.Column("interview_date", sa.Date()),
                    sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_index("ix_interview_questions_post_id", "interview_questions", ["post_id"])
    op.create_index("ix_interview_questions_company", "interview_questions", ["company"])
    op.create_table("job_followups",
                    sa.Column("id", sa.BigInteger(), primary_key=True),
                    sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
                    sa.Column("saved", sa.Boolean(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
                    sa.Column("stage", sa.String(200)), sa.Column("note", sa.Text()), sa.Column("next_followup", sa.Date()),
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("job_followup_events",
                    sa.Column("id", sa.BigInteger(), primary_key=True),
                    sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("request_key", sa.String(100), nullable=False, unique=True),
                    sa.Column("before", JSONB(), nullable=False), sa.Column("after", JSONB(), nullable=False),
                    sa.Column("changes", JSONB(), nullable=False), sa.Column("source_text", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_job_followup_events_job_id", "job_followup_events", ["job_id"])


def downgrade():
    op.drop_table("job_followup_events")
    op.drop_table("job_followups")
    op.drop_table("interview_questions")
    op.drop_column("posts", "evidence_profile")
