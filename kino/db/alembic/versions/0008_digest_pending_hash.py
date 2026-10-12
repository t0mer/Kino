"""digests.pending_hash: fingerprint of the pending review set a digest reported

Revision ID: 0008_digest_pending_hash
Revises: 0007_jobs
Create Date: 2026-10-12 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_digest_pending_hash"
down_revision = "0007_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("digests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pending_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("digests", schema=None) as batch_op:
        batch_op.drop_column("pending_hash")
