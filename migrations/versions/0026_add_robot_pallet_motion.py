"""Add persisted robot pallet-motion settings and history."""

from alembic import op
import sqlalchemy as sa


revision = "0026_add_robot_pallet_motion"
down_revision = "0025_add_workholding_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("pallet_motion_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("pallet_motion_timeout_seconds", sa.Float(), nullable=False, server_default="120"))
        batch_op.add_column(sa.Column("pallet_motion_signals", sa.String(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("pallet_motion_programs", sa.String(), nullable=False, server_default="[]"))
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint(
            "ck_pallet_location",
            "location IN ('pool','on_deck','machine','dripping','storage','robot_held')",
        )
    op.create_index(
        "uq_single_robot_held_pallet",
        "pallets",
        ["location"],
        unique=True,
        sqlite_where=sa.text("location = 'robot_held'"),
    )
    op.create_table(
        "robot_motions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("pallet_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("source_slot", sa.Integer(), nullable=True),
        sa.Column("destination_slot", sa.Integer(), nullable=True),
        sa.Column("program_path", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_busy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("failure_detail", sa.String(length=1000), nullable=True),
    )
    op.create_index("ix_robot_motions_pallet_id", "robot_motions", ["pallet_id"])
    op.create_index("uq_active_robot_motion", "robot_motions", ["status"], unique=True, sqlite_where=sa.text("status IN ('requested','running','faulted')"))


def downgrade() -> None:
    op.drop_index("uq_single_robot_held_pallet", table_name="pallets")
    op.drop_index("uq_active_robot_motion", table_name="robot_motions")
    op.drop_index("ix_robot_motions_pallet_id", table_name="robot_motions")
    op.drop_table("robot_motions")
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("pallet_motion_programs")
        batch_op.drop_column("pallet_motion_signals")
        batch_op.drop_column("pallet_motion_timeout_seconds")
        batch_op.drop_column("pallet_motion_enabled")
    with op.batch_alter_table("pallets") as batch_op:
        batch_op.drop_constraint("ck_pallet_location", type_="check")
        batch_op.create_check_constraint(
            "ck_pallet_location",
            "location IN ('pool','on_deck','machine','dripping','storage')",
        )
