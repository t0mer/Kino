"""images: transient_count (bounded requeues after infrastructure failures)

Revision ID: 0004_image_transient_count
Revises: 0003_image_processing_state
Create Date: 2026-10-11 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_image_transient_count"
down_revision = "0003_image_processing_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("transient_count", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.drop_column("transient_count")
