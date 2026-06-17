"""channel_snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("pulled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("subscribers", sa.BigInteger(), nullable=True),
        sa.Column("total_views", sa.BigInteger(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_channel_snapshots_platform_time", "channel_snapshots", ["platform", sa.text("pulled_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_channel_snapshots_platform_time", table_name="channel_snapshots")
    op.drop_table("channel_snapshots")
