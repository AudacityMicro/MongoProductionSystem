"""Add explicit robot connection mode."""

from alembic import op
import sqlalchemy as sa


revision = "0008_robot_connection_mode"
down_revision = "0007_debug_io_masks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "robot_connection_mode",
            sa.String(length=20),
            nullable=False,
            server_default="simulated",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "robot_connection_mode")
