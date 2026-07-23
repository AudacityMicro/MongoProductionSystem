"""remove manual ATC tool inventory

Revision ID: 0021_remove_manual_atc_tools
Revises: 0020_add_uploaded_fusion_tool_libraries
Create Date: 2026-07-12 03:00:00
"""

from alembic import op


revision = "0021_remove_manual_atc_tools"
down_revision = "0020_add_uploaded_fusion_tool_libraries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("atc_tools")


def downgrade() -> None:
    import sqlalchemy as sa

    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("atc_tools", sa.String(), nullable=False, server_default="[]"))
