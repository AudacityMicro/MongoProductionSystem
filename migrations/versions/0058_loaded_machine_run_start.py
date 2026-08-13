"""persist an explicit loaded-pallet Run Mode start choice

Revision ID: 0058_loaded_machine_run_start
Revises: 0057_background_stack_light_intensity
"""

from alembic import op
import sqlalchemy as sa


revision = "0058_loaded_machine_run_start"
down_revision = "0057_background_stack_light_intensity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("run_mode_loaded_machine_action", sa.String(length=30), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("app_settings", "run_mode_loaded_machine_action")
