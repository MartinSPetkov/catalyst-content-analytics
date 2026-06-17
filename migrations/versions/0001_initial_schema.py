"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("platform_post_id", sa.Text(), nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("topic_cluster", sa.Text(), nullable=True),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("hook_type", sa.Text(), nullable=True),
        sa.Column("length_bucket", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("tagged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "platform_post_id", name="uq_posts_platform_id"),
    )

    op.create_table(
        "metrics_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pulled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("engagements", sa.BigInteger(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "attribute_scores",
        sa.Column("attribute_type", sa.Text(), nullable=False),
        sa.Column("attribute_value", sa.Text(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=True),
        sa.Column("avg_engagement_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("trend_delta", sa.Numeric(8, 4), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("attribute_type", "attribute_value"),
    )

    op.create_table(
        "hypotheses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("hypothesis_text", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("supporting_post_count", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("attributes_used", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("scores_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "pull_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("platforms_pulled", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("posts_added", sa.Integer(), nullable=True),
        sa.Column("snapshots_added", sa.Integer(), nullable=True),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_metrics_post_time", "metrics_snapshots", ["post_id", sa.text("pulled_at DESC")])
    op.create_index("idx_metrics_pulled_at", "metrics_snapshots", ["pulled_at"])
    op.create_index("idx_posts_attributes", "posts", ["topic_cluster", "format", "hook_type"])
    op.create_index("idx_posts_platform", "posts", ["platform", sa.text("published_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_posts_platform", table_name="posts")
    op.drop_index("idx_posts_attributes", table_name="posts")
    op.drop_index("idx_metrics_pulled_at", table_name="metrics_snapshots")
    op.drop_index("idx_metrics_post_time", table_name="metrics_snapshots")

    op.drop_table("pull_log")
    op.drop_table("recommendations")
    op.drop_table("hypotheses")
    op.drop_table("attribute_scores")
    op.drop_table("metrics_snapshots")
    op.drop_table("posts")
