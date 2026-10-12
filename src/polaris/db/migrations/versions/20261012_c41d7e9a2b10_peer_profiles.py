"""Peer profiles: who got (or failed) which job, with what background, extracted from posts with evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c41d7e9a2b10"
down_revision = "5b7e2c9d41f0"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "peer_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("raw_item_id", sa.BigInteger(), sa.ForeignKey("raw_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("who", sa.String(8), nullable=False),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("grad_year", sa.String(50)),
        sa.Column("school", sa.String(200)),
        sa.Column("major", sa.String(200)),
        sa.Column("papers", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("paper_summary", sa.Text()),
        sa.Column("top_papers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("internships", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("awards", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("target_company", sa.String(200)),
        sa.Column("big_company", sa.String(64)),
        sa.Column("target_team", sa.String(200)),
        sa.Column("target_role", sa.String(200)),
        sa.Column("job_type", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(100)),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("reached", sa.String(50)),
        sa.Column("difficulty", sa.Text()),
        sa.Column("dates", sa.String(200)),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("from_image", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("llm_call_id", sa.BigInteger(), sa.ForeignKey("llm_calls.id", ondelete="SET NULL")),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_peer_profiles_raw", "peer_profiles", ["raw_item_id"])
    op.create_index("ix_peer_profiles_company", "peer_profiles", ["big_company"])


def downgrade():
    op.drop_index("ix_peer_profiles_company", "peer_profiles")
    op.drop_index("ix_peer_profiles_raw", "peer_profiles")
    op.drop_table("peer_profiles")
