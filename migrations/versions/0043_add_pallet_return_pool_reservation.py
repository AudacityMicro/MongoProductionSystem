"""add pallet return pool reservation

Revision ID: 0043_return_pool_reservation
Revises: 0042_add_pallet_program_metadata
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0043_return_pool_reservation"
down_revision = "0042_add_pallet_program_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.add_column(sa.Column("return_pool_slot_number", sa.Integer(), nullable=True))

    # Preserve the most recent known source position for a pallet already in
    # transit when this version is installed.
    op.execute(
        """
        UPDATE pallets
        SET return_pool_slot_number = (
            SELECT robot_motions.source_slot
            FROM robot_motions
            WHERE robot_motions.pallet_id = pallets.id
              AND robot_motions.source_slot IS NOT NULL
            ORDER BY robot_motions.created_at DESC
            LIMIT 1
        )
        WHERE location IN ('machine', 'robot_held')
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_column("return_pool_slot_number")
