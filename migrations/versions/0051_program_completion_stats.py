"""add per-program completion totals

Revision ID: 0051_program_completion_stats
Revises: 0050_stack_light
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0051_program_completion_stats"
down_revision = "0050_stack_light"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "program_completion_stats",
        sa.Column("program_path", sa.String(length=500), primary_key=True),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("completed_count >= 0", name="ck_program_completion_count_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("program_completion_stats")
