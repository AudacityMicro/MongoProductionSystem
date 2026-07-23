"""add persisted production run mode

Revision ID: 0034_add_run_mode
Revises: 0033_add_cnc_a_axis_homing_setting
Create Date: 2026-07-18 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_add_run_mode"
down_revision = "0033_add_cnc_a_axis_homing_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("run_mode_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("run_mode_safety_confirm", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("run_mode_state", sa.String(length=30), nullable=False, server_default="idle"))
        batch_op.add_column(sa.Column("run_mode_detail", sa.String(length=1000), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("run_mode_current_pallet_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("run_mode_return_slot", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("run_mode_pending_action", sa.String(length=30), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("run_mode_confirmation_token", sa.String(length=36), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("run_mode_confirmation_granted", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("run_mode_confirmation_granted")
        batch_op.drop_column("run_mode_confirmation_token")
        batch_op.drop_column("run_mode_pending_action")
        batch_op.drop_column("run_mode_return_slot")
        batch_op.drop_column("run_mode_current_pallet_id")
        batch_op.drop_column("run_mode_detail")
        batch_op.drop_column("run_mode_state")
        batch_op.drop_column("run_mode_safety_confirm")
        batch_op.drop_column("run_mode_enabled")
