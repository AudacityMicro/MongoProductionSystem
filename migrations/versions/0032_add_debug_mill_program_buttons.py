"""add configured mill debug program buttons

Revision ID: 0032_add_debug_mill_program_buttons
Revises: 0031_track_generated_motion_script_signature
Create Date: 2026-07-18 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_add_debug_mill_program_buttons"
down_revision = "0031_track_generated_motion_script_signature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("debug_mill_program_button_count", sa.Integer(), nullable=False, server_default="4"))
        batch_op.add_column(sa.Column("debug_mill_program_buttons", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("debug_mill_program_buttons")
        batch_op.drop_column("debug_mill_program_button_count")
