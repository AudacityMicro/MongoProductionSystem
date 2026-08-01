"""add pallet program work coordinate systems

Revision ID: 0047_pallet_program_wcs
Revises: 0046_mill_supervisor
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0047_pallet_program_wcs"
down_revision = "0046_mill_supervisor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.add_column(sa.Column("program_wcs_json", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_column("program_wcs_json")
