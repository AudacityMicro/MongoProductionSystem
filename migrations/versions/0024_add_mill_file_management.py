"""Add mill program file-management settings."""

from alembic import op
import sqlalchemy as sa


revision = "0024_add_mill_file_management"
down_revision = "0023_add_cnc_telemetry_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("mill_file_directory", sa.String(length=500), nullable=False, server_default="/home/operator/gcode"))
        batch_op.add_column(sa.Column("mill_program_extensions", sa.String(), nullable=False, server_default='[".nc",".tap",".gcode",".cnc"]'))
        batch_op.add_column(sa.Column("mill_programs_page_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("mill_programs_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("mill_editor_command", sa.String(length=500), nullable=False, server_default="code"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mill_editor_command")
        batch_op.drop_column("mill_programs_filter_enabled")
        batch_op.drop_column("mill_programs_page_enabled")
        batch_op.drop_column("mill_program_extensions")
        batch_op.drop_column("mill_file_directory")
