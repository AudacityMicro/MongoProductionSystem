"""add robot programs file filter setting

Revision ID: 0016_robot_programs_filter
Revises: 0015_robot_programs_page
Create Date: 2026-07-12 00:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_robot_programs_filter"
down_revision = "0015_robot_programs_page"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("robot_programs_filter_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("robot_programs_filter_enabled")
