"""add PathPilot results archiving settings

Revision ID: 0036_add_mill_results_archiving
Revises: 0035_add_optional_staging_stations
Create Date: 2026-07-18 18:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0036_add_mill_results_archiving"
down_revision = "0035_add_optional_staging_stations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("mill_results_archiving_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column(
            "mill_results_source_path", sa.String(length=500), nullable=False,
            server_default="/home/operator/gcode/RESULTS.TXT",
        ))
        batch_op.add_column(sa.Column(
            "mill_results_archive_directory", sa.String(length=500), nullable=False,
            server_default="/home/operator/gcode/Results",
        ))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mill_results_archive_directory")
        batch_op.drop_column("mill_results_source_path")
        batch_op.drop_column("mill_results_archiving_enabled")
