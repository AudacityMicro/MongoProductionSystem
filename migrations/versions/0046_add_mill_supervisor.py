"""add staged PathPilot mill supervisor

Revision ID: 0046_mill_supervisor
Revises: 0045_mill_status_file
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0046_mill_supervisor"
down_revision = "0045_mill_status_file"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("mill_supervisor_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("mill_supervisor_activation_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("mill_supervisor_hostname", sa.String(length=255), nullable=False, server_default="DESKTOP-KF5I73N.lan"))
        batch_op.add_column(sa.Column("mill_supervisor_listen_host", sa.String(length=255), nullable=False, server_default="0.0.0.0"))
        batch_op.add_column(sa.Column("mill_supervisor_port", sa.Integer(), nullable=False, server_default="50011"))
        batch_op.add_column(sa.Column("mill_supervisor_heartbeat_seconds", sa.Float(), nullable=False, server_default="5"))
        batch_op.add_column(sa.Column("mill_supervisor_telemetry_hz", sa.Float(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("mill_supervisor_last_sequence", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "mill_supervisor_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("mill_session", sa.Integer(), nullable=True),
        sa.Column("app_session", sa.Integer(), nullable=True),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("arguments_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("attempted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("sent_at", sa.String(length=40), nullable=True),
        sa.Column("accepted_at", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("result_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("fault_detail", sa.String(length=1000), nullable=True),
    )
    op.create_index("uq_mill_supervisor_sequence", "mill_supervisor_commands", ["sequence"], unique=True)
    op.create_index("ix_mill_supervisor_status", "mill_supervisor_commands", ["status"])


def downgrade() -> None:
    op.drop_index("ix_mill_supervisor_status", table_name="mill_supervisor_commands")
    op.drop_index("uq_mill_supervisor_sequence", table_name="mill_supervisor_commands")
    op.drop_table("mill_supervisor_commands")
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mill_supervisor_last_sequence")
        batch_op.drop_column("mill_supervisor_telemetry_hz")
        batch_op.drop_column("mill_supervisor_heartbeat_seconds")
        batch_op.drop_column("mill_supervisor_port")
        batch_op.drop_column("mill_supervisor_listen_host")
        batch_op.drop_column("mill_supervisor_hostname")
        batch_op.drop_column("mill_supervisor_activation_verified")
        batch_op.drop_column("mill_supervisor_enabled")
