"""add persistent non-blocking run mode alert

Revision ID: 0038_add_run_mode_alert
Revises: 0037_default_mill_files_to_gcode_directory
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_add_run_mode_alert"
down_revision = "0037_default_mill_files_to_gcode_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column(
            "run_mode_alert", sa.String(length=1000), nullable=False, server_default="",
        ))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("run_mode_alert")
