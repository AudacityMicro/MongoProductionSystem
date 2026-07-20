import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentStatus = Literal["empty", "raw_stock", "complete_parts", "defective_parts"]
Location = Literal["pool", "on_deck", "machine", "dripping", "storage", "robot_held"]
WeightUnit = Literal["lb", "kg"]
DebugSignal = Literal["complete", "out_of_spec", "error"]
RobotConnectionMode = Literal["simulated", "physical"]


def _round_three_decimals(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


class PalletFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workholding: str = Field(min_length=1, max_length=250)
    weight_kg: float = Field(gt=0)
    content_status: ContentStatus
    program_path: str | None = Field(default=None, max_length=500)

    @field_validator("workholding")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CreatePallet(PalletFields):
    expected_revision: int = Field(ge=0)


class UpdatePallet(PalletFields):
    expected_revision: int = Field(ge=0)


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class CncTelemetryConnectionTest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(default="", max_length=500)
    timeout_seconds: float = Field(default=2.0, gt=0, le=15)

    @field_validator("host", "username")
    @classmethod
    def strip_connection_text(cls, value: str) -> str:
        return value.strip()


class ToggleDebugIo(RevisionRequest):
    direction: Literal["input", "output"]
    bank: Literal["standard", "configurable", "tool"]
    index: int = Field(ge=0, le=7)


class RenameDebugIo(RevisionRequest):
    direction: Literal["input", "output"]
    bank: Literal["standard", "configurable", "tool"]
    index: int = Field(ge=0, le=7)
    label: str = Field(default="", max_length=80)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()


DebugProgramColor = Literal["amber", "blue", "cyan", "green", "lime", "orange", "red", "violet"]


class ConfigureDebugProgram(RevisionRequest):
    index: int = Field(ge=0, le=15)
    display_name: str = Field(default="", max_length=80)
    filename: str = Field(default="", max_length=500)
    color: DebugProgramColor

    @field_validator("display_name", "filename")
    @classmethod
    def strip_program_text(cls, value: str) -> str:
        return value.strip()


class RunDebugProgram(RevisionRequest):
    index: int = Field(ge=0, le=15)


class ConfigureDebugMillProgram(ConfigureDebugProgram):
    pass


class RunDebugMillProgram(RunDebugProgram):
    pass


class RunDebugPalletMotion(RevisionRequest):
    operation: Literal["pick", "put"]
    pool_slot_number: int = Field(ge=1, le=256)


class RunDebugMillPalletMotion(RevisionRequest):
    operation: Literal["load", "unload"]


class MovePallet(RevisionRequest):
    destination: Location
    pool_slot_number: int | None = Field(default=None, ge=1)


class QueuePallet(RevisionRequest):
    queue_index: int | None = Field(default=None, ge=0)


class ReorderQueue(RevisionRequest):
    pallet_ids: list[str]


class CartesianLocation(BaseModel):
    x_mm: float = 0
    y_mm: float = 0
    z_mm: float = 0

    @field_validator("x_mm", "y_mm", "z_mm")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Location coordinates must be finite numbers.")
        return _round_three_decimals(value)


class MillG53Location(BaseModel):
    x_in: float = 0
    y_in: float = 0
    z_in: float = 0

    @field_validator("x_in", "y_in", "z_in")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("G53 coordinates must be finite numbers.")
        return value


class PoolLocation(CartesianLocation):
    slot: int = Field(ge=1, le=256)


class RobotOutputChannel(BaseModel):
    bank: Literal["standard", "configurable", "tool"]
    index: int = Field(ge=0, le=7)

    @field_validator("index")
    @classmethod
    def tool_channel_limit(cls, value: int, info) -> int:
        if info.data.get("bank") == "tool" and value > 1:
            raise ValueError("Tool output channels only allow 0 or 1.")
        return value


class RobotOutputAction(BaseModel):
    output: RobotOutputChannel
    active_value: bool = True
    pulse: bool = True


class PoolMotionProgram(BaseModel):
    slot: int = Field(ge=1, le=256)
    pick_program: str = Field(default="", max_length=500)
    put_program: str = Field(default="", max_length=500)

    @field_validator("pick_program", "put_program")
    @classmethod
    def strip_program(cls, value: str) -> str:
        return value.strip()


class RobotJointWaypoint(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    joints_rad: list[float] = Field(min_length=6, max_length=6)

    @field_validator("name")
    @classmethod
    def strip_joint_waypoint_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("joints_rad")
    @classmethod
    def finite_joint_positions(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(joint) for joint in value):
            raise ValueError("Joint waypoint values must be finite numbers.")
        return [_round_three_decimals(joint) for joint in value]


class RobotWaypoint(CartesianLocation):
    name: str = Field(min_length=1, max_length=80)
    rx_rad: float = 0
    ry_rad: float = 0
    rz_rad: float = 0

    @field_validator("name")
    @classmethod
    def strip_waypoint_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("rx_rad", "ry_rad", "rz_rad")
    @classmethod
    def finite_rotation(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Waypoint rotations must be finite numbers.")
        return _round_three_decimals(value)


class IntermediateSafePose(RobotJointWaypoint):
    pool_slots: list[int] = Field(default_factory=list, max_length=256)

    @field_validator("pool_slots")
    @classmethod
    def unique_pool_slots(cls, value: list[int]) -> list[int]:
        if any(slot < 1 or slot > 256 for slot in value):
            raise ValueError("Intermediate safe-pose pool assignments must be between 1 and 256.")
        if len(value) != len(set(value)):
            raise ValueError("Intermediate safe-pose pool assignments must be unique.")
        return sorted(value)


class PalletMotionGeneration(BaseModel):
    approach_y_clearance_mm: float = Field(default=100, ge=-1000, le=1000)
    mill_approach_x_clearance_mm: float = Field(default=100, ge=0, le=1000)
    lift_z_clearance_mm: float = Field(default=100, gt=0, le=1000)
    mill_lift_z_clearance_mm: float = Field(default=100, gt=0, le=1000)
    max_travel_speed_rad_s: float = Field(default=0.6, gt=0, le=3.0)
    pickup_setdown_speed_m_s: float = Field(default=0.08, gt=0, le=1.0)
    rx_rad: float = 0
    ry_rad: float = 0
    rz_rad: float = 0
    grip_output: RobotOutputChannel | None = None
    grip_closed_value: bool = True
    door_open_action: RobotOutputAction | None = None
    door_close_action: RobotOutputAction | None = None
    erowa_unlock_action: RobotOutputAction | None = None
    erowa_lock_action: RobotOutputAction | None = None
    mill_actuation_wait_seconds: float = Field(default=2.0, ge=0, le=30)
    mill_pre_entry_waypoint: RobotWaypoint | None = None
    safe_pre_waypoint: RobotJointWaypoint | None = None
    safe_post_waypoint: RobotJointWaypoint | None = None
    intermediate_safe_poses: list[IntermediateSafePose] = Field(default_factory=list, max_length=24)

    @field_validator("rx_rad", "ry_rad", "rz_rad")
    @classmethod
    def finite_orientation(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Pallet orientation values must be finite numbers.")
        return _round_three_decimals(value)


class StartPalletMotion(RevisionRequest):
    operation: Literal["pick", "put"]
    pool_slot_number: int = Field(ge=1, le=256)
    pallet_id: str | None = Field(default=None, max_length=36)


class StartMillPalletTransfer(RevisionRequest):
    """A scheduled, two-step robot transfer between a pool position and the mill."""

    operation: Literal["load", "unload"]
    pallet_id: str | None = Field(default=None, max_length=36)
    pool_slot_number: int | None = Field(default=None, ge=1, le=256)


class RecoverPalletMotion(RevisionRequest):
    resolution: Literal["source_pool", "robot_held", "destination_pool", "machine"]


class ClearRobotFault(RevisionRequest):
    confirmed: bool


class StartRunMode(RevisionRequest):
    safety_confirm: bool | None = None


class SetRunModeSafety(RevisionRequest):
    enabled: bool


class ConfirmRunModeAction(RevisionRequest):
    token: str = Field(min_length=36, max_length=36)
    approved: bool


class SettingsUpdate(RevisionRequest):
    source_folder: str | None = Field(default=None, max_length=1000)
    program_extensions: list[str] | None = Field(default=None, min_length=1)
    weight_unit: WeightUnit | None = None
    pool_slot_count: int | None = Field(default=None, ge=1, le=256)
    on_deck_enabled: bool | None = None
    dripping_enabled: bool | None = None
    run_mode_safety_confirm: bool | None = None
    pool_locations: list[PoolLocation] | None = None
    on_deck_location: CartesianLocation | None = None
    dripping_location: CartesianLocation | None = None
    robot_mill_load_unload: RobotWaypoint | None = None
    robot_mill_safe_entry_exit: RobotWaypoint | None = None
    mill_load_unload_g53: MillG53Location | None = None
    # Compatibility for the prior millimeter-based field while cached clients expire.
    mill_pallet_change_g53: CartesianLocation | None = None
    debug_menu_enabled: bool | None = None
    # Older cached Settings pages did not send this field. Keep it optional so
    # those pages cannot silently re-lock an operator's saved I/O permission.
    manual_io_control_enabled: bool | None = None
    robot_connection_mode: RobotConnectionMode | None = None
    robot_host: str | None = Field(default=None, max_length=255)
    robot_port: int | None = Field(default=None, ge=1, le=65535)
    robot_poll_hz: int | None = Field(default=None, ge=1, le=125)
    robot_timeout_seconds: float | None = Field(default=None, gt=0, le=10)
    robot_supervisor_enabled: bool | None = None
    robot_supervisor_hostname: str | None = Field(default=None, max_length=255)
    robot_supervisor_listen_host: str | None = Field(default=None, max_length=255)
    robot_supervisor_port: int | None = Field(default=None, ge=1, le=65535)
    robot_supervisor_heartbeat_seconds: float | None = Field(default=None, ge=0.25, le=10)
    robot_supervisor_telemetry_hz: float | None = Field(default=None, ge=0.25, le=10)
    robot_supervisor_reconnect_limit_seconds: float | None = Field(default=None, ge=1, le=60)
    robot_supervisor_pre_dispatch_fallback: bool | None = None
    debug_program_button_count: int | None = Field(default=None, ge=1, le=16)
    debug_mill_program_button_count: int | None = Field(default=None, ge=1, le=16)
    robot_file_access_enabled: bool | None = None
    robot_file_host: str | None = Field(default=None, max_length=255)
    robot_file_port: int | None = Field(default=None, ge=1, le=65535)
    robot_file_username: str | None = Field(default=None, max_length=255)
    robot_file_password: str | None = Field(default=None, max_length=500)
    robot_file_directory: str | None = Field(default=None, max_length=500)
    robot_program_extensions: list[str] | None = Field(default=None, min_length=1)
    robot_programs_page_enabled: bool | None = None
    robot_programs_filter_enabled: bool | None = None
    robot_editor_command: str | None = Field(default=None, max_length=500)
    fusion_tool_library_path: str | None = Field(default=None, max_length=1000)
    workholding_library: list[str] | None = Field(default=None, max_length=200)
    pallet_motion_enabled: bool | None = None
    pallet_motion_timeout_seconds: float | None = Field(default=None, gt=1, le=1800)
    pallet_motion_programs: list[PoolMotionProgram] | None = None
    pallet_motion_generation: PalletMotionGeneration | None = None
    cnc_telemetry_enabled: bool | None = None
    cnc_host: str | None = Field(default=None, max_length=255)
    cnc_ssh_port: int | None = Field(default=None, ge=1, le=65535)
    cnc_ssh_username: str | None = Field(default=None, max_length=255)
    cnc_ssh_password: str | None = Field(default=None, max_length=500)
    cnc_timeout_seconds: float | None = Field(default=None, gt=0, le=15)
    cnc_require_a_axis_homed: bool | None = None
    mill_file_directory: str | None = Field(default=None, max_length=500)
    mill_program_extensions: list[str] | None = Field(default=None, min_length=1)
    mill_programs_page_enabled: bool | None = None
    mill_programs_filter_enabled: bool | None = None
    mill_editor_command: str | None = Field(default=None, max_length=500)
    mill_results_archiving_enabled: bool | None = None
    mill_results_source_path: str | None = Field(default=None, max_length=500)
    mill_results_archive_directory: str | None = Field(default=None, max_length=500)

    @field_validator("robot_host", "robot_supervisor_hostname", "robot_supervisor_listen_host")
    @classmethod
    def strip_robot_host(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator(
        "robot_file_host", "robot_file_username", "robot_file_directory", "cnc_host", "cnc_ssh_username",
        "mill_file_directory", "mill_results_source_path", "mill_results_archive_directory",
    )
    @classmethod
    def strip_robot_file_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("robot_editor_command", "mill_editor_command")
    @classmethod
    def strip_robot_editor_command(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("fusion_tool_library_path")
    @classmethod
    def strip_fusion_tool_library_path(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("workholding_library")
    @classmethod
    def normalize_workholding_library(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        names: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = item.strip()
            if not name:
                continue
            if len(name) > 250:
                raise ValueError("Workholding names must be 250 characters or fewer.")
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
        return names


class SupervisorReconcile(RevisionRequest):
    sequence: int = Field(ge=1)
    resolution: Literal["accept_completed", "mark_faulted", "clear_latch"]


class SupervisorMaintenance(RevisionRequest):
    enabled: bool



class RobotFileAction(BaseModel):
    action: Literal["copy", "move", "rename", "delete", "create_folder", "open"]
    path: str = Field(default="", max_length=1000)
    destination_directory: str = Field(default="", max_length=1000)
    folder_name: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=500)
    conflict_strategy: Literal["prompt", "overwrite", "skip", "rename"] = "prompt"
