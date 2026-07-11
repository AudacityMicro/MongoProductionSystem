"""set Universal Robots SFTP defaults

Revision ID: 0013_default_ur_sftp_credentials
Revises: 0012_robot_sftp_file_access
Create Date: 2026-07-11 23:30:00
"""

from alembic import op


revision = "0013_default_ur_sftp_credentials"
down_revision = "0012_robot_sftp_file_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE app_settings SET robot_file_username = 'root' WHERE robot_file_username = ''")
    op.execute("UPDATE app_settings SET robot_file_password = 'easybot' WHERE robot_file_password = ''")


def downgrade() -> None:
    pass
