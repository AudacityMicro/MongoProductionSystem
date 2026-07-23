"""add persisted robot queue reliability runs

Revision ID: 0041_add_robot_reliability_runs
Revises: 0040_add_robot_supervisor
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_add_robot_reliability_runs"
down_revision = "0040_add_robot_supervisor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "robot_reliability_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
        sa.Column("queue_snapshot", sa.String(), nullable=False, server_default="[]"),
        sa.Column("total_pallets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_pallets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_index", sa.Integer(), nullable=True),
        sa.Column("current_pallet_id", sa.String(length=36), nullable=True),
        sa.Column("current_pallet_name", sa.String(length=100), nullable=True),
        sa.Column("current_pool_slot", sa.Integer(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("failure_detail", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_active_robot_reliability_run",
        "robot_reliability_runs",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status IN ('requested','running')"),
    )
    op.create_index(
        "ix_robot_reliability_created_at",
        "robot_reliability_runs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_robot_reliability_created_at", table_name="robot_reliability_runs")
    op.drop_index("uq_active_robot_reliability_run", table_name="robot_reliability_runs")
    op.drop_table("robot_reliability_runs")
