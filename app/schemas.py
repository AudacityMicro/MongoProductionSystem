from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentStatus = Literal["empty", "raw_stock", "complete_parts", "defective_parts"]
Location = Literal["pool", "machine", "storage"]
WeightUnit = Literal["lb", "kg"]
DebugSignal = Literal["complete", "out_of_spec", "error"]


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
