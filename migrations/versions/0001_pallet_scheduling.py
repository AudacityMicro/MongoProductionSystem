"""Create pallet scheduling schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_pallet_scheduling"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_folder", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "program_extensions",
            sa.String(),
            nullable=False,
            server_default='[".nc",".tap",".gcode",".cnc",".urp"]',
        ),
        sa.Column("weight_unit", sa.String(), nullable=False, server_default="lb"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "storage_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
    )
    op.create_table(
        "pallets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("workholding", sa.String(250), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("content_status", sa.String(30), nullable=False),
        sa.Column("program_path", sa.String(500), nullable=True),
        sa.Column("location", sa.String(20), nullable=False),
        sa.Column("queue_position", sa.Integer(), nullable=True),
        sa.Column(
            "storage_slot_id",
            sa.Integer(),
            sa.ForeignKey("storage_slots.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        sa.CheckConstraint("weight_kg > 0", name="ck_pallet_weight_positive"),
        sa.CheckConstraint(
            "content_status IN ('empty','raw_stock','complete_parts','defective_parts')",
            name="ck_pallet_content_status",
        ),
        sa.CheckConstraint(
            "location IN ('pool','queue','machine','storage')",
            name="ck_pallet_location",
        ),
    )
    op.create_index(
        "uq_pallet_queue_position",
        "pallets",
        ["queue_position"],
        unique=True,
        sqlite_where=sa.text("location = 'queue'"),
    )
    op.create_index(
        "uq_single_machine_pallet",
        "pallets",
        ["location"],
        unique=True,
        sqlite_where=sa.text("location = 'machine'"),
    )
    op.execute(
        "INSERT INTO app_settings "
        "(id, source_folder, program_extensions, weight_unit, revision) "
        "VALUES (1, '', '[\".nc\",\".tap\",\".gcode\",\".cnc\",\".urp\"]', 'lb', 0)"
    )


def downgrade() -> None:
    op.drop_index("uq_single_machine_pallet", table_name="pallets")
    op.drop_index("uq_pallet_queue_position", table_name="pallets")
    op.drop_table("pallets")
    op.drop_table("storage_slots")
    op.drop_table("app_settings")

