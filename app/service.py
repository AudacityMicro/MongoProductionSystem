from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import AppSettings, Pallet
from app.autoschedule import ScheduleJob, optimize_tool_schedule, simulate_tool_plan
from app.cnc_linuxcnc import CncTelemetryError, read_linuxcnc_io_labels, read_linuxcnc_snapshot
from app.robot_dashboard import RobotDashboardError, loaded_robot_program, run_robot_program
from app.robot_files import RobotFileAccessError, list_robot_program_files
from app.pallet_names import PALLET_NAMES
from app.robot_rtde import RobotTelemetryError, read_robot_snapshot, toggle_robot_digital_output
from app.schemas import (
    CreatePallet,
    MovePallet,
    QueuePallet,
    RenameDebugIo,
    ConfigureDebugProgram,
    ReorderQueue,
    SettingsUpdate,
    ToggleDebugIo,
    RunDebugProgram,
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


def program_metadata(program_path: str | None, content_status: str) -> dict:
    if not program_path or content_status in {"complete_parts", "defective_parts"}:
        return {"program_tools": [], "expected_cycle_seconds": None}
    # Placeholder metadata until program headers are parsed. It is stable per file path.
    digest = hashlib.sha256(program_path.casefold().encode("utf-8")).digest()
    tool_count = 2 + digest[0] % 4
    tools = sorted({1 + int.from_bytes(digest[index:index + 2], "big") % 999 for index in range(1, tool_count + 5)})[:tool_count]
    cycle_seconds = 180 + int.from_bytes(digest[8:10], "big") % 2101
    return {"program_tools": [f"T{tool}" for tool in tools], "expected_cycle_seconds": cycle_seconds}


def _nested_value(item: dict, *paths: tuple[str, ...]) -> object | None:
    for path in paths:
        value: object = item
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value is not None and value != "":
            return value
    return None


def fusion_tool_library(path_value: str) -> tuple[list[dict], str | None]:
    if not path_value.strip():
        return [], "No Fusion 360 tool library is configured."
    path = Path(path_value).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return [], "The Fusion 360 tool library file is unavailable."
    except json.JSONDecodeError:
        return [], "The Fusion 360 tool library is not valid JSON."
    entries = payload.get("data", payload.get("tools", [])) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return [], "The Fusion 360 tool library does not contain a tool list."
    tools: dict[int, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        number = _nested_value(item, ("post-process", "number"), ("post_process", "number"), ("number",))
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= 999:
            continue
        description = _nested_value(item, ("description",), ("comment",), ("product-id",), ("type",))
        tools[number] = {"number": number, "tool": f"T{number}", "description": str(description or "Fusion tool")}
    return [tools[number] for number in sorted(tools)], None


def fusion_tool_library_paths(settings: AppSettings) -> list[str]:
    try:
        paths = json.loads(settings.fusion_tool_library_paths or "[]")
    except json.JSONDecodeError:
        paths = []
    result = [path for path in paths if isinstance(path, str) and path.strip()]
    if settings.fusion_tool_library_path.strip() and settings.fusion_tool_library_path not in result:
        result.insert(0, settings.fusion_tool_library_path)
    return result


def fusion_tool_libraries(paths: list[str]) -> tuple[list[dict], list[str]]:
    merged: dict[int, dict] = {}
    warnings: list[str] = [] if paths else ["No Fusion 360 tool libraries are uploaded."]
    for path in paths:
        tools, warning = fusion_tool_library(path)
        if warning:
            warnings.append(f"{Path(path).name}: {warning}")
        for tool in tools:
            merged[tool["number"]] = tool
    return [merged[number] for number in sorted(merged)], warnings


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
        **program_metadata(pallet.program_path, pallet.content_status),
    }


def _location_position(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}
    try:
        return {axis: float(value.get(axis, 0)) for axis in ("x_mm", "y_mm", "z_mm")}
    except (TypeError, ValueError):
        return {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}


def pallet_location_positions(settings: AppSettings) -> dict:
    try:
        raw_pool = json.loads(settings.pool_location_positions or "[]")
    except json.JSONDecodeError:
        raw_pool = []
    stored = {
        item.get("slot"): _location_position(item)
        for item in raw_pool
        if isinstance(item, dict) and isinstance(item.get("slot"), int)
    }
    pool = [
        {"slot": slot, **stored.get(slot, _location_position({}))}
        for slot in range(1, settings.pool_slot_count + 1)
    ]
    try:
        on_deck = _location_position(json.loads(settings.on_deck_location_position))
    except json.JSONDecodeError:
        on_deck = _location_position({})
    try:
        dripping = _location_position(json.loads(settings.dripping_location_position))
    except json.JSONDecodeError:
        dripping = _location_position({})
    return {"pool_locations": pool, "on_deck_location": on_deck, "dripping_location": dripping}


def store_pallet_location_positions(settings: AppSettings, pool_locations: list[dict] | None, on_deck: dict | None, dripping: dict | None) -> None:
    current = pallet_location_positions(settings)
    if pool_locations is not None:
        by_slot = {item["slot"]: item for item in pool_locations}
        if set(by_slot) != set(range(1, settings.pool_slot_count + 1)):
            raise problem(422, "Provide exactly one location for every configured pool slot.")
        current["pool_locations"] = [{"slot": slot, **_location_position(by_slot[slot])} for slot in range(1, settings.pool_slot_count + 1)]
    if on_deck is not None:
        current["on_deck_location"] = _location_position(on_deck)
    if dripping is not None:
        current["dripping_location"] = _location_position(dripping)
    settings.pool_location_positions = json.dumps(current["pool_locations"], separators=(",", ":"))
    settings.on_deck_location_position = json.dumps(current["on_deck_location"], separators=(",", ":"))
    settings.dripping_location_position = json.dumps(current["dripping_location"], separators=(",", ":"))


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
            **pallet_location_positions(settings),
            "debug_menu_enabled": settings.debug_menu_enabled,
            "manual_io_control_enabled": settings.manual_io_control_enabled,
            "machine_state": settings.machine_state,
            "robot_connection_mode": settings.robot_connection_mode,
            "robot_host": settings.robot_host,
            "robot_port": settings.robot_port,
            "robot_poll_hz": settings.robot_poll_hz,
            "robot_timeout_seconds": settings.robot_timeout_seconds,
            "debug_program_button_count": settings.debug_program_button_count,
            "robot_file_access_enabled": settings.robot_file_access_enabled,
            "robot_file_host": settings.robot_file_host,
            "robot_file_port": settings.robot_file_port,
            "robot_file_username": settings.robot_file_username,
            "robot_file_password": settings.robot_file_password,
            "robot_file_password_configured": bool(settings.robot_file_password),
            "robot_file_directory": settings.robot_file_directory,
            "robot_program_extensions": json.loads(settings.robot_program_extensions),
            "robot_programs_page_enabled": settings.robot_programs_page_enabled,
            "robot_programs_filter_enabled": settings.robot_programs_filter_enabled,
            "robot_editor_command": settings.robot_editor_command,
            "cnc_telemetry_enabled": settings.cnc_telemetry_enabled,
            "cnc_host": settings.cnc_host,
            "cnc_ssh_port": settings.cnc_ssh_port,
            "cnc_ssh_username": settings.cnc_ssh_username,
            "cnc_ssh_password": settings.cnc_ssh_password,
            "cnc_timeout_seconds": settings.cnc_timeout_seconds,
            "mill_file_directory": settings.mill_file_directory,
            "mill_program_extensions": json.loads(settings.mill_program_extensions),
            "mill_programs_page_enabled": settings.mill_programs_page_enabled,
            "mill_programs_filter_enabled": settings.mill_programs_filter_enabled,
            "mill_editor_command": settings.mill_editor_command,
            "fusion_tool_library_path": settings.fusion_tool_library_path,
            "fusion_tool_libraries": [{"path": path, "name": Path(path).name} for path in fusion_tool_library_paths(settings)],
        },
        "programs": programs,
        "program_warning": warning,
    }


