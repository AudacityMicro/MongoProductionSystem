"""add Mongo stack light settings

Revision ID: 0050_stack_light
Revises: 0049_recovery_sessions
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0050_stack_light"
down_revision = "0049_recovery_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("stack_light_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column("stack_light_outputs", sa.String(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("app_settings", "stack_light_outputs")
    op.drop_column("app_settings", "stack_light_enabled")
