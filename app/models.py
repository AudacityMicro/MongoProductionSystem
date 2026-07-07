from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Float, Index, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    source_folder: Mapped[str] = mapped_column(String, default="")
    program_extensions: Mapped[str] = mapped_column(
        String,
        default='[".nc",".tap",".gcode",".cnc",".urp"]',
    )
    weight_unit: Mapped[str] = mapped_column(String, default="lb")
    pool_slot_count: Mapped[int] = mapped_column(Integer, default=16)
    debug_menu_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    machine_state: Mapped[str] = mapped_column(String(20), default="idle")
    robot_connection_mode: Mapped[str] = mapped_column(String(20), default="simulated")
    robot_host: Mapped[str] = mapped_column(String(255), default="")
    robot_port: Mapped[int] = mapped_column(Integer, default=30004)
    robot_poll_hz: Mapped[int] = mapped_column(Integer, default=10)
    robot_timeout_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    debug_standard_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_configurable_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_tool_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_standard_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_configurable_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_tool_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_io_labels: Mapped[str] = mapped_column(String, default="{}")
    revision: Mapped[int] = mapped_column(Integer, default=0)

    __mapper_args__ = {
        "version_id_col": revision,
        "version_id_generator": False,
    }


class Pallet(Base):
    __tablename__ = "pallets"
    __table_args__ = (
        CheckConstraint("weight_kg > 0", name="ck_pallet_weight_positive"),
        CheckConstraint(
            "content_status IN ('empty','raw_stock','complete_parts','defective_parts')",
            name="ck_pallet_content_status",
        ),
        CheckConstraint(
            "location IN ('pool','machine','storage')",
            name="ck_pallet_location",
        ),
        Index(
            "uq_pallet_queue_position",
            "queue_position",
            unique=True,
            sqlite_where=text("queue_position IS NOT NULL"),
        ),
        Index(
            "uq_single_machine_pallet",
            "location",
            unique=True,
            sqlite_where=text("location = 'machine'"),
        ),
        Index(
            "uq_pallet_pool_slot",
            "pool_slot_number",
            unique=True,
            sqlite_where=text("location = 'pool'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    workholding: Mapped[str] = mapped_column(String(250))
    weight_kg: Mapped[float] = mapped_column(Float)
    content_status: Mapped[str] = mapped_column(String(30))
    program_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location: Mapped[str] = mapped_column(String(20), default="pool")
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool_slot_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
