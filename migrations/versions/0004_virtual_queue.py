"""Separate virtual queue membership from physical location."""

from alembic import op
import sqlalchemy as sa


revision = "0004_virtual_queue"
down_revision = "0003_remove_storage_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    settings = connection.execute(
        sa.text("SELECT pool_slot_count FROM app_settings WHERE id = 1")
    ).one()
    pool_slot_count = settings.pool_slot_count
    occupied = {
        row.pool_slot_number
        for row in connection.execute(
            sa.text(
                "SELECT pool_slot_number FROM pallets "
                "WHERE location = 'pool' AND pool_slot_number IS NOT NULL"
            )
        )
    }
    queued = connection.execute(
        sa.text(
            "SELECT id FROM pallets WHERE location = 'queue' "
            "ORDER BY queue_position"
        )
    ).all()

    next_slot = 1
    for row in queued:
        while next_slot in occupied:
            next_slot += 1
        if next_slot > pool_slot_count:
            pool_slot_count = next_slot
        connection.execute(
            sa.text(
                "UPDATE pallets SET location = 'pool', pool_slot_number = :slot "
                "WHERE id = :pallet_id"
            ),
            {"slot": next_slot, "pallet_id": row.id},
        )
        occupied.add(next_slot)

    connection.execute(
        sa.text(
            "UPDATE app_settings SET pool_slot_count = :count WHERE id = 1"
        ),
        {"count": pool_slot_count},
    )

    op.drop_index("uq_pallet_queue_position", table_name="pallets")
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint(
            "ck_pallet_location",
            "location IN ('pool','machine','storage')",
        )
    op.create_index(
        "uq_pallet_queue_position",
        "pallets",
        ["queue_position"],
        unique=True,
        sqlite_where=sa.text("queue_position IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_pallet_queue_position", table_name="pallets")
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint(
            "ck_pallet_location",
            "location IN ('pool','queue','machine','storage')",
        )
    op.create_index(
        "uq_pallet_queue_position",
        "pallets",
        ["queue_position"],
        unique=True,
        sqlite_where=sa.text("location = 'queue'"),
    )

