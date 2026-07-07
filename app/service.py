from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import AppSettings, Pallet
from app.pallet_names import PALLET_NAMES
from app.schemas import (
    CreatePallet,
    MovePallet,
    QueuePallet,
    ReorderQueue,
    SettingsUpdate,
    UpdatePallet,
)


def problem(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def get_settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, 1)
    if not settings:
        raise RuntimeError("Database settings row is missing")
    return settings


def check_revision(settings: AppSettings, expected: int) -> None:
    if settings.revision != expected:
        raise problem(409, "The board changed in another session. Refresh and retry.")


def normalize_extensions(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        extension = value.strip().lower()
        if not extension.startswith("."):
            extension = f".{extension}"
        if len(extension) < 2 or any(char in extension for char in r"\/:*?\"<>|"):
            raise problem(422, f"Invalid program extension: {value}")
        if extension not in normalized:
            normalized.append(extension)
    return normalized


def available_programs(settings: AppSettings) -> tuple[list[str], str | None]:
    if not settings.source_folder.strip():
        return [], "No program source folder is configured."
    root = Path(settings.source_folder).expanduser()
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return [], "The configured program source folder is unavailable."
    if not root.is_dir():
        return [], "The configured program source path is not a folder."

    extensions = set(json.loads(settings.program_extensions))
    programs: list[str] = []
    try:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in extensions:
                resolved = path.resolve()
                if resolved.is_relative_to(root):
                    programs.append(resolved.relative_to(root).as_posix())
    except OSError:
        return [], "The program source folder could not be read completely."
    return sorted(programs, key=str.casefold), None


def validate_program(program_path: str | None, programs: set[str]) -> str | None:
    if not program_path:
        return None
    normalized = Path(program_path.replace("\\", "/")).as_posix()
    if normalized.startswith("../") or Path(normalized).is_absolute():
        raise problem(422, "Program path must be relative to the source folder.")
    if normalized not in programs:
        raise problem(422, "Selected program is not available in the source folder.")
    return normalized


def serialize_pallet(pallet: Pallet) -> dict:
    return {
        "id": pallet.id,
        "name": pallet.name,
        "workholding": pallet.workholding,
        "weight_kg": pallet.weight_kg,
        "content_status": pallet.content_status,
        "program_path": pallet.program_path,
        "location": pallet.location,
        "queue_position": pallet.queue_position,
        "pool_slot_number": pallet.pool_slot_number,
    }


def board_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    programs, warning = available_programs(settings)
    return {
        "revision": settings.revision,
        "pallets": [serialize_pallet(item) for item in pallets],
        "settings": {
            "source_folder": settings.source_folder,
            "program_extensions": json.loads(settings.program_extensions),
            "weight_unit": settings.weight_unit,
            "pool_slot_count": settings.pool_slot_count,
            "debug_menu_enabled": settings.debug_menu_enabled,
            "machine_state": settings.machine_state,
        },
        "programs": programs,
        "program_warning": warning,
    }


def bump(settings: AppSettings) -> None:
    settings.revision += 1


def commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except (IntegrityError, StaleDataError) as exc:
        session.rollback()
        raise problem(409, "That change conflicts with the current board state.") from exc


def first_open_pool_slot(
    session: Session,
    settings: AppSettings,
    exclude_id: str | None = None,
) -> int:
    occupied = set(
        session.scalars(
            select(Pallet.pool_slot_number).where(
                Pallet.location == "pool",
                Pallet.id != exclude_id,
            )
        ).all()
    )
    for number in range(1, settings.pool_slot_count + 1):
        if number not in occupied:
            return number
    raise problem(409, "The pallet pool is full.")


def next_pallet_name(session: Session) -> str:
    used_names = {
        name.casefold() for name in session.scalars(select(Pallet.name)).all()
    }
    for name in PALLET_NAMES:
        if name.casefold() not in used_names:
            return name
    raise problem(409, "All configured pallet names are currently in use.")


def create_pallet(session: Session, payload: CreatePallet) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not math.isfinite(payload.weight_kg):
        raise problem(422, "Weight must be a finite positive number.")
    programs, _ = available_programs(settings)
    pallet = Pallet(
        id=str(uuid4()),
        name=next_pallet_name(session),
        location="pool",
        queue_position=None,
        pool_slot_number=first_open_pool_slot(session, settings),
        **payload.model_dump(exclude={"expected_revision", "program_path"}),
        program_path=validate_program(payload.program_path, set(programs)),
    )
    session.add(pallet)
    bump(settings)
    commit_or_conflict(session)


def update_pallet(session: Session, pallet_id: str, payload: UpdatePallet) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if not math.isfinite(payload.weight_kg):
        raise problem(422, "Weight must be a finite positive number.")
    programs, _ = available_programs(settings)
    values = payload.model_dump(exclude={"expected_revision", "program_path"})
    for key, value in values.items():
        setattr(pallet, key, value)
    pallet.program_path = validate_program(payload.program_path, set(programs))
    bump(settings)
    commit_or_conflict(session)


def duplicate_pallet(session: Session, pallet_id: str, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    source = session.get(Pallet, pallet_id)
    if not source:
        raise problem(404, "Pallet not found.")
    session.add(
        Pallet(
            id=str(uuid4()),
            name=next_pallet_name(session),
            workholding=source.workholding,
            weight_kg=source.weight_kg,
            content_status=source.content_status,
            program_path=source.program_path,
            location="pool",
            pool_slot_number=first_open_pool_slot(session, settings),
        )
    )
    bump(settings)
    commit_or_conflict(session)


def compact_queue(session: Session, exclude_id: str | None = None) -> None:
    queue = session.scalars(
        select(Pallet)
        .where(Pallet.queue_position.is_not(None), Pallet.id != exclude_id)
        .order_by(Pallet.queue_position)
    ).all()
    # Clear first to avoid transient collisions with the unique partial index.
    for pallet in queue:
        pallet.queue_position = None
    session.flush()
    for position, pallet in enumerate(queue):
        pallet.queue_position = position


def move_pallet(session: Session, pallet_id: str, payload: MovePallet) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")

    if payload.destination == "machine":
        occupant = session.scalar(
            select(Pallet).where(Pallet.location == "machine", Pallet.id != pallet_id)
        )
        if occupant:
            raise problem(409, f"Machine is occupied by {occupant.name}.")
        settings.machine_state = "running"
    if payload.destination == "pool":
        pool_slot = payload.pool_slot_number or first_open_pool_slot(
            session,
            settings,
            pallet_id,
        )
        if pool_slot > settings.pool_slot_count:
            raise problem(422, "Pool position is outside the configured range.")
        occupant = session.scalar(
            select(Pallet).where(
                Pallet.location == "pool",
                Pallet.pool_slot_number == pool_slot,
                Pallet.id != pallet_id,
            )
        )
        if occupant:
            raise problem(409, f"Pool position {pool_slot} is occupied by {occupant.name}.")
    elif payload.pool_slot_number is not None:
        raise problem(422, "Pool position is only valid for a pool destination.")

    was_queued = pallet.queue_position is not None
    if was_queued and payload.destination != "pool":
        pallet.queue_position = None
        session.flush()
        compact_queue(session, pallet.id)

    if pallet.location == "machine" and payload.destination != "machine":
        settings.machine_state = "idle"
    pallet.location = payload.destination
    pallet.pool_slot_number = pool_slot if payload.destination == "pool" else None

    bump(settings)
    commit_or_conflict(session)


def queue_pallet(
    session: Session,
    pallet_id: str,
    payload: QueuePallet,
) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if pallet.location == "machine":
        pallet.location = "pool"
        pallet.pool_slot_number = first_open_pool_slot(session, settings)
    elif pallet.location != "pool":
        raise problem(
            409,
            "A stored pallet must be returned to the Pool before it can be queued.",
        )

    queue = session.scalars(
        select(Pallet)
        .where(Pallet.queue_position.is_not(None), Pallet.id != pallet_id)
        .order_by(Pallet.queue_position)
    ).all()
    index = payload.queue_index if payload.queue_index is not None else len(queue)
    index = min(index, len(queue))
    for item in queue:
        item.queue_position = None
    pallet.queue_position = None
    session.flush()
    queue.insert(index, pallet)
    for position, item in enumerate(queue):
        item.queue_position = position
    bump(settings)
    commit_or_conflict(session)


def dequeue_pallet(
    session: Session,
    pallet_id: str,
    expected_revision: int,
) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if pallet.queue_position is None:
        raise problem(409, "Pallet is not in the Queue.")
    pallet.queue_position = None
    session.flush()
    compact_queue(session, pallet.id)
    bump(settings)
    commit_or_conflict(session)


def reorder_queue(session: Session, payload: ReorderQueue) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    queue = session.scalars(
        select(Pallet).where(Pallet.queue_position.is_not(None))
    ).all()
    if len(payload.pallet_ids) != len(set(payload.pallet_ids)):
        raise problem(422, "Queue contains duplicate pallet IDs.")
    if set(payload.pallet_ids) != {item.id for item in queue}:
        raise problem(422, "Queue reorder must contain every queued pallet exactly once.")
    by_id = {item.id: item for item in queue}
    for item in queue:
        item.queue_position = None
    session.flush()
    for position, pallet_id in enumerate(payload.pallet_ids):
        by_id[pallet_id].queue_position = position
    bump(settings)
    commit_or_conflict(session)


def delete_pallet(session: Session, pallet_id: str, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    was_queued = pallet.queue_position is not None
    session.delete(pallet)
    session.flush()
    if was_queued:
        compact_queue(session)
    bump(settings)
    commit_or_conflict(session)


def reconcile_programs(session: Session, settings: AppSettings) -> list[str]:
    programs, _ = available_programs(settings)
    available = set(programs)
    cleared: list[str] = []
    assigned = session.scalars(
        select(Pallet).where(Pallet.program_path.is_not(None))
    ).all()
    for pallet in assigned:
        if pallet.program_path not in available:
            pallet.program_path = None
            cleared.append(pallet.name)
    return cleared


def update_settings(session: Session, payload: SettingsUpdate) -> list[str]:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    highest_occupied = session.scalar(
        select(Pallet.pool_slot_number)
        .where(Pallet.location == "pool")
        .order_by(Pallet.pool_slot_number.desc())
        .limit(1)
    )
    if highest_occupied and payload.pool_slot_count < highest_occupied:
        raise problem(
            409,
            f"Pool position {highest_occupied} is occupied. Move that pallet before reducing capacity.",
        )

    settings.source_folder = payload.source_folder.strip()
    settings.program_extensions = json.dumps(
        normalize_extensions(payload.program_extensions),
        separators=(",", ":"),
    )
    settings.weight_unit = payload.weight_unit
    settings.pool_slot_count = payload.pool_slot_count
    settings.debug_menu_enabled = payload.debug_menu_enabled
    if not payload.debug_menu_enabled:
        settings.machine_state = "idle"
    cleared = reconcile_programs(session, settings)
    bump(settings)
    commit_or_conflict(session)
    return cleared


def refresh_programs(session: Session, expected_revision: int) -> list[str]:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    cleared = reconcile_programs(session, settings)
    if cleared:
        bump(settings)
        commit_or_conflict(session)
    return cleared


def simulate_signal(
    session: Session,
    signal: str,
    expected_revision: int,
) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if not settings.debug_menu_enabled:
        raise problem(403, "Debug simulation is disabled in Settings.")

    if signal == "error":
        settings.machine_state = "error"
    elif signal in {"complete", "out_of_spec"}:
        pallet = session.scalar(
            select(Pallet).where(Pallet.location == "machine")
        )
        if not pallet:
            raise problem(409, "No pallet is currently in the Mill.")
        pallet.location = "pool"
        pallet.pool_slot_number = first_open_pool_slot(session, settings)
        pallet.content_status = (
            "complete_parts" if signal == "complete" else "defective_parts"
        )
        settings.machine_state = "idle"
    else:
        raise problem(422, "Unknown debug signal.")

    bump(settings)
    commit_or_conflict(session)
