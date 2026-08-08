"""store measured machining durations for program estimates

Revision ID: 0054_program_runtime_history
Revises: 0053_manual_robot_run_pause
Create Date: 2026-08-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0054_program_runtime_history"
down_revision = "0053_manual_robot_run_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("run_mode_program_started_at", sa.String(length=40), nullable=True))
    op.create_table(
        "program_runtimes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("program_path", sa.String(length=500), nullable=False),
        sa.Column("pallet_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("duration_seconds > 0", name="ck_program_runtime_duration_positive"),
    )
    op.create_index("ix_program_runtime_program_completed", "program_runtimes", ["program_path", "completed_at"])


def downgrade() -> None:
    op.drop_index("ix_program_runtime_program_completed", table_name="program_runtimes")
    op.drop_table("program_runtimes")
    op.drop_column("app_settings", "run_mode_program_started_at")
