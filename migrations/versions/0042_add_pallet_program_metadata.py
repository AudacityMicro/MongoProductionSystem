"""add parsed pallet program metadata

Revision ID: 0042_add_pallet_program_metadata
Revises: 0041_add_robot_reliability_runs
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0042_add_pallet_program_metadata"
down_revision = "0041_add_robot_reliability_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.add_column(sa.Column("program_tools_json", sa.String(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("expected_cycle_seconds", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("program_metadata_state", sa.String(length=20), nullable=False, server_default="unavailable"))
        batch_op.add_column(sa.Column("program_metadata_detail", sa.String(length=500), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("program_cycle_basis", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_column("program_cycle_basis")
        batch_op.drop_column("program_metadata_detail")
        batch_op.drop_column("program_metadata_state")
        batch_op.drop_column("expected_cycle_seconds")
        batch_op.drop_column("program_tools_json")