DEBUG_PROGRAM_COLORS = ("amber", "blue", "cyan", "green", "lime", "orange", "red", "violet")


def _load_debug_program_buttons(settings: AppSettings) -> list[dict[str, str]]:
    try:
        stored = json.loads(settings.debug_program_buttons or "[]")
    except json.JSONDecodeError:
        stored = []
    if not isinstance(stored, list):
        stored = []
    buttons: list[dict[str, str]] = []
    for index in range(settings.debug_program_button_count):
        raw = stored[index] if index < len(stored) and isinstance(stored[index], dict) else {}
        color = raw.get("color") if isinstance(raw.get("color"), str) else "blue"
        buttons.append(
            {
                "display_name": raw.get("display_name", "").strip() or f"Program {index + 1}",
                "filename": raw.get("filename", "").strip(),
                "color": color if color in DEBUG_PROGRAM_COLORS else "blue",
            }
        )
    return buttons


def _store_debug_program_buttons(settings: AppSettings, buttons: list[dict[str, str]]) -> None:
    settings.debug_program_buttons = json.dumps(buttons, separators=(",", ":"))


def _apply_debug_program_controls(snapshot: dict, settings: AppSettings) -> dict:
    buttons = _load_debug_program_buttons(settings)
    snapshot["program_controls"] = {
        "buttons": [
            {
                "index": index,
                **button,
                "can_run": bool(
                    settings.robot_connection_mode == "physical"
                    and settings.robot_host.strip()
                    and button["filename"]
                ),
            }
            for index, button in enumerate(buttons)
        ],
        "loaded_program": None,
        "file_list_note": (
            f"SFTP file browser is enabled. Open Edit to retrieve programs from {settings.robot_file_directory}."
            if settings.robot_file_access_enabled
            else "The Universal Robots Dashboard server does not provide a controller file listing. Enable SFTP file access in Settings to browse controller programs."
        ),
    }
    return snapshot


