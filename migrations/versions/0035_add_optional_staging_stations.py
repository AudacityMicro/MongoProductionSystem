"""add optional on-deck and dripping stations

Revision ID: 0035_add_optional_staging_stations
Revises: 0034_add_run_mode
Create Date: 2026-07-18 15:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_add_optional_staging_stations"
down_revision = "0034_add_run_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("on_deck_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("dripping_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("dripping_enabled")
        batch_op.drop_column("on_deck_enabled")
