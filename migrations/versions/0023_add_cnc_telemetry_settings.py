"""Add CNC telemetry connection settings."""

from alembic import op
import sqlalchemy as sa


revision = "0023_add_cnc_telemetry_settings"
down_revision = "0022_add_pallet_location_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("cnc_telemetry_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("cnc_host", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cnc_ssh_port", sa.Integer(), nullable=False, server_default="22"))
        batch_op.add_column(sa.Column("cnc_ssh_username", sa.String(length=255), nullable=False, server_default="operator"))
        batch_op.add_column(sa.Column("cnc_ssh_password", sa.String(length=500), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cnc_timeout_seconds", sa.Float(), nullable=False, server_default="2"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("cnc_timeout_seconds")
        batch_op.drop_column("cnc_ssh_password")
        batch_op.drop_column("cnc_ssh_username")
        batch_op.drop_column("cnc_ssh_port")
        batch_op.drop_column("cnc_host")
        batch_op.drop_column("cnc_telemetry_enabled")
