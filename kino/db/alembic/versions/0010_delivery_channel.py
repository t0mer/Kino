"""per-destination delivery channel: forwards/digests.channel, sent_ref, telegram_chats

Revision ID: 0010_delivery_channel
Revises: 0009_reference_outlier
Create Date: 2026-10-25 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_delivery_channel"
down_revision = "0009_reference_outlier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("forwards", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("channel", sa.String(), server_default="whatsapp", nullable=False)
        )
        batch_op.add_column(sa.Column("sent_ref", sa.String(), nullable=True))
        batch_op.drop_constraint("uq_forwards_image_dest", type_="unique")
        batch_op.create_unique_constraint(
            "uq_forwards_image_channel_dest", ["image_id", "channel", "dest_chat_id"]
        )
    op.execute(
        "UPDATE forwards SET sent_ref = openwa_message_id WHERE openwa_message_id IS NOT NULL"
    )
    with op.batch_alter_table("digests", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("channel", sa.String(), server_default="whatsapp", nullable=False)
        )
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), server_default="", nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_chats")
    with op.batch_alter_table("digests", schema=None) as batch_op:
        batch_op.drop_column("channel")
    # Telegram rows cannot satisfy the old (image, dest) uniqueness safely.
    op.execute("DELETE FROM forwards WHERE channel != 'whatsapp'")
    with op.batch_alter_table("forwards", schema=None) as batch_op:
        batch_op.drop_constraint("uq_forwards_image_channel_dest", type_="unique")
        batch_op.create_unique_constraint("uq_forwards_image_dest", ["image_id", "dest_chat_id"])
        batch_op.drop_column("sent_ref")
        batch_op.drop_column("channel")
