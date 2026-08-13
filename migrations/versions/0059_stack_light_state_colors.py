"""add configurable stack-light colors by system state

Revision ID: 0059_stack_light_state_colors
Revises: 0058_loaded_machine_run_start
"""

from alembic import op
import sqlalchemy as sa


revision = "0059_stack_light_state_colors"
down_revision = "0058_loaded_machine_run_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("stack_light_state_colors", sa.String(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "stack_light_state_colors")
