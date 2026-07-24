"""Add the reusable workholding library setting."""

from alembic import op
import sqlalchemy as sa


revision = "0025_add_workholding_library"
down_revision = "0024_add_mill_file_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(
            sa.Column("workholding_library", sa.String(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("workholding_library")