def robot_program_files(session: Session, include_all: bool = False) -> list[str]:
    settings = get_settings(session)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Robot file browsing requires a configured physical robot.")
    if not settings.robot_file_access_enabled:
        raise problem(409, "Enable SFTP robot file access in Settings first.")
    host = settings.robot_file_host or settings.robot_host.strip()
    try:
        return list_robot_program_files(
            host=host,
            port=settings.robot_file_port,
            username=settings.robot_file_username,
            password=settings.robot_file_password,
            directory=settings.robot_file_directory,
            extensions=None if include_all else set(json.loads(settings.robot_program_extensions)),
            timeout_seconds=settings.robot_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, str(exc)) from exc


def robot_programs_page_settings(session: Session) -> AppSettings:
    settings = get_settings(session)
    if not settings.robot_programs_page_enabled:
        raise problem(404, "Robot Programs is disabled in Settings.")
    return settings


def robot_file_manager_settings(session: Session) -> AppSettings:
    settings = robot_programs_page_settings(session)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Robot Programs requires a configured physical robot.")
    if not settings.robot_file_access_enabled:
        raise problem(409, "Enable SFTP robot file access in Settings first.")
    return settings


def mill_programs_page_settings(session: Session) -> AppSettings:
    settings = get_settings(session)
    if not settings.mill_programs_page_enabled:
        raise problem(404, "Mill Programs is disabled in Settings.")
    return settings


def mill_file_manager_settings(session: Session) -> AppSettings:
    settings = mill_programs_page_settings(session)
    if not settings.cnc_host.strip():
        raise problem(409, "Mill Programs requires PathPilot SSH connection settings.")
    if not settings.cnc_ssh_username or not settings.cnc_ssh_password:
        raise problem(409, "Enter the PathPilot SSH username and password in Settings first.")
    return settings


