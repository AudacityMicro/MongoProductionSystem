"""add durable run mode start request

Revision ID: 0044_run_mode_start_request
Revises: 0043_return_pool_reservation
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0044_run_mode_start_request"
down_revision = "0043_return_pool_reservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("run_mode_start_request_id", sa.String(length=36), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("run_mode_start_request_id")
