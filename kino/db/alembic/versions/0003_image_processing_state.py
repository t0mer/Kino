"""images: processing_started_at and stuck_count (stale image sweeper)

Revision ID: 0003_image_processing_state
Revises: 0002_forward_next_attempt
Create Date: 2026-10-11 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_image_processing_state"
down_revision = "0002_forward_next_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.add_column(sa.Column("processing_started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("stuck_count", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.drop_column("stuck_count")
        batch_op.drop_column("processing_started_at")