def _board_summary(settings: AppSettings, pallets: list[Pallet]) -> dict:
    machine_pallet = next((item for item in pallets if item.location == "machine"), None)
    on_deck_pallet = next((item for item in pallets if item.location == "on_deck"), None)
    dripping_pallet = next((item for item in pallets if item.location == "dripping"), None)
    queue_count = sum(1 for item in pallets if item.queue_position is not None)
    pool_count = sum(1 for item in pallets if item.location == "pool")
    storage_count = sum(1 for item in pallets if item.location == "storage")
    return {
        "machine_pallet": machine_pallet.name if machine_pallet else None,
        "on_deck_pallet": on_deck_pallet.name if on_deck_pallet else None,
        "dripping_pallet": dripping_pallet.name if dripping_pallet else None,
        "queue_count": queue_count,
        "pool_count": pool_count,
        "storage_count": storage_count,
        "pool_open_positions": max(settings.pool_slot_count - pool_count, 0),
    }


def _configured_cnc_telemetry(settings: AppSettings) -> tuple[dict | None, str]:
    if not settings.cnc_telemetry_enabled:
        return None, "Mill telemetry is not connected yet."
    if not settings.cnc_host.strip():
        return None, "CNC telemetry is enabled, but no controller host is configured."
    try:
        telemetry = read_linuxcnc_snapshot(
            settings.cnc_host.strip(),
            settings.cnc_ssh_port,
            settings.cnc_ssh_username,
            settings.cnc_ssh_password,
            settings.cnc_timeout_seconds,
        )
    except CncTelemetryError as exc:
        return None, f"PathPilot ATC telemetry is unavailable: {exc}"
    return telemetry, "Live PathPilot zbot carousel assignments."


def _atc_inventory(telemetry: dict | None, descriptions: dict[int, dict] | None = None) -> list[dict]:
    descriptions = descriptions or {}
    slots = telemetry.get("atc", {}).get("slots", []) if telemetry else []
    inventory = []
    for slot in slots:
        tool_number = slot.get("tool_number")
        library_tool = descriptions.get(tool_number, {}) if tool_number else {}
        inventory.append(
            {
                "position": slot.get("position"),
                "number": tool_number,
                "tool": f"T{tool_number}" if tool_number else None,
                "description": library_tool.get("description") or (f"PathPilot tool {tool_number}" if tool_number else "Empty"),
                "diameter": slot.get("diameter"),
                "length_offset": slot.get("length_offset"),
                "current": bool(slot.get("current")),
            }
        )
    return inventory


def _tool_color_states(library: list[dict], telemetry: dict | None, atc_slots: list[dict]) -> dict[str, dict]:
    if not telemetry:
        return {}
    loaded_numbers = {slot["number"] for slot in atc_slots if slot["number"] is not None}
    lengths = {
        row.get("tool_number"): row.get("length_offset")
        for row in telemetry.get("tool_table", [])
        if row.get("tool_number") is not None
    }
    states = {}
    for tool in library:
        number = tool["number"]
        length = lengths.get(number)
        if number in loaded_numbers:
            status = "atc"
        elif isinstance(length, (int, float)) and not math.isclose(length, 0.0, abs_tol=1e-9):
            status = "measured"
        else:
            status = "zero"
        states[str(number)] = {"status": status, "length_offset": length}
    return states


def dashboard_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    queue = sorted((item for item in pallets if item.queue_position is not None), key=lambda item: item.queue_position or 0)
    machine = next((item for item in pallets if item.location == "machine"), None)
    queue_items = [serialize_pallet(item) for item in queue]
    queue_cycle_seconds = sum(item["expected_cycle_seconds"] or 0 for item in queue_items)
    queue_tools = sorted({tool for item in queue_items for tool in item["program_tools"]}, key=lambda tool: int(tool[1:]))
    machine_item = serialize_pallet(machine) if machine else None
    telemetry, atc_source = _configured_cnc_telemetry(settings)
    atc_slots = _atc_inventory(telemetry)
    return {
        "revision": settings.revision,
        "queue": queue_items,
        "queue_cycle_seconds": queue_cycle_seconds,
        "queue_tools": queue_tools,
        "machine_pallet": machine_item,
        "current_cycle_seconds": machine_item["expected_cycle_seconds"] if machine_item else None,
        "atc_tools": [slot["tool"] for slot in atc_slots if slot["tool"]],
        "atc_source": atc_source,
        "summary": _board_summary(settings, pallets),
    }


