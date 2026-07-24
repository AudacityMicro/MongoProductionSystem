"""add on deck and dripping pallet locations

Revision ID: 0017_add_on_deck_and_dripping
Revises: 0016_robot_programs_filter
Create Date: 2026-07-12 01:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_add_on_deck_and_dripping"
down_revision = "0016_robot_programs_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint(
            "ck_pallet_location",
            "location IN ('pool','on_deck','machine','dripping','storage')",
        )
        batch_op.create_index(
            "uq_single_on_deck_pallet",
            ["location"],
            unique=True,
            sqlite_where=sa.text("location = 'on_deck'"),
        )
        batch_op.create_index(
            "uq_single_dripping_pallet",
            ["location"],
            unique=True,
            sqlite_where=sa.text("location = 'dripping'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_index("uq_single_dripping_pallet")
        batch_op.drop_index("uq_single_on_deck_pallet")
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint("ck_pallet_location", "location IN ('pool','machine','storage')")
