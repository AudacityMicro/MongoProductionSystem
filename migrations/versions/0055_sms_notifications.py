"""add configurable ntfy push notifications

Revision ID: 0055_sms_notifications
Revises: 0054_program_runtime_history
"""

from alembic import op
import sqlalchemy as sa


revision = "0055_sms_notifications"
down_revision = "0054_program_runtime_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("push_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column("push_notification_server", sa.String(length=500), nullable=False, server_default="https://ntfy.sh"))
    op.add_column("app_settings", sa.Column("push_notification_topic", sa.String(length=200), nullable=False, server_default=""))
    op.add_column("app_settings", sa.Column("push_notification_token", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("app_settings", sa.Column("push_notify_errors", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("app_settings", sa.Column("push_notify_completed_pallets", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for column in ("push_notify_completed_pallets", "push_notify_errors", "push_notification_token", "push_notification_topic", "push_notification_server", "push_notifications_enabled"):
        op.drop_column("app_settings", column)
