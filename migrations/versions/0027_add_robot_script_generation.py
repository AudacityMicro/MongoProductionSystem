"""Add generated robot-script configuration."""

from alembic import op
import sqlalchemy as sa


revision = "0027_add_robot_script_generation"
down_revision = "0026_add_robot_pallet_motion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("pallet_motion_generation", sa.String(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("pallet_motion_generation")
