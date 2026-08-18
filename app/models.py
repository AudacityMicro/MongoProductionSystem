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
    robot_port: Mapped[int] = mapped_column(Integer, default=30003)
    robot_poll_hz: Mapped[int] = mapped_column(Integer, default=10)
    robot_timeout_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    robot_supervisor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    robot_supervisor_activation_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    robot_supervisor_hostname: Mapped[str] = mapped_column(String(255), default="DESKTOP-KF5I73N.lan")
    robot_supervisor_listen_host: Mapped[str] = mapped_column(String(255), default="0.0.0.0")
    robot_supervisor_port: Mapped[int] = mapped_column(Integer, default=50010)
    robot_supervisor_heartbeat_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    robot_supervisor_telemetry_hz: Mapped[float] = mapped_column(Float, default=2.0)
    robot_supervisor_reconnect_limit_seconds: Mapped[float] = mapped_column(Float, default=10.0)
    robot_supervisor_pre_dispatch_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    robot_supervisor_maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    robot_supervisor_last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    debug_standard_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_configurable_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_tool_input_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_standard_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_configurable_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_tool_output_mask: Mapped[int] = mapped_column(Integer, default=0)
    debug_io_labels: Mapped[str] = mapped_column(String, default="{}")
    stack_light_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stack_light_outputs: Mapped[str] = mapped_column(String, default="{}")
    stack_light_state_colors: Mapped[str] = mapped_column(String, default="{}")
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
    mill_supervisor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mill_supervisor_activation_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mill_supervisor_hostname: Mapped[str] = mapped_column(String(255), default="DESKTOP-KF5I73N.lan")
    mill_supervisor_listen_host: Mapped[str] = mapped_column(String(255), default="0.0.0.0")
    mill_supervisor_port: Mapped[int] = mapped_column(Integer, default=50011)
    mill_supervisor_heartbeat_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    mill_supervisor_telemetry_hz: Mapped[float] = mapped_column(Float, default=1.0)
    mill_supervisor_last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    run_mode_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    run_mode_safety_confirm: Mapped[bool] = mapped_column(Boolean, default=True)
    run_mode_state: Mapped[str] = mapped_column(String(30), default="idle")
    run_mode_detail: Mapped[str] = mapped_column(String(1000), default="")
    run_mode_alert: Mapped[str] = mapped_column(String(1000), default="")
    run_mode_current_pallet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_mode_return_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_mode_pending_action: Mapped[str] = mapped_column(String(30), default="")
    run_mode_confirmation_token: Mapped[str] = mapped_column(String(36), default="")
    run_mode_confirmation_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    run_mode_start_request_id: Mapped[str] = mapped_column(String(36), default="")
    run_mode_loaded_machine_action: Mapped[str] = mapped_column(String(30), default="")
    run_mode_manual_robot_pause: Mapped[bool] = mapped_column(Boolean, default=False)
    run_mode_program_started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    push_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    push_notification_server: Mapped[str] = mapped_column(String(500), default="https://ntfy.sh")
    push_notification_topic: Mapped[str] = mapped_column(String(200), default="")
    push_notification_token: Mapped[str] = mapped_column(String(500), default="")
    push_notify_errors: Mapped[bool] = mapped_column(Boolean, default=True)
    push_notify_completed_pallets: Mapped[bool] = mapped_column(Boolean, default=True)
    push_notify_queue_empty: Mapped[bool] = mapped_column(Boolean, default=True)
    background_stack_light_intensity: Mapped[int] = mapped_column(Integer, default=65)
    mill_file_directory: Mapped[str] = mapped_column(String(500), default="/home/operator/gcode/Gcode")
    mill_program_extensions: Mapped[str] = mapped_column(String, default='[".nc",".tap",".gcode",".cnc"]')
    mill_programs_page_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mill_programs_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mill_editor_command: Mapped[str] = mapped_column(String(500), default="code")
    mill_status_file_path: Mapped[str] = mapped_column(
        String(500), default="/home/operator/gcode/MongoProduction/mill-status.txt"
    )
    camera_devices_json: Mapped[str] = mapped_column(String, default="[]")
    camera_idle_id: Mapped[str] = mapped_column(String(100), default="")
    camera_loading_id: Mapped[str] = mapped_column(String(100), default="")
    camera_machining_id: Mapped[str] = mapped_column(String(100), default="")
    camera_recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    camera_recording_path: Mapped[str] = mapped_column(String(1000), default="data/camera-recordings")
    camera_recording_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    camera_width: Mapped[int] = mapped_column(Integer, default=1920)
    camera_height: Mapped[int] = mapped_column(Integer, default=1080)
    camera_fps: Mapped[int] = mapped_column(Integer, default=30)
    camera_segment_seconds: Mapped[int] = mapped_column(Integer, default=300)
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