def autoschedule_queue_preview(session: Session, expected_revision: int) -> dict:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    queue = tuple(
        session.scalars(
            select(Pallet)
            .where(Pallet.queue_position.is_not(None))
            .order_by(Pallet.queue_position)
        ).all()
    )
    serialized = {pallet.id: serialize_pallet(pallet) for pallet in queue}
    active_jobs = tuple(
        ScheduleJob(
            pallet_id=pallet.id,
            name=pallet.name,
            program=pallet.program_path or "",
            tools=frozenset(int(tool[1:]) for tool in serialized[pallet.id]["program_tools"]),
            original_position=pallet.queue_position or 0,
        )
        for pallet in queue
        if serialized[pallet.id]["program_tools"]
    )

    telemetry, atc_source = _configured_cnc_telemetry(settings)
    slots = telemetry.get("atc", {}).get("slots", []) if telemetry else []
    initial_tools = frozenset(
        int(slot["tool_number"])
        for slot in slots
        if slot.get("tool_number") is not None
    )
    largest_job = max((len(job.tools) for job in active_jobs), default=0)
    capacity = max(len(slots), len(initial_tools), largest_job, 16 if not slots else 0)

    original = simulate_tool_plan(active_jobs, initial_tools, capacity)
    optimized, method = optimize_tool_schedule(active_jobs, initial_tools, capacity)
    optimized_by_id = {job.pallet_id: job for job in active_jobs}
    optimized_jobs = iter(optimized["pallet_ids"])
    full_order = [
        next(optimized_jobs) if pallet.id in optimized_by_id else pallet.id
        for pallet in queue
    ]
    optimized["pallet_ids"] = full_order
    fixed = [
        {"pallet_id": pallet.id, "name": pallet.name, "position": pallet.queue_position}
        for pallet in queue
        if pallet.id not in optimized_by_id
    ]
    telemetry_available = bool(slots)
    warning = None if telemetry_available else (
        f"{atc_source} Scheduling used an empty 16-position ATC baseline."
    )
    return {
        "revision": settings.revision,
        "algorithm": method,
        "atc": {
            "capacity": capacity,
            "initial_tools": [f"T{number}" for number in sorted(initial_tools)],
            "source": atc_source,
            "telemetry_available": telemetry_available,
        },
        "original": original,
        "optimized": optimized,
        "savings": {
            "loads": original["loads"] - optimized["loads"],
            "unloads": original["unloads"] - optimized["unloads"],
            "tool_movements": original["tool_movements"] - optimized["tool_movements"],
        },
        "fixed_pallets": fixed,
        "can_apply": full_order != [pallet.id for pallet in queue],
        "warning": warning,
        "automation": {
            "commands_generated": False,
            "note": "Tool load and unload steps are planning data only; no robot or mill commands are sent.",
        },
    }


def tools_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    paths = fusion_tool_library_paths(settings)
    library, warnings = fusion_tool_libraries(paths)
    by_number = {item["number"]: item for item in library}
    telemetry, atc_source = _configured_cnc_telemetry(settings)
    atc_slots = _atc_inventory(telemetry, by_number)
    return {
        "revision": settings.revision,
        "atc_slots": atc_slots,
        "atc_tools": [slot for slot in atc_slots if slot["tool"]],
        "atc_source": atc_source,
        "tool_states": _tool_color_states(library, telemetry, atc_slots),
        "library": library,
        "warning": " ".join(warnings) or None,
    }


def add_fusion_tool_library(session: Session, path: str) -> list[str]:
    settings = get_settings(session)
    paths = fusion_tool_library_paths(settings)
    if path not in paths:
        paths.append(path)
    settings.fusion_tool_library_paths = json.dumps(paths, separators=(",", ":"))
    bump(settings)
    commit_or_conflict(session)
    return paths


