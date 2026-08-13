"""add queue-complete push-notification preference

Revision ID: 0056_queue_empty_notifications
Revises: 0055_sms_notifications
"""

from alembic import op
import sqlalchemy as sa


revision = "0056_queue_empty_notifications"
down_revision = "0055_sms_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("push_notify_queue_empty", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("app_settings", "push_notify_queue_empty")
