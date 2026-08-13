"""add background stack-light intensity setting

Revision ID: 0057_background_stack_light_intensity
Revises: 0056_queue_empty_notifications
"""

from alembic import op
import sqlalchemy as sa


revision = "0057_background_stack_light_intensity"
down_revision = "0056_queue_empty_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("background_stack_light_intensity", sa.Integer(), nullable=False, server_default="65"),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "background_stack_light_intensity")