def remove_fusion_tool_library(session: Session, path: str) -> list[str]:
    settings = get_settings(session)
    try:
        paths = json.loads(settings.fusion_tool_library_paths or "[]")
    except json.JSONDecodeError:
        paths = []
    if path not in paths:
        raise problem(404, "Uploaded Fusion tool library was not found.")
    paths.remove(path)
    settings.fusion_tool_library_paths = json.dumps(paths, separators=(",", ":"))
    bump(settings)
    commit_or_conflict(session)
    return paths


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
        summary["queue_count"] + summary["pool_count"] + summary["storage_count"]
        + int(summary["on_deck_pallet"] is not None) + int(summary["dripping_pallet"] is not None),
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
    return _apply_debug_program_controls(_apply_debug_labels(snapshot, settings), settings)


def _cnc_unavailable_snapshot(label: str, notes: str) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected": False,
        "connection_label": label,
        "source": "PathPilot / LinuxCNC adapter",
        "machine_model": "Tormach 1500MX",
        "controller_state": "Unavailable",
        "program": "Unavailable",
        "spindle": "Unavailable",
        "coolant": "Unavailable",
        "feed_override": "Unavailable",
        "notes": notes,
        "axis_rows": [],
        "health": {},
        "motion": {},
        "coordinates": {},
        "program_execution": {},
        "spindle_details": {},
        "probe": {},
        "tooling": {},
        "production": {},
        "io": {},
        "atc": {},
        "tool_table": [],
    }


def cnc_debug_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    if not settings.cnc_telemetry_enabled:
        return _cnc_unavailable_snapshot(
            "Telemetry disabled",
            "Enable CNC telemetry in Settings, then provide the PathPilot controller SSH connection details.",
        )
    if not settings.cnc_host.strip():
        return _cnc_unavailable_snapshot("Host not configured", "Enter the PathPilot controller IP address in Settings.")

    try:
        telemetry = read_linuxcnc_snapshot(
            settings.cnc_host.strip(),
            settings.cnc_ssh_port,
            settings.cnc_ssh_username,
            settings.cnc_ssh_password,
            settings.cnc_timeout_seconds,
        )
    except CncTelemetryError as exc:
        return _cnc_unavailable_snapshot("Controller unavailable", f"Read-only PathPilot telemetry failed: {exc}")

    task_states = {1: "Estop", 2: "Estop reset", 3: "Machine off", 4: "Machine on"}
    task_modes = {1: "Manual", 2: "Auto", 3: "MDI"}
    interpreter_states = {1: "Idle", 2: "Reading", 3: "Paused", 4: "Waiting"}
    spindle_speed = telemetry.get("spindle_speed")
    spindle_text = "Stopped" if not telemetry.get("spindle_enabled") else f"{spindle_speed or 0:g} RPM"
    coolant = "Flood" if telemetry.get("flood") else ("Mist" if telemetry.get("mist") else "Off")
    feed_override = telemetry.get("feed_override")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected": True,
        "connection_label": "Live read-only telemetry",
        "source": "PathPilot / LinuxCNC over SSH",
        "machine_model": "Tormach 1500MX",
        "controller_state": task_states.get(telemetry.get("task_state"), f"State {telemetry.get('task_state')}") + f" / {task_modes.get(telemetry.get('task_mode'), 'Unknown mode')}",
        "program": telemetry.get("program") or "No program loaded",
        "spindle": spindle_text,
        "coolant": coolant,
        "feed_override": f"{feed_override * 100:.0f}%" if isinstance(feed_override, (int, float)) else "Unavailable",
        "notes": f"Interpreter: {interpreter_states.get(telemetry.get('interp_state'), 'Unknown')} | Tool: T{telemetry.get('tool_in_spindle') or 0} | Line: {telemetry.get('motion_line') or telemetry.get('current_line') or 'Unavailable'}",
        "axis_rows": telemetry.get("axis_rows", []),
        "atc": telemetry.get("atc", {}),
        "tool_table": telemetry.get("tool_table", []),
        "health": telemetry.get("health", {}),
        "motion": telemetry.get("motion", {}),
        "coordinates": telemetry.get("coordinates", {}),
        "program_execution": telemetry.get("program_execution", {}),
        "spindle_details": telemetry.get("spindle_details", {}),
        "probe": telemetry.get("probe", {}),
        "tooling": telemetry.get("tooling", {}),
        "production": telemetry.get("production", {}),
        "io": telemetry.get("io", {}),
    }


