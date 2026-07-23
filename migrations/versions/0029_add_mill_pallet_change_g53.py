"""add mill pallet change G53 position

Revision ID: 0029_add_mill_pallet_change_g53
Revises: 0028_remove_pallet_motion_signals
Create Date: 2026-07-16 17:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_add_mill_pallet_change_g53"
down_revision = "0028_remove_pallet_motion_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "mill_pallet_change_g53_position",
                sa.String(),
                nullable=False,
                server_default='{"x_mm":0,"y_mm":0,"z_mm":0}',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mill_pallet_change_g53_position")