class ProductionRuntimeMetrics(Base):
    __tablename__ = "production_runtime_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_mode: Mapped[str] = mapped_column(String(20), default="idle")
    last_updated_at: Mapped[str] = mapped_column(String(40), default="")
    non_idle_started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    non_idle_record_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    alarm_free_run_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    alarm_free_run_record_seconds: Mapped[float] = mapped_column(Float, default=0.0)


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
    program_tools_json: Mapped[str] = mapped_column(String, default="[]")
    program_tool_counts_json: Mapped[str] = mapped_column(String, default="{}")
    program_wcs_json: Mapped[str] = mapped_column(String, default="[]")
    expected_cycle_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    program_metadata_state: Mapped[str] = mapped_column(String(20), default="unavailable")
    program_metadata_detail: Mapped[str] = mapped_column(String(500), default="")
    program_cycle_basis: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str] = mapped_column(String(20), default="pool")
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool_slot_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_pool_slot_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ProgramCompletionStat(Base):
    """Durable production totals, keyed by the assigned mill program."""

    __tablename__ = "program_completion_stats"

    program_path: Mapped[str] = mapped_column(String(500), primary_key=True)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("completed_count >= 0", name="ck_program_completion_count_nonnegative"),
    )


class ProgramRuntime(Base):
    """One confirmed machining duration, retained for future cycle estimates."""

    __tablename__ = "program_runtimes"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="ck_program_runtime_duration_positive"),
        Index("ix_program_runtime_program_completed", "program_path", "completed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    program_path: Mapped[str] = mapped_column(String(500), nullable=False)
    pallet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    completed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


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


class RobotSupervisorCommand(Base):
    __tablename__ = "robot_supervisor_commands"
    __table_args__ = (
        Index("uq_robot_supervisor_sequence", "sequence", unique=True),
        Index("ix_robot_supervisor_motion_id", "robot_motion_id"),
        Index("ix_robot_supervisor_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    robot_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    robot_motion_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    opcode: Mapped[int] = mapped_column(Integer, nullable=False)
    argument: Mapped[int] = mapped_column(Integer, default=0)
    value: Mapped[int] = mapped_column(Integer, default=0)
    payload_g: Mapped[int] = mapped_column(Integer, default=0)
    transport: Mapped[str] = mapped_column(String(20), default="supervisor")
    status: Mapped[str] = mapped_column(String(20), default="created")
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    sent_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    accepted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class MillSupervisorCommand(Base):
    __tablename__ = "mill_supervisor_commands"
    __table_args__ = (
        Index("uq_mill_supervisor_sequence", "sequence", unique=True),
        Index("ix_mill_supervisor_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    mill_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    arguments_json: Mapped[str] = mapped_column(String, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="created")
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    sent_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    accepted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_json: Mapped[str] = mapped_column(String, default="{}")
    fault_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class RobotReliabilityRun(Base):
    __tablename__ = "robot_reliability_runs"
    __table_args__ = (
        Index(
            "uq_active_robot_reliability_run",
            "status",
            unique=True,
            sqlite_where=text("status IN ('requested','running')"),
        ),
        Index("ix_robot_reliability_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="requested")
    queue_snapshot: Mapped[str] = mapped_column(String, default="[]")
    total_pallets: Mapped[int] = mapped_column(Integer, default=0)
    completed_pallets: Mapped[int] = mapped_column(Integer, default=0)
    current_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_pallet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_pallet_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_pool_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class RecoverySession(Base):
    __tablename__ = "recovery_sessions"
    __table_args__ = (
        Index(
            "uq_active_recovery_session",
            "status",
            unique=True,
            sqlite_where=text("status IN ('awaiting_safety','running','awaiting_restart','ready','handoff')"),
        ),
        Index("ix_recovery_sessions_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_safety")
    step: Mapped[str] = mapped_column(String(50), default="safety")
    answers_json: Mapped[str] = mapped_column(String, default="{}")
    faults_json: Mapped[str] = mapped_column(String, default="[]")
    actions_json: Mapped[str] = mapped_column(String, default="[]")
    message: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
