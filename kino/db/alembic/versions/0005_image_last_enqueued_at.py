"""images: last_enqueued_at (sweeper re-enqueue dedupe)

Revision ID: 0005_image_last_enqueued_at
Revises: 0004_image_transient_count
Create Date: 2026-10-11 13:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_image_last_enqueued_at"
down_revision = "0004_image_transient_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_enqueued_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.drop_column("last_enqueued_at")
