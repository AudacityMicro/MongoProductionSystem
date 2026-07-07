"""Remove legacy named storage slots."""

from alembic import op
import sqlalchemy as sa


revision = "0003_remove_storage_slots"
down_revision = "0002_pool_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_column("storage_slot_id")
    op.drop_table("storage_slots")


def downgrade() -> None:
    op.create_table(
        "storage_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
    )
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.add_column(
            sa.Column("storage_slot_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_pallets_storage_slot_id_storage_slots",
            "storage_slots",
            ["storage_slot_id"],
            ["id"],
            ondelete="RESTRICT",
        )

