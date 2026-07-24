"""add Fusion tool library path

Revision ID: 0019_add_fusion_tool_library_path
Revises: 0018_add_atc_tool_inventory
Create Date: 2026-07-12 02:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_add_fusion_tool_library_path"
down_revision = "0018_add_atc_tool_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("fusion_tool_library_path", sa.String(length=1000), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("fusion_tool_library_path")
