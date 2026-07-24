"""add robot-originated supervisor settings and command ledger

Revision ID: 0040_add_robot_supervisor
Revises: 0039_restore_pathpilot_results_paths
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_add_robot_supervisor"
down_revision = "0039_restore_pathpilot_results_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("robot_supervisor_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("robot_supervisor_activation_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("robot_supervisor_hostname", sa.String(length=255), nullable=False, server_default="DESKTOP-KF5I73N.lan"))
        batch.add_column(sa.Column("robot_supervisor_listen_host", sa.String(length=255), nullable=False, server_default="0.0.0.0"))
        batch.add_column(sa.Column("robot_supervisor_port", sa.Integer(), nullable=False, server_default="50010"))
        batch.add_column(sa.Column("robot_supervisor_heartbeat_seconds", sa.Float(), nullable=False, server_default="1.0"))
        batch.add_column(sa.Column("robot_supervisor_telemetry_hz", sa.Float(), nullable=False, server_default="2.0"))
        batch.add_column(sa.Column("robot_supervisor_reconnect_limit_seconds", sa.Float(), nullable=False, server_default="10.0"))
        batch.add_column(sa.Column("robot_supervisor_pre_dispatch_fallback", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("robot_supervisor_maintenance_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("robot_supervisor_last_sequence", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "robot_supervisor_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("robot_session", sa.Integer(), nullable=True),
        sa.Column("app_session", sa.Integer(), nullable=True),
        sa.Column("robot_motion_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("opcode", sa.Integer(), nullable=False),
        sa.Column("argument", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_g", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transport", sa.String(length=20), nullable=False, server_default="supervisor"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("attempted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("sent_at", sa.String(length=40), nullable=True),
        sa.Column("accepted_at", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("result_code", sa.Integer(), nullable=True),
        sa.Column("fault_detail", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_robot_supervisor_sequence", "robot_supervisor_commands", ["sequence"], unique=True)
    op.create_index("ix_robot_supervisor_motion_id", "robot_supervisor_commands", ["robot_motion_id"], unique=False)
    op.create_index("ix_robot_supervisor_status", "robot_supervisor_commands", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_robot_supervisor_status", table_name="robot_supervisor_commands")
    op.drop_index("ix_robot_supervisor_motion_id", table_name="robot_supervisor_commands")
    op.drop_index("uq_robot_supervisor_sequence", table_name="robot_supervisor_commands")
    op.drop_table("robot_supervisor_commands")
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("robot_supervisor_last_sequence")
        batch.drop_column("robot_supervisor_maintenance_mode")
        batch.drop_column("robot_supervisor_pre_dispatch_fallback")
        batch.drop_column("robot_supervisor_reconnect_limit_seconds")
        batch.drop_column("robot_supervisor_telemetry_hz")
        batch.drop_column("robot_supervisor_heartbeat_seconds")
        batch.drop_column("robot_supervisor_port")
        batch.drop_column("robot_supervisor_listen_host")
        batch.drop_column("robot_supervisor_hostname")
        batch.drop_column("robot_supervisor_activation_verified")
        batch.drop_column("robot_supervisor_enabled")
