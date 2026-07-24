"""add pallet location positions

Revision ID: 0022_add_pallet_location_positions
Revises: 0021_remove_manual_atc_tools
Create Date: 2026-07-12 03:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_add_pallet_location_positions"
down_revision = "0021_remove_manual_atc_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("pool_location_positions", sa.String(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("on_deck_location_position", sa.String(), nullable=False, server_default='{"x_mm":0,"y_mm":0,"z_mm":0}'))
        batch_op.add_column(sa.Column("dripping_location_position", sa.String(), nullable=False, server_default='{"x_mm":0,"y_mm":0,"z_mm":0}'))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("dripping_location_position")
        batch_op.drop_column("on_deck_location_position")
        batch_op.drop_column("pool_location_positions")
