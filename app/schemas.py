from typing import Literal

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentStatus = Literal["empty", "raw_stock", "complete_parts", "defective_parts"]
Location = Literal["pool", "on_deck", "machine", "dripping", "storage"]
WeightUnit = Literal["lb", "kg"]
DebugSignal = Literal["complete", "out_of_spec", "error"]
RobotConnectionMode = Literal["simulated", "physical"]


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
        return value


class PoolLocation(CartesianLocation):
    slot: int = Field(ge=1, le=256)


class SettingsUpdate(RevisionRequest):
    source_folder: str = Field(max_length=1000)
    program_extensions: list[str] = Field(min_length=1)
    weight_unit: WeightUnit
    pool_slot_count: int = Field(ge=1, le=256)
    pool_locations: list[PoolLocation] | None = None
    on_deck_location: CartesianLocation | None = None
    dripping_location: CartesianLocation | None = None
    debug_menu_enabled: bool = False
    # Older cached Settings pages did not send this field. Keep it optional so
    # those pages cannot silently re-lock an operator's saved I/O permission.
    manual_io_control_enabled: bool | None = None
    robot_connection_mode: RobotConnectionMode = "simulated"
    robot_host: str = Field(default="", max_length=255)
    robot_port: int = Field(default=30004, ge=1, le=65535)
    robot_poll_hz: int = Field(default=10, ge=1, le=125)
    robot_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    debug_program_button_count: int | None = Field(default=None, ge=1, le=16)
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
    cnc_telemetry_enabled: bool | None = None
    cnc_host: str | None = Field(default=None, max_length=255)
    cnc_ssh_port: int | None = Field(default=None, ge=1, le=65535)
    cnc_ssh_username: str | None = Field(default=None, max_length=255)
    cnc_ssh_password: str | None = Field(default=None, max_length=500)
    cnc_timeout_seconds: float | None = Field(default=None, gt=0, le=15)
    mill_file_directory: str | None = Field(default=None, max_length=500)
    mill_program_extensions: list[str] | None = Field(default=None, min_length=1)
    mill_programs_page_enabled: bool | None = None
    mill_programs_filter_enabled: bool | None = None
    mill_editor_command: str | None = Field(default=None, max_length=500)

    @field_validator("robot_host")
    @classmethod
    def strip_robot_host(cls, value: str) -> str:
        return value.strip()

    @field_validator("robot_file_host", "robot_file_username", "robot_file_directory", "cnc_host", "cnc_ssh_username", "mill_file_directory")
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



class RobotFileAction(BaseModel):
    action: Literal["copy", "move", "rename", "delete", "create_folder", "open"]
    path: str = Field(default="", max_length=1000)
    destination_directory: str = Field(default="", max_length=1000)
    folder_name: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=500)
    conflict_strategy: Literal["prompt", "overwrite", "skip", "rename"] = "prompt"
