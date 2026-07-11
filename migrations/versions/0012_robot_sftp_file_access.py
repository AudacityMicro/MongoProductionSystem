"""add robot sftp file access settings

Revision ID: 0012_robot_sftp_file_access
Revises: 0011_debug_program_buttons
Create Date: 2026-07-11 23:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_robot_sftp_file_access"
down_revision = "0011_debug_program_buttons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("robot_file_access_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("robot_file_host", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("robot_file_port", sa.Integer(), nullable=False, server_default="22"))
        batch_op.add_column(sa.Column("robot_file_username", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("robot_file_password", sa.String(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("robot_file_directory", sa.String(length=500), nullable=False, server_default="/programs"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("robot_file_directory")
        batch_op.drop_column("robot_file_password")
        batch_op.drop_column("robot_file_username")
        batch_op.drop_column("robot_file_port")
        batch_op.drop_column("robot_file_host")
        batch_op.drop_column("robot_file_access_enabled")
