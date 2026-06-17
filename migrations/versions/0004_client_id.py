"""client_id

Add client_id to posts and attribute_scores so the system can serve multiple
Catalyst clients from a single database, with all analytics scoped per client.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── posts ────────────────────────────────────────────────────────────────
    op.add_column("posts", sa.Column("client_id", sa.Text(), nullable=False,
                                     server_default="default"))
    op.execute("ALTER TABLE posts ALTER COLUMN client_id DROP DEFAULT")

    op.drop_index("idx_posts_platform", table_name="posts")
    op.create_index(
        "idx_posts_client_platform",
        "posts",
        ["client_id", "platform", sa.text("published_at DESC")],
    )

    # ── attribute_scores ─────────────────────────────────────────────────────
    # Safe to clear — recomputed every scheduler run.
    op.execute("DELETE FROM attribute_scores")
    op.execute("ALTER TABLE attribute_scores DROP CONSTRAINT attribute_scores_pkey")
    op.drop_index("idx_attribute_scores_platform", table_name="attribute_scores")

    op.add_column("attribute_scores", sa.Column("client_id", sa.Text(), nullable=False,
                                                 server_default="default"))
    op.execute("ALTER TABLE attribute_scores ALTER COLUMN client_id DROP DEFAULT")

    op.create_primary_key(
        "attribute_scores_pkey",
        "attribute_scores",
        ["client_id", "platform", "attribute_type", "attribute_value"],
    )
    op.create_index(
        "idx_attribute_scores_client_platform",
        "attribute_scores",
        ["client_id", "platform", "attribute_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_attribute_scores_client_platform", table_name="attribute_scores")
    op.execute("DELETE FROM attribute_scores")
    op.execute("ALTER TABLE attribute_scores DROP CONSTRAINT attribute_scores_pkey")
    op.drop_column("attribute_scores", "client_id")
    op.create_primary_key(
        "attribute_scores_pkey",
        "attribute_scores",
        ["platform", "attribute_type", "attribute_value"],
    )
    op.create_index(
        "idx_attribute_scores_platform",
        "attribute_scores",
        ["platform", "attribute_type"],
    )

    op.drop_index("idx_posts_client_platform", table_name="posts")
    op.drop_column("posts", "client_id")
    op.create_index(
        "idx_posts_platform",
        "posts",
        ["platform", sa.text("published_at DESC")],
    )
