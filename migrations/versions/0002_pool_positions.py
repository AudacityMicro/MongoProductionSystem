"""Replace storage slots with configurable pool positions."""

from alembic import op
import sqlalchemy as sa


revision = "0002_pool_positions"
down_revision = "0001_pallet_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "pool_slot_count",
            sa.Integer(),
            nullable=False,
            server_default="16",
        ),
    )
    op.add_column(
        "pallets",
        sa.Column("pool_slot_number", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE pallets
        SET pool_slot_number = (
            SELECT COUNT(*)
            FROM pallets AS earlier
            WHERE earlier.location = 'pool'
              AND earlier.rowid <= pallets.rowid
        )
        WHERE location = 'pool'
        """
    )
    op.execute(
        """
        UPDATE app_settings
        SET pool_slot_count = MAX(
            16,
            (SELECT COUNT(*) FROM pallets WHERE location = 'pool')
        )
        WHERE id = 1
        """
    )
    op.execute(
        "UPDATE pallets SET storage_slot_id = NULL WHERE location = 'storage'"
    )
    op.create_index(
        "uq_pallet_pool_slot",
        "pallets",
        ["pool_slot_number"],
        unique=True,
        sqlite_where=sa.text("location = 'pool'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pallet_pool_slot", table_name="pallets")
    op.drop_column("pallets", "pool_slot_number")
    op.drop_column("app_settings", "pool_slot_count")

