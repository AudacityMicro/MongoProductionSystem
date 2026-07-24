"""add optional PathPilot A-axis homing interlock

Revision ID: 0033_add_cnc_a_axis_homing_setting
Revises: 0032_add_debug_mill_program_buttons
Create Date: 2026-07-18 13:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_add_cnc_a_axis_homing_setting"
down_revision = "0032_add_debug_mill_program_buttons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("cnc_require_a_axis_homed", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("cnc_require_a_axis_homed")
