"""forwards: next_attempt_at and file_path

Revision ID: 0002_forward_next_attempt
Revises: 0001_initial
Create Date: 2026-10-10 23:50:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_forward_next_attempt"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("forwards", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("file_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("forwards", schema=None) as batch_op:
        batch_op.drop_column("file_path")
        batch_op.drop_column("next_attempt_at")
