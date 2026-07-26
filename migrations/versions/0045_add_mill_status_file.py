"""add dedicated mill completion status file

Revision ID: 0045_mill_status_file
Revises: 0044_run_mode_start_request
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0045_mill_status_file"
down_revision = "0044_run_mode_start_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column(
            "mill_status_file_path", sa.String(length=500), nullable=False,
            server_default="/home/operator/gcode/MongoProduction/mill-status.txt",
        ))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mill_status_file_path")
