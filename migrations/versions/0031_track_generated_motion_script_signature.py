"""track generated pallet-motion script configuration

Revision ID: 0031_track_generated_motion_script_signature
Revises: 0030_add_machine_loading_positions
Create Date: 2026-07-16 19:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_track_generated_motion_script_signature"
down_revision = "0030_add_machine_loading_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column("generated_motion_script_signature", sa.String(length=64), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("generated_motion_script_signature")
