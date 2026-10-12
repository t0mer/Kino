"""reference_faces.outlier / outlier_reason: nightly gallery health verdict

Revision ID: 0009_reference_outlier
Revises: 0008_digest_pending_hash
Create Date: 2026-10-12 13:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_reference_outlier"
down_revision = "0008_digest_pending_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reference_faces", schema=None) as batch_op:
        batch_op.add_column(sa.Column("outlier", sa.Boolean(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("outlier_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reference_faces", schema=None) as batch_op:
        batch_op.drop_column("outlier_reason")
        batch_op.drop_column("outlier")
