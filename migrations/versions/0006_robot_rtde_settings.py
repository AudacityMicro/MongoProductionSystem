"""Add robot RTDE telemetry settings."""

from alembic import op
import sqlalchemy as sa


revision = "0006_robot_rtde_settings"
down_revision = "0005_debug_simulator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("robot_host", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "app_settings",
        sa.Column("robot_port", sa.Integer(), nullable=False, server_default="30004"),
    )
    op.add_column(
        "app_settings",
        sa.Column("robot_poll_hz", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "robot_timeout_seconds",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "robot_timeout_seconds")
    op.drop_column("app_settings", "robot_poll_hz")
    op.drop_column("app_settings", "robot_port")
    op.drop_column("app_settings", "robot_host")
