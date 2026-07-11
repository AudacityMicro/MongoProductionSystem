from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import AppSettings, Pallet
from app.pallet_names import PALLET_NAMES
from app.robot_rtde import RobotTelemetryError, read_robot_snapshot, toggle_robot_digital_output
from app.schemas import (
    CreatePallet,
    MovePallet,
    QueuePallet,
    RenameDebugIo,
    ReorderQueue,
    SettingsUpdate,
    ToggleDebugIo,
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
            "manual_io_control_enabled": settings.manual_io_control_enabled,
            "machine_state": settings.machine_state,
            "robot_connection_mode": settings.robot_connection_mode,
            "robot_host": settings.robot_host,
            "robot_port": settings.robot_port,
            "robot_poll_hz": settings.robot_poll_hz,
            "robot_timeout_seconds": settings.robot_timeout_seconds,
        },
        "programs": programs,
        "program_warning": warning,
    }


def _board_summary(settings: AppSettings, pallets: list[Pallet]) -> dict:
    machine_pallet = next((item for item in pallets if item.location == "machine"), None)
    queue_count = sum(1 for item in pallets if item.queue_position is not None)
    pool_count = sum(1 for item in pallets if item.location == "pool")
    storage_count = sum(1 for item in pallets if item.location == "storage")
    return {
        "machine_pallet": machine_pallet.name if machine_pallet else None,
        "queue_count": queue_count,
        "pool_count": pool_count,
        "storage_count": storage_count,
        "pool_open_positions": max(settings.pool_slot_count - pool_count, 0),
    }


def _bit_value(mask: int, index: int) -> bool:
    return bool((mask >> index) & 1)


def _debug_io_label_key(direction: str, bank: str, index: int) -> str:
    return f"{direction}:{bank}:{index}"


def _load_debug_io_labels(settings: AppSettings) -> dict[str, str]:
    try:
        raw = json.loads(settings.debug_io_labels or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value.strip()
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }


def _store_debug_io_labels(settings: AppSettings, labels: dict[str, str]) -> None:
    settings.debug_io_labels = json.dumps(labels, separators=(",", ":"), sort_keys=True)


def _mask_rows(
    mask: int,
    prefix: str,
    count: int,
    *,
    writable: bool,
    direction: str,
    bank: str,
) -> list[dict]:
    return [
        {
            "channel": f"{prefix}{index}",
            "index": index,
            "bit": index,
            "value": _bit_value(mask, index),
            "writable": writable,
            "direction": direction,
            "bank": bank,
        }
        for index in range(count)
    ]


def _apply_debug_labels(snapshot: dict, settings: AppSettings) -> dict:
    labels = _load_debug_io_labels(settings)
    for group_name in ("digital_input_groups", "digital_output_groups"):
        for group in snapshot.get(group_name, []):
            for row in group.get("rows", []):
                direction = row.get("direction")
                bank = row.get("bank")
                index = row.get("index")
                key = (
                    _debug_io_label_key(direction, bank, index)
                    if direction is not None and bank is not None and index is not None
                    else None
                )
                custom = labels.get(key) if key else None
                if not settings.manual_io_control_enabled:
                    row["writable"] = False
                elif settings.robot_connection_mode == "physical":
                    row["writable"] = direction == "output" and row.get("value") is not None
                row["label_key"] = key
                row["label"] = custom or row.get("channel")
                row["custom_label"] = custom
    return snapshot


