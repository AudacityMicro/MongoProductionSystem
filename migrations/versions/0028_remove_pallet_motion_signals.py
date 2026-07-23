"""Remove physical pallet-motion status outputs."""

from alembic import op
import sqlalchemy as sa


revision = "0028_remove_pallet_motion_signals"
down_revision = "0027_add_robot_script_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("pallet_motion_signals")


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("pallet_motion_signals", sa.String(), nullable=False, server_default="{}"))
