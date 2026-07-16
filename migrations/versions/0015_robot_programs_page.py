"""add robot programs page settings

Revision ID: 0015_robot_programs_page
Revises: 0014_robot_program_extensions
Create Date: 2026-07-12 00:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_robot_programs_page"
down_revision = "0014_robot_program_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("robot_programs_page_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("robot_editor_command", sa.String(length=500), nullable=False, server_default="code"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("robot_editor_command")
        batch_op.drop_column("robot_programs_page_enabled")
