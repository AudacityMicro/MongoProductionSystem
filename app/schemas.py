from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentStatus = Literal["empty", "raw_stock", "complete_parts", "defective_parts"]
Location = Literal["pool", "machine", "storage"]
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


class MovePallet(RevisionRequest):
    destination: Location
    pool_slot_number: int | None = Field(default=None, ge=1)


class QueuePallet(RevisionRequest):
    queue_index: int | None = Field(default=None, ge=0)


class ReorderQueue(RevisionRequest):
    pallet_ids: list[str]


class SettingsUpdate(RevisionRequest):
    source_folder: str = Field(max_length=1000)
    program_extensions: list[str] = Field(min_length=1)
    weight_unit: WeightUnit
    pool_slot_count: int = Field(ge=1, le=256)
    debug_menu_enabled: bool = False
    robot_connection_mode: RobotConnectionMode = "simulated"
    robot_host: str = Field(default="", max_length=255)
    robot_port: int = Field(default=30004, ge=1, le=65535)
    robot_poll_hz: int = Field(default=10, ge=1, le=125)
    robot_timeout_seconds: float = Field(default=1.0, gt=0, le=10)

    @field_validator("robot_host")
    @classmethod
    def strip_robot_host(cls, value: str) -> str:
        return value.strip()
