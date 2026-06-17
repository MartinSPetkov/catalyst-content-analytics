"""platform_attribute_scores

Add platform column to attribute_scores so scores are computed per-platform
rather than blended across YouTube and LinkedIn.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the existing composite PK and all rows (scores are always fully
    # recomputed from posts+snapshots, so safe to clear and rebuild).
    op.execute("DELETE FROM attribute_scores")
    op.execute("ALTER TABLE attribute_scores DROP CONSTRAINT attribute_scores_pkey")

    op.add_column("attribute_scores", sa.Column("platform", sa.Text(), nullable=False, server_default="unknown"))
    op.execute("ALTER TABLE attribute_scores ALTER COLUMN platform DROP DEFAULT")

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


def downgrade() -> None:
    op.drop_index("idx_attribute_scores_platform", table_name="attribute_scores")
    op.execute("DELETE FROM attribute_scores")
    op.execute("ALTER TABLE attribute_scores DROP CONSTRAINT attribute_scores_pkey")
    op.drop_column("attribute_scores", "platform")
    op.create_primary_key(
        "attribute_scores_pkey",
        "attribute_scores",
        ["attribute_type", "attribute_value"],
    )
