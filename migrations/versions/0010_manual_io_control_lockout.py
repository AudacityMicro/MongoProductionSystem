"""add manual io control lockout

Revision ID: 0010_manual_io_control_lockout
Revises: 0009_debug_io_labels
Create Date: 2026-07-11 17:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_manual_io_control_lockout"
down_revision = "0009_debug_io_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "manual_io_control_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("manual_io_control_enabled")
