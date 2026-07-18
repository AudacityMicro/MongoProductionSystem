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
    on_deck_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    dripping_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    debug_menu_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_io_control_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
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
    debug_program_button_count: Mapped[int] = mapped_column(Integer, default=4)
    debug_program_buttons: Mapped[str] = mapped_column(String, default="[]")
    debug_mill_program_button_count: Mapped[int] = mapped_column(Integer, default=4)
    debug_mill_program_buttons: Mapped[str] = mapped_column(String, default="[]")
    robot_file_access_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    robot_file_host: Mapped[str] = mapped_column(String(255), default="")
    robot_file_port: Mapped[int] = mapped_column(Integer, default=22)
    robot_file_username: Mapped[str] = mapped_column(String(255), default="root")
    robot_file_password: Mapped[str] = mapped_column(String, default="easybot")
    robot_file_directory: Mapped[str] = mapped_column(String(500), default="/programs")
    robot_program_extensions: Mapped[str] = mapped_column(String, default='[".urp"]')
    robot_programs_page_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    robot_programs_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    robot_editor_command: Mapped[str] = mapped_column(String(500), default="code")
    fusion_tool_library_path: Mapped[str] = mapped_column(String(1000), default="")
    fusion_tool_library_paths: Mapped[str] = mapped_column(String, default="[]")
    workholding_library: Mapped[str] = mapped_column(String, default="[]")
    pallet_motion_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pallet_motion_timeout_seconds: Mapped[float] = mapped_column(Float, default=120.0)
    pallet_motion_programs: Mapped[str] = mapped_column(String, default="[]")
    pallet_motion_generation: Mapped[str] = mapped_column(String, default="{}")
    generated_motion_script_signature: Mapped[str] = mapped_column(String(64), default="")
    cnc_telemetry_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cnc_host: Mapped[str] = mapped_column(String(255), default="")
    cnc_ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    cnc_ssh_username: Mapped[str] = mapped_column(String(255), default="operator")
    cnc_ssh_password: Mapped[str] = mapped_column(String(500), default="")
    cnc_timeout_seconds: Mapped[float] = mapped_column(Float, default=2.0)
    cnc_require_a_axis_homed: Mapped[bool] = mapped_column(Boolean, default=False)
    run_mode_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    run_mode_safety_confirm: Mapped[bool] = mapped_column(Boolean, default=True)
    run_mode_state: Mapped[str] = mapped_column(String(30), default="idle")
    run_mode_detail: Mapped[str] = mapped_column(String(1000), default="")
    run_mode_current_pallet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_mode_return_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_mode_pending_action: Mapped[str] = mapped_column(String(30), default="")
    run_mode_confirmation_token: Mapped[str] = mapped_column(String(36), default="")
    run_mode_confirmation_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    mill_file_directory: Mapped[str] = mapped_column(String(500), default="/home/operator/gcode")
    mill_program_extensions: Mapped[str] = mapped_column(String, default='[".nc",".tap",".gcode",".cnc"]')
    mill_programs_page_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mill_programs_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mill_editor_command: Mapped[str] = mapped_column(String(500), default="code")
    pool_location_positions: Mapped[str] = mapped_column(String, default="[]")
    on_deck_location_position: Mapped[str] = mapped_column(String, default='{"x_mm":0,"y_mm":0,"z_mm":0}')
    dripping_location_position: Mapped[str] = mapped_column(String, default='{"x_mm":0,"y_mm":0,"z_mm":0}')
    mill_pallet_change_g53_position: Mapped[str] = mapped_column(String, default='{"x_mm":0,"y_mm":0,"z_mm":0}')
    robot_mill_load_unload_position: Mapped[str] = mapped_column(String, default="{}")
    robot_mill_safe_entry_exit_position: Mapped[str] = mapped_column(String, default="{}")
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
            "location IN ('pool','on_deck','machine','dripping','storage','robot_held')",
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
            "uq_single_on_deck_pallet",
            "location",
            unique=True,
            sqlite_where=text("location = 'on_deck'"),
        ),
        Index(
            "uq_single_dripping_pallet",
            "location",
            unique=True,
            sqlite_where=text("location = 'dripping'"),
        ),
        Index(
            "uq_single_robot_held_pallet",
            "location",
            unique=True,
            sqlite_where=text("location = 'robot_held'"),
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


class RobotMotion(Base):
    __tablename__ = "robot_motions"
    __table_args__ = (
        Index(
            "uq_active_robot_motion",
            "status",
            unique=True,
            sqlite_where=text("status IN ('requested','running','faulted')"),
        ),
        Index("ix_robot_motions_pallet_id", "pallet_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pallet_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    source_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    program_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    observed_busy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
