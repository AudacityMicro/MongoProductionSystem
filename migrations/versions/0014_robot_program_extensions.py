"""add robot program extension filter

Revision ID: 0014_robot_program_extensions
Revises: 0013_default_ur_sftp_credentials
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_robot_program_extensions"
down_revision = "0013_default_ur_sftp_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column("robot_program_extensions", sa.String(), nullable=False, server_default='[".urp"]')
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("robot_program_extensions")