def _simulated_robot_snapshot(settings: AppSettings, summary: dict) -> dict:
    machine_running = settings.machine_state == "running"
    machine_error = settings.machine_state == "error"
    queue_has_work = summary["queue_count"] > 0
    total_known = max(
        summary["queue_count"] + summary["pool_count"] + summary["storage_count"],
        1,
    )
    snapshot = {
        "revision": settings.revision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "simulated",
        "connected": True,
        "connection_label": "Simulator",
        "robot": {
            "host": None,
            "port": None,
            "controller_version": None,
            "recipe_fields": [],
        },
        "digital_input_groups": [
            {
                "title": "Standard inputs",
                "rows": _mask_rows(
                    settings.debug_standard_input_mask,
                    "DI",
                    8,
                    writable=True,
                    direction="input",
                    bank="standard",
                ),
            },
            {
                "title": "Configurable inputs",
                "rows": _mask_rows(
                    settings.debug_configurable_input_mask,
                    "CI",
                    8,
                    writable=True,
                    direction="input",
                    bank="configurable",
                ),
            },
            {
                "title": "Tool inputs",
                "rows": _mask_rows(
                    settings.debug_tool_input_mask,
                    "TI",
                    2,
                    writable=True,
                    direction="input",
                    bank="tool",
                ),
            }
        ],
        "digital_output_groups": [
            {
                "title": "Standard outputs",
                "rows": _mask_rows(
                    settings.debug_standard_output_mask,
                    "DO",
                    8,
                    writable=True,
                    direction="output",
                    bank="standard",
                ),
            },
            {
                "title": "Configurable outputs",
                "rows": _mask_rows(
                    settings.debug_configurable_output_mask,
                    "CO",
                    8,
                    writable=True,
                    direction="output",
                    bank="configurable",
                ),
            },
            {
                "title": "Tool outputs",
                "rows": _mask_rows(
                    settings.debug_tool_output_mask,
                    "TO",
                    2,
                    writable=True,
                    direction="output",
                    bank="tool",
                ),
            }
        ],
        "analog_inputs": [
            {"channel": "AI0", "label": "Queue fill ratio", "value": round(summary["queue_count"] / total_known, 3), "mode_mask": None, "mode_bit": None},
            {"channel": "AI1", "label": "Pool open ratio", "value": round(summary["pool_open_positions"] / max(settings.pool_slot_count, 1), 3), "mode_mask": None, "mode_bit": None},
        ],
        "analog_outputs": [
            {"channel": "AO0", "label": "Machine load demand", "value": 1.0 if summary["machine_pallet"] is None and queue_has_work else 0.0, "mode_mask": None, "mode_bit": None},
            {"channel": "AO1", "label": "Machine unload demand", "value": 1.0 if summary["machine_pallet"] is not None and not machine_running else 0.0, "mode_mask": None, "mode_bit": None},
        ],
        "state_rows": [
            {"label": "Robot mode", "value": "simulated"},
            {"label": "Safety mode", "value": "normal" if not machine_error else "fault"},
            {"label": "Runtime state", "value": settings.machine_state},
        ],
        "pose_rows": [],
        "tcp_speed_rows": [],
        "joint_rows": [],
        "extra_actual_rows": [],
        "notes": "Showing simulated I/O derived from the current board state because debug mode is enabled.",
    }
    return _apply_debug_labels(snapshot, settings)


