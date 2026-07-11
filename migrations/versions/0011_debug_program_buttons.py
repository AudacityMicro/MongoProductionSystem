"""add debug controller program buttons

Revision ID: 0011_debug_program_buttons
Revises: 0010_manual_io_control_lockout
Create Date: 2026-07-11 22:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_debug_program_buttons"
down_revision = "0010_manual_io_control_lockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("debug_program_button_count", sa.Integer(), nullable=False, server_default="4"))
        batch_op.add_column(sa.Column("debug_program_buttons", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("debug_program_buttons")
        batch_op.drop_column("debug_program_button_count")