def cnc_io_labels_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    empty = {"digital_inputs": {}, "digital_outputs": {}, "analog_inputs": {}, "analog_outputs": {}}
    if not settings.cnc_telemetry_enabled or not settings.cnc_host.strip():
        return {"connected": False, "labels": empty}
    try:
        labels = read_linuxcnc_io_labels(
            settings.cnc_host.strip(),
            settings.cnc_ssh_port,
            settings.cnc_ssh_username,
            settings.cnc_ssh_password,
            settings.cnc_timeout_seconds,
        )
    except CncTelemetryError:
        return {"connected": False, "labels": empty}
    return {"connected": True, "labels": labels}


def test_cnc_telemetry_connection(host: str, port: int, username: str, password: str, timeout: float) -> dict:
    """Run one read-only status query using unsaved CNC settings."""
    try:
        telemetry = read_linuxcnc_snapshot(host, port, username, password, timeout)
    except CncTelemetryError as exc:
        raise problem(502, f"CNC telemetry test failed: {exc}") from exc
    axes = len(telemetry.get("axis_rows", []))
    return {
        "connected": True,
        "message": f"Connected. Read {axes} axis status record{'s' if axes != 1 else ''} from LinuxCNC.",
        "program": telemetry.get("program") or "No program loaded",
        "task_state": telemetry.get("task_state"),
        "axis_count": axes,
    }


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
            try:
                snapshot["program_controls"] = _apply_debug_program_controls({}, settings)["program_controls"]
                snapshot["program_controls"]["loaded_program"] = loaded_robot_program(
                    settings.robot_host.strip(), settings.robot_timeout_seconds
                )
            except RobotDashboardError as exc:
                snapshot = _apply_debug_program_controls(snapshot, settings)
                snapshot["program_controls"]["file_list_note"] = f"Controller program query unavailable: {exc}"
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

    if payload.destination in {"on_deck", "machine", "dripping"}:
        occupant = session.scalar(
            select(Pallet).where(Pallet.location == payload.destination, Pallet.id != pallet_id)
        )
        if occupant:
            labels = {"on_deck": "On deck station", "machine": "Machine", "dripping": "Dripping station"}
            raise problem(409, f"{labels[payload.destination]} is occupied by {occupant.name}.")
        if payload.destination == "machine":
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
    if was_queued and payload.destination not in {"pool", "on_deck"}:
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
    store_pallet_location_positions(
        settings,
        [item.model_dump() for item in payload.pool_locations] if payload.pool_locations is not None else None,
        payload.on_deck_location.model_dump() if payload.on_deck_location is not None else None,
        payload.dripping_location.model_dump() if payload.dripping_location is not None else None,
    )
    settings.debug_menu_enabled = payload.debug_menu_enabled
    if payload.manual_io_control_enabled is not None:
        settings.manual_io_control_enabled = payload.manual_io_control_enabled
    settings.robot_connection_mode = payload.robot_connection_mode
    settings.robot_host = payload.robot_host
    settings.robot_port = payload.robot_port
    settings.robot_poll_hz = payload.robot_poll_hz
    settings.robot_timeout_seconds = payload.robot_timeout_seconds
    if payload.debug_program_button_count is not None:
        settings.debug_program_button_count = payload.debug_program_button_count
    if payload.robot_file_access_enabled is not None:
        settings.robot_file_access_enabled = payload.robot_file_access_enabled
    if payload.robot_file_host is not None:
        settings.robot_file_host = payload.robot_file_host
    if payload.robot_file_port is not None:
        settings.robot_file_port = payload.robot_file_port
    if payload.robot_file_username is not None:
        settings.robot_file_username = payload.robot_file_username
    if payload.robot_file_directory is not None:
        settings.robot_file_directory = payload.robot_file_directory
    if payload.robot_file_password is not None:
        settings.robot_file_password = payload.robot_file_password
    if payload.robot_program_extensions is not None:
        settings.robot_program_extensions = json.dumps(
            normalize_extensions(payload.robot_program_extensions),
            separators=(",", ":"),
        )
    if payload.robot_programs_page_enabled is not None:
        settings.robot_programs_page_enabled = payload.robot_programs_page_enabled
    if payload.robot_programs_filter_enabled is not None:
        settings.robot_programs_filter_enabled = payload.robot_programs_filter_enabled
    if payload.robot_editor_command is not None:
        settings.robot_editor_command = payload.robot_editor_command
    if payload.cnc_telemetry_enabled is not None:
        settings.cnc_telemetry_enabled = payload.cnc_telemetry_enabled
    if payload.cnc_host is not None:
        settings.cnc_host = payload.cnc_host
    if payload.cnc_ssh_port is not None:
        settings.cnc_ssh_port = payload.cnc_ssh_port
    if payload.cnc_ssh_username is not None:
        settings.cnc_ssh_username = payload.cnc_ssh_username
    if payload.cnc_ssh_password is not None:
        settings.cnc_ssh_password = payload.cnc_ssh_password
    if payload.cnc_timeout_seconds is not None:
        settings.cnc_timeout_seconds = payload.cnc_timeout_seconds
    if payload.mill_file_directory is not None:
        settings.mill_file_directory = payload.mill_file_directory
    if payload.mill_program_extensions is not None:
        settings.mill_program_extensions = json.dumps(
            normalize_extensions(payload.mill_program_extensions),
            separators=(",", ":"),
        )
    if payload.mill_programs_page_enabled is not None:
        settings.mill_programs_page_enabled = payload.mill_programs_page_enabled
    if payload.mill_programs_filter_enabled is not None:
        settings.mill_programs_filter_enabled = payload.mill_programs_filter_enabled
    if payload.mill_editor_command is not None:
        settings.mill_editor_command = payload.mill_editor_command
    if payload.fusion_tool_library_path is not None:
        settings.fusion_tool_library_path = payload.fusion_tool_library_path
    if not payload.debug_menu_enabled:
        settings.machine_state = "idle"
    cleared = reconcile_programs(session, settings)
    bump(settings)
    commit_or_conflict(session)
    return cleared


