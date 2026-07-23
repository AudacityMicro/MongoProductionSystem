"""add uploaded Fusion tool libraries

Revision ID: 0020_add_uploaded_fusion_tool_libraries
Revises: 0019_add_fusion_tool_library_path
Create Date: 2026-07-12 02:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_add_uploaded_fusion_tool_libraries"
down_revision = "0019_add_fusion_tool_library_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("fusion_tool_library_paths", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("fusion_tool_library_paths")
