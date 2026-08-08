"""store per-program tool-use counts

Revision ID: 0052_program_tool_counts
Revises: 0051_program_completion_stats
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0052_program_tool_counts"
down_revision = "0051_program_completion_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pallets", sa.Column("program_tool_counts_json", sa.String(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("pallets", "program_tool_counts_json")
