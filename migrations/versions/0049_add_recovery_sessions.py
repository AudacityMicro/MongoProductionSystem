"""add durable guided recovery sessions

Revision ID: 0049_recovery_sessions
Revises: 0048_add_cameras
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0049_recovery_sessions"
down_revision = "0048_add_cameras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recovery_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="awaiting_safety"),
        sa.Column("step", sa.String(length=50), nullable=False, server_default="safety"),
        sa.Column("answers_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("faults_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("actions_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("message", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "uq_active_recovery_session",
        "recovery_sessions",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status IN ('awaiting_safety','running','awaiting_restart','ready','handoff')"),
    )
    op.create_index("ix_recovery_sessions_updated_at", "recovery_sessions", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_recovery_sessions_updated_at", table_name="recovery_sessions")
    op.drop_index("uq_active_recovery_session", table_name="recovery_sessions")
    op.drop_table("recovery_sessions")
