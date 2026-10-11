"""Chat tasks: transient live state (draft answer, compacting) and a stop request flag."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "5b7e2c9d41f0"
down_revision = "a812d53f607e"
branch_labels = depends_on = None


def upgrade():
    op.add_column("chat_tasks", sa.Column("live", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("chat_tasks", sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade():
    op.drop_column("chat_tasks", "cancel_requested")
    op.drop_column("chat_tasks", "live")
