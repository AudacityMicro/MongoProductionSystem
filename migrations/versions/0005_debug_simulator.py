"""Add debug simulator settings and machine state."""

from alembic import op
import sqlalchemy as sa


revision = "0005_debug_simulator"
down_revision = "0004_virtual_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "debug_menu_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "machine_state",
            sa.String(20),
            nullable=False,
            server_default="idle",
        ),
    )
    op.execute(
        """
        UPDATE app_settings
        SET machine_state = 'running'
        WHERE EXISTS (
            SELECT 1 FROM pallets WHERE location = 'machine'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("app_settings", "machine_state")
    op.drop_column("app_settings", "debug_menu_enabled")