def robot_io_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    summary = _board_summary(settings, pallets)

    if settings.robot_connection_mode == "physical":
        if not settings.robot_host.strip():
            return {
                "revision": settings.revision,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "unavailable",
                "connected": False,
                "connection_label": "Physical robot not configured",
                "machine_state": settings.machine_state,
                "summary": summary,
                "robot": {
                    "mode": settings.robot_connection_mode,
                    "host": None,
                    "port": settings.robot_port,
                    "controller_version": None,
                    "recipe_fields": [],
                },
                "digital_input_groups": [],
                "digital_output_groups": [],
                "analog_inputs": [],
                "analog_outputs": [],
                "state_rows": [],
                "pose_rows": [],
                "tcp_speed_rows": [],
                "joint_rows": [],
                "extra_actual_rows": [],
                "notes": "Physical robot mode is selected, but no robot host is configured.",
            }
        try:
            snapshot = read_robot_snapshot(
                settings.robot_host.strip(),
                settings.robot_port,
                settings.robot_poll_hz,
                settings.robot_timeout_seconds,
            )
            snapshot["revision"] = settings.revision
            snapshot["summary"] = summary
            snapshot["machine_state"] = settings.machine_state
            snapshot["robot"]["mode"] = settings.robot_connection_mode
            return _apply_debug_labels(snapshot, settings)
        except RobotTelemetryError as exc:
            return {
                "revision": settings.revision,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "unavailable",
                "connected": False,
                "connection_label": "Physical robot unavailable",
                "machine_state": settings.machine_state,
                "summary": summary,
                "robot": {
                    "mode": settings.robot_connection_mode,
                    "host": settings.robot_host.strip(),
                    "port": settings.robot_port,
                    "controller_version": None,
                    "recipe_fields": [],
                },
                "digital_input_groups": [],
                "digital_output_groups": [],
                "analog_inputs": [],
                "analog_outputs": [],
                "state_rows": [],
                "pose_rows": [],
                "tcp_speed_rows": [],
                "joint_rows": [],
                "extra_actual_rows": [],
                "notes": f"Physical robot mode is selected, but live RTDE telemetry is unavailable: {exc}",
                "warning": str(exc),
            }

    if settings.robot_connection_mode == "simulated":
        snapshot = _simulated_robot_snapshot(settings, summary)
        snapshot["summary"] = summary
        snapshot["machine_state"] = settings.machine_state
        snapshot["robot"]["mode"] = settings.robot_connection_mode
        return snapshot

    if settings.robot_host.strip():
        try:
            snapshot = read_robot_snapshot(
                settings.robot_host.strip(),
                settings.robot_port,
                settings.robot_poll_hz,
                settings.robot_timeout_seconds,
            )
            snapshot["revision"] = settings.revision
            snapshot["summary"] = summary
            snapshot["machine_state"] = settings.machine_state
            return snapshot
        except RobotTelemetryError as exc:
            if settings.debug_menu_enabled:
                snapshot = _simulated_robot_snapshot(settings, summary)
                snapshot["revision"] = settings.revision
                snapshot["summary"] = summary
                snapshot["machine_state"] = settings.machine_state
                snapshot["notes"] = (
                    f"Live robot telemetry failed: {exc}. Falling back to simulated I/O because debug mode is enabled."
                )
                snapshot["warning"] = str(exc)
                return snapshot
            return {
                "revision": settings.revision,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "unavailable",
                "connected": False,
                "connection_label": "Unavailable",
                "machine_state": settings.machine_state,
                "summary": summary,
                "robot": {
                    "host": settings.robot_host.strip(),
                    "port": settings.robot_port,
                    "controller_version": None,
                    "recipe_fields": [],
                },
                "digital_input_groups": [],
                "digital_output_groups": [],
                "analog_inputs": [],
                "analog_outputs": [],
                "state_rows": [],
                "pose_rows": [],
                "tcp_speed_rows": [],
                "joint_rows": [],
                "extra_actual_rows": [],
                "notes": f"Live robot telemetry is unavailable: {exc}",
                "warning": str(exc),
            }

    return {
        "revision": settings.revision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "unavailable",
        "connected": False,
        "connection_label": "Unavailable",
        "machine_state": settings.machine_state,
        "summary": summary,
        "robot": {
            "mode": settings.robot_connection_mode,
            "host": None,
            "port": None,
            "controller_version": None,
            "recipe_fields": [],
        },
        "digital_input_groups": [],
        "digital_output_groups": [],
        "analog_inputs": [],
        "analog_outputs": [],
        "state_rows": [],
        "pose_rows": [],
        "tcp_speed_rows": [],
        "joint_rows": [],
        "extra_actual_rows": [],
        "notes": "Select simulated or physical robot mode in Settings.",
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
    if payload.manual_io_control_enabled is not None:
        settings.manual_io_control_enabled = payload.manual_io_control_enabled
    settings.robot_connection_mode = payload.robot_connection_mode
    settings.robot_host = payload.robot_host
    settings.robot_port = payload.robot_port
    settings.robot_poll_hz = payload.robot_poll_hz
    settings.robot_timeout_seconds = payload.robot_timeout_seconds
    if not payload.debug_menu_enabled:
        settings.machine_state = "idle"
    cleared = reconcile_programs(session, settings)
    bump(settings)
    commit_or_conflict(session)
    return cleared


def toggle_debug_io(session: Session, payload: ToggleDebugIo) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.manual_io_control_enabled:
        raise problem(403, "Manual I/O control is locked in Settings.")

    if payload.bank == "tool" and payload.index > 1:
        raise problem(422, "Tool I/O indices only allow 0 or 1.")

    if settings.robot_connection_mode == "physical":
        if payload.direction != "output":
            raise problem(409, "Physical robot inputs are read-only.")
        if not settings.robot_host.strip():
            raise problem(409, "Physical robot mode requires a configured robot host.")
        try:
            toggle_robot_digital_output(
                settings.robot_host.strip(),
                settings.robot_port,
                settings.robot_timeout_seconds,
                payload.bank,
                payload.index,
            )
        except RobotTelemetryError as exc:
            raise problem(502, f"Could not toggle physical robot output: {exc}") from exc
        bump(settings)
        commit_or_conflict(session)
        return

    if not settings.debug_menu_enabled:
        raise problem(403, "Enable the debug simulator before changing simulated I/O.")

    attribute_name = f"debug_{payload.bank}_{payload.direction}_mask"
    current_mask = getattr(settings, attribute_name)
    toggled_mask = current_mask ^ (1 << payload.index)
    setattr(settings, attribute_name, toggled_mask)
    bump(settings)
    commit_or_conflict(session)


def rename_debug_io(session: Session, payload: RenameDebugIo) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if payload.bank == "tool" and payload.index > 1:
        raise problem(422, "Tool I/O indices only allow 0 or 1.")

    labels = _load_debug_io_labels(settings)
    key = _debug_io_label_key(payload.direction, payload.bank, payload.index)
    if payload.label:
        labels[key] = payload.label
    else:
        labels.pop(key, None)
    _store_debug_io_labels(settings, labels)
    bump(settings)
    commit_or_conflict(session)


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