def configure_debug_program(session: Session, payload: ConfigureDebugProgram) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if payload.index >= settings.debug_program_button_count:
        raise problem(422, "That program button is not enabled in Settings.")
    filename = payload.filename.strip()
    if filename:
        path = PurePosixPath(filename)
        if not path.is_absolute() or ".." in path.parts:
            raise problem(422, "Robot program filename must be an absolute controller path without '..'.")
        if path.suffix.lower() not in set(json.loads(settings.robot_program_extensions)):
            raise problem(422, "Robot program filename does not match the configured Robot program extensions.")
    buttons = _load_debug_program_buttons(settings)
    buttons[payload.index] = {
        "display_name": payload.display_name or f"Program {payload.index + 1}",
        "filename": filename,
        "color": payload.color,
    }
    _store_debug_program_buttons(settings, buttons)
    bump(settings)
    commit_or_conflict(session)


def run_debug_program(session: Session, payload: RunDebugProgram) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Running controller programs requires a configured physical robot.")
    buttons = _load_debug_program_buttons(settings)
    if payload.index >= len(buttons):
        raise problem(422, "That program button is not enabled in Settings.")
    filename = buttons[payload.index]["filename"]
    if not filename:
        raise problem(422, "Configure a controller filename for this program button first.")
    try:
        run_robot_program(settings.robot_host.strip(), filename, settings.robot_timeout_seconds)
    except RobotDashboardError as exc:
        raise problem(502, str(exc)) from exc


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
