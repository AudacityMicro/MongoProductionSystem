"""Add persistent simulated debug I/O masks."""

from alembic import op
import sqlalchemy as sa


revision = "0007_debug_io_masks"
down_revision = "0006_robot_rtde_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column_name in (
        "debug_standard_input_mask",
        "debug_configurable_input_mask",
        "debug_tool_input_mask",
        "debug_standard_output_mask",
        "debug_configurable_output_mask",
        "debug_tool_output_mask",
    ):
        op.add_column(
            "app_settings",
            sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for column_name in (
        "debug_tool_output_mask",
        "debug_configurable_output_mask",
        "debug_standard_output_mask",
        "debug_tool_input_mask",
        "debug_configurable_input_mask",
        "debug_standard_input_mask",
    ):
        op.drop_column("app_settings", column_name)
