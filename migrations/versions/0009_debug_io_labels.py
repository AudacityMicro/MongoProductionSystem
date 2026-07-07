"""add persistent debug io labels

Revision ID: 0009_debug_io_labels
Revises: 0008_robot_connection_mode
Create Date: 2026-07-07 13:25:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_debug_io_labels"
down_revision = "0008_robot_connection_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "debug_io_labels",
                sa.String(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("debug_io_labels")
