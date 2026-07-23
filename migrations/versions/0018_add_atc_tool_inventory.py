"""add ATC tool inventory

Revision ID: 0018_add_atc_tool_inventory
Revises: 0017_add_on_deck_and_dripping
Create Date: 2026-07-12 01:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_add_atc_tool_inventory"
down_revision = "0017_add_on_deck_and_dripping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("atc_tools", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("atc_tools")
