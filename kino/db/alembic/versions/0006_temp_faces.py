"""temp_faces (enrollment uploads, TTL 1 h) and reference_faces.yaw (coverage meter)

Revision ID: 0006_temp_faces
Revises: 0005_image_last_enqueued_at
Create Date: 2026-10-11 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_temp_faces"
down_revision = "0005_image_last_enqueued_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "temp_faces",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("upload_id", sa.String(length=32), nullable=False),
        sa.Column("kid_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("bbox_x", sa.Float(), nullable=False),
        sa.Column("bbox_y", sa.Float(), nullable=False),
        sa.Column("bbox_w", sa.Float(), nullable=False),
        sa.Column("bbox_h", sa.Float(), nullable=False),
        sa.Column("size_px", sa.Float(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("blur_score", sa.Float(), nullable=True),
        sa.Column("tier", sa.String(length=4), nullable=False),
        sa.Column("yaw", sa.Float(), nullable=True),
        sa.Column("pitch", sa.Float(), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("crop_path", sa.String(), nullable=True),
        sa.Column("photo_date", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kid_id"], ["kids.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("temp_faces", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_temp_faces_upload_id"), ["upload_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_temp_faces_kid_id"), ["kid_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_temp_faces_created_at"), ["created_at"], unique=False)
    with op.batch_alter_table("reference_faces", schema=None) as batch_op:
        batch_op.add_column(sa.Column("yaw", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reference_faces", schema=None) as batch_op:
        batch_op.drop_column("yaw")
    op.drop_table("temp_faces")
