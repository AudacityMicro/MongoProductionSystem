"""add persistent production runtime dashboard metrics

Revision ID: 0060_production_runtime_metrics
Revises: 0059_stack_light_state_colors
"""

from alembic import op
import sqlalchemy as sa


revision = "0060_production_runtime_metrics"
down_revision = "0059_stack_light_state_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_runtime_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_mode", sa.String(length=20), nullable=False, server_default="idle"),
        sa.Column("last_updated_at", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("non_idle_started_at", sa.String(length=40), nullable=True),
        sa.Column("non_idle_record_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("alarm_free_run_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("alarm_free_run_record_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("production_runtime_metrics")
