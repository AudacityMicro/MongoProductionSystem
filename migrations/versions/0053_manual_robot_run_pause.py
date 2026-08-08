"""pause run mode for manual robot control

Revision ID: 0053_manual_robot_run_pause
Revises: 0052_program_tool_counts
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0053_manual_robot_run_pause"
down_revision = "0052_program_tool_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("run_mode_manual_robot_pause", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("app_settings", "run_mode_manual_robot_pause")
