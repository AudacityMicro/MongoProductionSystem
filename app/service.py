from __future__ import annotations

import json
import math
import hashlib
import time
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import AppSettings, Pallet, RobotMotion
from app.autoschedule import ScheduleJob, optimize_tool_schedule, simulate_tool_plan
from app.cnc_linuxcnc import CncTelemetryError, read_linuxcnc_io_labels, read_linuxcnc_snapshot, run_linuxcnc_program
from app.robot_dashboard import RobotDashboardError, loaded_robot_program, run_robot_program
from app.robot_files import RobotFileAccessError, list_robot_program_files, upload_robot_file
from app.robot_scripts import (
    GENERATED_REMOTE_DIRECTORY,
    build_mill_pallet_motion_script,
    build_pallet_motion_script,
    generated_script_directory,
    run_robot_script,
    sync_generated_scripts,
)
from app.pallet_names import PALLET_NAMES
from app.robot_rtde import RobotTelemetryError, read_robot_snapshot, toggle_robot_digital_output
from app.schemas import (
    CreatePallet,
    MovePallet,
    QueuePallet,
    RecoverPalletMotion,
    ConfirmRunModeAction,
    RenameDebugIo,
    ConfigureDebugProgram,
    ConfigureDebugMillProgram,
    ReorderQueue,
    SettingsUpdate,
    SetRunModeSafety,
    StartRunMode,
    StartMillPalletTransfer,
    StartPalletMotion,
    ToggleDebugIo,
    RunDebugProgram,
    RunDebugMillProgram,
    RunDebugMillPalletMotion,
    RunDebugPalletMotion,
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


def assert_run_mode_inactive(settings: AppSettings) -> None:
    if settings.run_mode_enabled:
        raise problem(409, "Stop run mode before making manual schedule or controller changes.")


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


def pallet_motion_programs(settings: AppSettings) -> list[dict]:
    try:
        stored = json.loads(settings.pallet_motion_programs or "[]")
    except json.JSONDecodeError:
        stored = []
    return [item for item in stored if isinstance(item, dict) and isinstance(item.get("slot"), int)]


def pallet_motion_generation(settings: AppSettings) -> dict:
    defaults = {
        "approach_y_clearance_mm": 100.0,
        "mill_approach_x_clearance_mm": 100.0,
        "lift_z_clearance_mm": 100.0,
        "max_travel_speed_rad_s": 0.6,
        "pickup_setdown_speed_m_s": 0.08,
        "rx_rad": 0.0,
        "ry_rad": 0.0,
        "rz_rad": 0.0,
        "grip_output": None,
        "grip_closed_value": True,
        "door_open_action": None,
        "door_close_action": None,
        "erowa_unlock_action": None,
        "erowa_lock_action": None,
        "mill_actuation_wait_seconds": 2.0,
        "safe_pre_waypoint": None,
        "safe_post_waypoint": None,
        "travel_waypoints": [],
    }
    try:
        stored = json.loads(settings.pallet_motion_generation or "{}")
    except json.JSONDecodeError:
        stored = {}
    return {**defaults, **stored} if isinstance(stored, dict) else defaults


def _motion_script_signature(settings: AppSettings) -> str:
    """Fingerprint every saved value that changes generated files or their destination."""
    deployment_host = settings.robot_file_host.strip() or settings.robot_host.strip()
    inputs = {
        "generation": pallet_motion_generation(settings),
        "locations": pallet_location_positions(settings),
        "stations": {
            "on_deck_enabled": settings.on_deck_enabled,
            "dripping_enabled": settings.dripping_enabled,
        },
        "deployment": {
            "host": deployment_host,
            "port": settings.robot_file_port,
            "directory": settings.robot_file_directory,
        },
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def motion_scripts_need_rebuild(settings: AppSettings) -> bool:
    if not settings.generated_motion_script_signature:
        # Existing installations can already have generated program mappings from before
        # signatures were introduced. Ask for one rebuild to establish a trusted baseline.
        return bool(pallet_motion_programs(settings))
    return settings.generated_motion_script_signature != _motion_script_signature(settings)


def _motion_program(settings: AppSettings, slot: int, operation: str) -> str:
    key = "pick_program" if operation == "pick" else "put_program"
    mapping = next((item for item in pallet_motion_programs(settings) if item["slot"] == slot), None)
    filename = mapping.get(key, "").strip() if mapping else ""
    path = PurePosixPath(filename)
    if not filename or not path.is_absolute() or ".." in path.parts or path.suffix.lower() not in {".urp", ".script"}:
        raise problem(422, f"Rebuild generated scripts or configure an absolute {operation} robot program for Pool {slot:02d}.")
    return filename


def _mill_motion_program(settings: AppSettings, operation: str) -> str:
    filename = f"{operation}_mill.script"
    root = PurePosixPath(settings.robot_file_directory or "/programs")
    return str(root / GENERATED_REMOTE_DIRECTORY / filename)


MILL_LOAD_POSITION_PROGRAM_NAME = "mongo_mill_load_position.nc"
MILL_PROGRAM_DIRECTORY = PurePosixPath("/home/operator/gcode/Gcode")


def build_mill_load_position_program(position: dict[str, float]) -> str:
    """Build the PathPilot move-to-load-position program in G53 machine coordinates."""
    x = float(position["x_in"])
    y = float(position["y_in"])
    z = float(position["z_in"])
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise problem(422, "Mill loading coordinates must be finite numbers.")
    return f"""( Mongo Production System - mill loading position )
( Generated from saved G53 machine coordinates. Units: inches. )
( Z moves first for clearance, then X and Y move together. )
G20
G90
G53 G1 Z{z:.4f} F100.0
G53 G1 X{x:.4f} Y{y:.4f} F100.0
M30
"""


def rebuild_mill_load_position_program(session: Session, expected_revision: int) -> dict[str, str]:
    """Write the generated mill-load-position program locally and to PathPilot."""
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if not settings.cnc_host.strip() or not settings.cnc_ssh_username or not settings.cnc_ssh_password:
        raise problem(409, "Configure the PathPilot SSH host, username, and password before building the mill loading program.")
    position = pallet_location_positions(settings)["mill_load_unload_g53"]
    content = build_mill_load_position_program(position)
    local_directory = Path(__file__).parents[1] / "runtime" / "generated-mill-programs"
    local_directory.mkdir(parents=True, exist_ok=True)
    local_path = local_directory / MILL_LOAD_POSITION_PROGRAM_NAME
    local_path.write_text(content, encoding="ascii")
    try:
        remote_path = upload_robot_file(
            host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
            username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
            directory=settings.mill_file_directory, destination=str(MILL_PROGRAM_DIRECTORY), filename=MILL_LOAD_POSITION_PROGRAM_NAME,
            content=BytesIO(content.encode("ascii")), timeout_seconds=settings.cnc_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, f"Could not upload the mill loading program: {exc}") from exc
    return {"filename": MILL_LOAD_POSITION_PROGRAM_NAME, "local_path": str(local_path), "remote_path": remote_path}


def _serialize_motion(motion: RobotMotion, pallet: Pallet | None = None) -> dict:
    return {
        "id": motion.id,
        "pallet_id": motion.pallet_id,
        "pallet_name": pallet.name if pallet else None,
        "operation": motion.operation,
        "source_slot": motion.source_slot,
        "destination_slot": motion.destination_slot,
        "program_path": motion.program_path,
        "status": motion.status,
        "retry_count": motion.retry_count,
        "observed_busy": motion.observed_busy,
        "created_at": motion.created_at,
        "started_at": motion.started_at,
        "completed_at": motion.completed_at,
        "failure_detail": motion.failure_detail,
    }


def _motion_snapshot(session: Session) -> dict:
    motions = session.scalars(select(RobotMotion).order_by(RobotMotion.created_at.desc()).limit(12)).all()
    pallets = {item.id: item for item in session.scalars(select(Pallet)).all()}
    active = next((item for item in motions if item.status in {"requested", "running", "faulted"}), None)
    return {
        "active": _serialize_motion(active, pallets.get(active.pallet_id)) if active else None,
        "history": [_serialize_motion(item, pallets.get(item.pallet_id)) for item in motions],
    }


def workholding_library(settings: AppSettings) -> list[str]:
    try:
        stored = json.loads(settings.workholding_library or "[]")
    except json.JSONDecodeError:
        stored = []
    if not isinstance(stored, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in stored:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


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


def _robot_waypoint(value: object, name: str) -> dict | None:
    axes = ("x_mm", "y_mm", "z_mm", "rx_rad", "ry_rad", "rz_rad")
    if not isinstance(value, dict) or not all(axis in value for axis in axes):
        return None
    try:
        return {"name": str(value.get("name") or name), **{axis: float(value[axis]) for axis in axes}}
    except (TypeError, ValueError):
        return None


def _mill_g53_position(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {axis: 0.0 for axis in ("x_in", "y_in", "z_in")}
    try:
        if any(axis in value for axis in ("x_in", "y_in", "z_in")):
            return {axis: float(value.get(axis, 0)) for axis in ("x_in", "y_in", "z_in")}
        return {
            "x_in": float(value.get("x_mm", 0)) / 25.4,
            "y_in": float(value.get("y_mm", 0)) / 25.4,
            "z_in": float(value.get("z_mm", 0)) / 25.4,
        }
    except (TypeError, ValueError):
        return {axis: 0.0 for axis in ("x_in", "y_in", "z_in")}


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
    try:
        mill_load_unload_g53 = _mill_g53_position(json.loads(settings.mill_pallet_change_g53_position))
    except json.JSONDecodeError:
        mill_load_unload_g53 = _mill_g53_position({})
    try:
        robot_mill_load_unload = _robot_waypoint(json.loads(settings.robot_mill_load_unload_position), "Mill load/unload")
    except json.JSONDecodeError:
        robot_mill_load_unload = None
    try:
        robot_mill_safe_entry_exit = _robot_waypoint(json.loads(settings.robot_mill_safe_entry_exit_position), "Mill safe entry/exit")
    except json.JSONDecodeError:
        robot_mill_safe_entry_exit = None
    return {
        "pool_locations": pool,
        "on_deck_location": on_deck,
        "dripping_location": dripping,
        "robot_mill_load_unload": robot_mill_load_unload,
        "robot_mill_safe_entry_exit": robot_mill_safe_entry_exit,
        "mill_load_unload_g53": mill_load_unload_g53,
    }


def store_pallet_location_positions(
    settings: AppSettings,
    pool_locations: list[dict] | None,
    on_deck: dict | None,
    dripping: dict | None,
    robot_mill_load_unload: dict | None,
    robot_mill_safe_entry_exit: dict | None,
    mill_load_unload_g53: dict | None,
    legacy_mill_pallet_change_g53: dict | None,
) -> None:
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
    if robot_mill_load_unload is not None:
        current["robot_mill_load_unload"] = robot_mill_load_unload
    if robot_mill_safe_entry_exit is not None:
        current["robot_mill_safe_entry_exit"] = robot_mill_safe_entry_exit
    if mill_load_unload_g53 is not None:
        current["mill_load_unload_g53"] = mill_load_unload_g53
    elif legacy_mill_pallet_change_g53 is not None:
        current["mill_load_unload_g53"] = {
            "x_in": float(legacy_mill_pallet_change_g53.get("x_mm", 0)) / 25.4,
            "y_in": float(legacy_mill_pallet_change_g53.get("y_mm", 0)) / 25.4,
            "z_in": float(legacy_mill_pallet_change_g53.get("z_mm", 0)) / 25.4,
        }
    settings.pool_location_positions = json.dumps(current["pool_locations"], separators=(",", ":"))
    settings.on_deck_location_position = json.dumps(current["on_deck_location"], separators=(",", ":"))
    settings.dripping_location_position = json.dumps(current["dripping_location"], separators=(",", ":"))
    settings.robot_mill_load_unload_position = json.dumps(current.get("robot_mill_load_unload") or {}, separators=(",", ":"))
    settings.robot_mill_safe_entry_exit_position = json.dumps(current.get("robot_mill_safe_entry_exit") or {}, separators=(",", ":"))
    settings.mill_pallet_change_g53_position = json.dumps(current["mill_load_unload_g53"], separators=(",", ":"))


def board_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    programs, warning = available_programs(settings)
    current_run_pallet = session.get(Pallet, settings.run_mode_current_pallet_id) if settings.run_mode_current_pallet_id else None
    return {
        "revision": settings.revision,
        "pallets": [serialize_pallet(item) for item in pallets],
        "settings": {
            "source_folder": settings.source_folder,
            "program_extensions": json.loads(settings.program_extensions),
            "weight_unit": settings.weight_unit,
            "workholding_library": workholding_library(settings),
            "pallet_motion_enabled": settings.pallet_motion_enabled,
            "pallet_motion_timeout_seconds": settings.pallet_motion_timeout_seconds,
            "pallet_motion_programs": pallet_motion_programs(settings),
            "pallet_motion_generation": pallet_motion_generation(settings),
            "motion_scripts_need_rebuild": motion_scripts_need_rebuild(settings),
            "pool_slot_count": settings.pool_slot_count,
            "on_deck_enabled": settings.on_deck_enabled,
            "dripping_enabled": settings.dripping_enabled,
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
            "debug_mill_program_button_count": settings.debug_mill_program_button_count,
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
            "cnc_require_a_axis_homed": settings.cnc_require_a_axis_homed,
            "mill_file_directory": settings.mill_file_directory,
            "mill_program_extensions": json.loads(settings.mill_program_extensions),
            "mill_programs_page_enabled": settings.mill_programs_page_enabled,
            "mill_programs_filter_enabled": settings.mill_programs_filter_enabled,
            "mill_editor_command": settings.mill_editor_command,
            "fusion_tool_library_path": settings.fusion_tool_library_path,
            "fusion_tool_libraries": [{"path": path, "name": Path(path).name} for path in fusion_tool_library_paths(settings)],
        },
        "run_mode": {
            "enabled": settings.run_mode_enabled,
            "safety_confirm": settings.run_mode_safety_confirm,
            "state": settings.run_mode_state,
            "detail": settings.run_mode_detail,
            "current_pallet_id": settings.run_mode_current_pallet_id,
            "current_pallet_name": current_run_pallet.name if current_run_pallet else None,
            "return_slot": settings.run_mode_return_slot,
            "pending_action": settings.run_mode_pending_action or None,
            "confirmation_token": settings.run_mode_confirmation_token or None,
        },
        "programs": programs,
        "program_warning": warning,
        "robot_motion": _motion_snapshot(session),
    }


DEBUG_PROGRAM_COLORS = ("amber", "blue", "cyan", "green", "lime", "orange", "red", "violet")


def _load_debug_program_buttons(settings: AppSettings, controller: str = "robot") -> list[dict[str, str]]:
    count = settings.debug_program_button_count if controller == "robot" else settings.debug_mill_program_button_count
    stored_value = settings.debug_program_buttons if controller == "robot" else settings.debug_mill_program_buttons
    try:
        stored = json.loads(stored_value or "[]")
    except json.JSONDecodeError:
        stored = []
    if not isinstance(stored, list):
        stored = []
    buttons: list[dict[str, str]] = []
    for index in range(count):
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


def _store_debug_program_buttons(settings: AppSettings, buttons: list[dict[str, str]], controller: str = "robot") -> None:
    if controller == "robot":
        settings.debug_program_buttons = json.dumps(buttons, separators=(",", ":"))
    else:
        settings.debug_mill_program_buttons = json.dumps(buttons, separators=(",", ":"))


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


def _apply_debug_mill_program_controls(snapshot: dict, settings: AppSettings) -> dict:
    buttons = _load_debug_program_buttons(settings, "mill")
    snapshot["revision"] = settings.revision
    snapshot["mill_program_controls"] = {
        "buttons": [
            {
                "index": index,
                **button,
                "can_run": bool(
                    settings.cnc_telemetry_enabled
                    and settings.cnc_host.strip()
                    and settings.cnc_ssh_username
                    and button["filename"]
                ),
            }
            for index, button in enumerate(buttons)
        ],
        "file_list_note": (
            "Choose G-code files from /home/operator/gcode/Gcode only. The list follows the Mill extensions in Settings."
            if settings.cnc_host.strip()
            else "Configure the PathPilot SSH connection in Settings to browse and run mill programs."
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
        return _apply_debug_mill_program_controls(_cnc_unavailable_snapshot(
            "Telemetry disabled",
            "Enable CNC telemetry in Settings, then provide the PathPilot controller SSH connection details.",
        ), settings)
    if not settings.cnc_host.strip():
        return _apply_debug_mill_program_controls(_cnc_unavailable_snapshot("Host not configured", "Enter the PathPilot controller IP address in Settings."), settings)

    try:
        telemetry = read_linuxcnc_snapshot(
            settings.cnc_host.strip(),
            settings.cnc_ssh_port,
            settings.cnc_ssh_username,
            settings.cnc_ssh_password,
            settings.cnc_timeout_seconds,
        )
    except CncTelemetryError as exc:
        return _apply_debug_mill_program_controls(
            _cnc_unavailable_snapshot("Controller unavailable", f"PathPilot telemetry failed: {exc}"), settings,
        )

    task_states = {1: "Estop", 2: "Estop reset", 3: "Machine off", 4: "Machine on"}
    task_modes = {1: "Manual", 2: "Auto", 3: "MDI"}
    interpreter_states = {1: "Idle", 2: "Reading", 3: "Paused", 4: "Waiting"}
    spindle_speed = telemetry.get("spindle_speed")
    spindle_text = "Stopped" if not telemetry.get("spindle_enabled") else f"{spindle_speed or 0:g} RPM"
    coolant = "Flood" if telemetry.get("flood") else ("Mist" if telemetry.get("mist") else "Off")
    feed_override = telemetry.get("feed_override")
    return _apply_debug_mill_program_controls({
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
    }, settings)


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


def current_robot_pose(session: Session) -> dict:
    settings = get_settings(session)
    if settings.robot_connection_mode != "physical":
        raise problem(409, "Current robot pose is only available in physical robot mode.")

    snapshot = robot_io_snapshot(session)
    if not snapshot.get("connected"):
        detail = snapshot.get("warning") or snapshot.get("notes") or "Live robot telemetry is unavailable."
        raise problem(409, str(detail))

    pose = _actual_tcp_pose(snapshot)
    if pose is None:
        raise problem(409, "The robot is connected, but its actual TCP pose is unavailable.")

    return {
        "x_mm": round(pose[0] * 1000, 3),
        "y_mm": round(pose[1] * 1000, 3),
        "z_mm": round(pose[2] * 1000, 3),
        "rx_rad": round(pose[3], 6),
        "ry_rad": round(pose[4], 6),
        "rz_rad": round(pose[5], 6),
        "timestamp": snapshot.get("timestamp"),
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
    assert_run_mode_inactive(settings)
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
    assert_run_mode_inactive(settings)
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
    assert_run_mode_inactive(settings)
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
    assert_run_mode_inactive(settings)
    _assert_no_locked_motion(session)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if pallet.location == "robot_held" or payload.destination == "robot_held":
        raise problem(409, "Use the pallet-motion controls to move a Robot-held pallet.")

    if payload.destination in {"on_deck", "machine", "dripping"}:
        if payload.destination == "on_deck" and not settings.on_deck_enabled:
            raise problem(409, "The On deck station is disabled in Settings.")
        if payload.destination == "dripping" and not settings.dripping_enabled:
            raise problem(409, "The Dripping station is disabled in Settings.")
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


def _robot_motion_activity(session: Session) -> tuple[bool, dict[str, object]]:
    first_snapshot = robot_io_snapshot(session)
    if not first_snapshot.get("connected"):
        raise problem(409, "Live robot telemetry is unavailable. Pallet movement is blocked.")

    # RTDE velocity values can have a noticeable noise floor while a robot is holding
    # position. Compare two actual TCP poses instead, so stationary noise cannot block a move.
    time.sleep(0.35)
    snapshot = robot_io_snapshot(session)
    if not snapshot.get("connected"):
        raise problem(409, "Live robot telemetry is unavailable. Pallet movement is blocked.")
    first_pose = _actual_tcp_pose(first_snapshot)
    second_pose = _actual_tcp_pose(snapshot)
    if first_pose is not None and second_pose is not None:
        linear_delta = math.sqrt(sum((second_pose[index] - first_pose[index]) ** 2 for index in range(3)))
        angular_delta = math.sqrt(sum((second_pose[index] - first_pose[index]) ** 2 for index in range(3, 6)))
        moving = linear_delta >= 0.001 or angular_delta >= 0.01
    else:
        # Retain a deliberately conservative fallback for telemetry recipes without pose data.
        components = [row.get("value") for row in snapshot.get("tcp_speed_rows", [])]
        try:
            linear_speed = math.sqrt(sum(float(components[index]) ** 2 for index in range(3)))
            angular_speed = math.sqrt(sum(float(components[index]) ** 2 for index in range(3, 6)))
            moving = linear_speed >= 0.01 or angular_speed >= 0.1
        except (TypeError, ValueError, IndexError):
            moving = False
    state = {row.get("label"): row.get("value") for row in snapshot.get("state_rows", [])}
    return moving, state


def _actual_tcp_pose(snapshot: dict) -> tuple[float, float, float, float, float, float] | None:
    rows = snapshot.get("tcp_detail_rows", [])
    if not isinstance(rows, list) or len(rows) < 6:
        return None
    try:
        values = tuple(float(row["actual_pose"]) for row in rows[:6])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return values


def _assert_motion_ready(session: Session, settings: AppSettings) -> None:
    if settings.robot_connection_mode != "physical":
        return
    if not settings.pallet_motion_enabled:
        raise problem(403, "Enable physical pallet movements in Settings before commanding Mongo.")
    if not settings.robot_host.strip():
        raise problem(409, "A physical robot host is required for pallet movement.")
    moving, state = _robot_motion_activity(session)
    if moving:
        raise problem(409, "Robot TCP is moving. Wait until the robot is stationary before starting a pallet movement.")
    safety_mode = state.get("Safety mode")
    runtime_state = state.get("Runtime state")
    if safety_mode not in {1, "normal", "NORMAL"}:
        raise problem(409, f"Robot safety mode is not normal ({safety_mode!s}).")
    if runtime_state not in {1, "stopped", "idle", "STOPPED", "IDLE"}:
        raise problem(409, f"Robot runtime is not idle ({runtime_state!s}).")


def _locked_motion(session: Session) -> RobotMotion | None:
    return session.scalar(
        select(RobotMotion)
        .where(RobotMotion.status.in_(("requested", "running", "faulted")))
        .order_by(RobotMotion.created_at.desc())
    )


def _assert_no_locked_motion(session: Session) -> None:
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before changing pallet records.")


def _finish_motion(session: Session, motion: RobotMotion, success: bool, detail: str | None = None) -> None:
    pallet = session.get(Pallet, motion.pallet_id)
    if not pallet:
        motion.status = "faulted"
        motion.failure_detail = "The pallet was deleted while its robot movement was active."
    elif success and motion.operation == "pick":
        pallet.location = "robot_held"
        pallet.pool_slot_number = None
        motion.status = "succeeded"
    elif success and motion.operation == "put":
        pallet.location = "pool"
        pallet.pool_slot_number = motion.destination_slot
        motion.status = "succeeded"
    elif success and motion.operation == "load_mill":
        # Queue membership is virtual; a pallet leaves its run position only once it is in the mill.
        if pallet.queue_position is not None:
            pallet.queue_position = None
            session.flush()
            compact_queue(session, pallet.id)
        pallet.location = "machine"
        pallet.pool_slot_number = None
        get_settings(session).machine_state = "running"
        motion.status = "succeeded"
    elif success and motion.operation == "unload_mill":
        pallet.location = "pool"
        pallet.pool_slot_number = motion.destination_slot
        get_settings(session).machine_state = "idle"
        motion.status = "succeeded"
    else:
        motion.status = "faulted"
        motion.failure_detail = detail or "Robot motion failed. Inspect the cell and reconcile the pallet location."
    motion.completed_at = datetime.now(timezone.utc).isoformat()
    settings = get_settings(session)
    bump(settings)
    commit_or_conflict(session)


def start_pallet_motion(session: Session, payload: StartPalletMotion, automated: bool = False) -> str | None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not automated:
        assert_run_mode_inactive(settings)
    locked = _locked_motion(session)
    if locked:
        if locked.status == "faulted":
            raise problem(409, "Resolve the existing pallet-motion fault before commanding another move.")
        raise problem(409, "Another pallet movement is already active.")
    if payload.pool_slot_number > settings.pool_slot_count:
        raise problem(422, "Pool position is outside the configured range.")
    _assert_motion_ready(session, settings)

    if payload.operation == "pick":
        if not payload.pallet_id:
            raise problem(422, "Select the pool pallet to pick.")
        pallet = session.get(Pallet, payload.pallet_id)
        if not pallet or pallet.location != "pool" or pallet.pool_slot_number != payload.pool_slot_number:
            raise problem(409, "That pallet is no longer in the selected pool position.")
        source_slot, destination_slot = payload.pool_slot_number, None
    else:
        pallet = session.scalar(select(Pallet).where(Pallet.location == "robot_held"))
        if not pallet:
            raise problem(409, "No pallet is currently marked Robot-held.")
        if payload.pallet_id and payload.pallet_id != pallet.id:
            raise problem(409, "Only the Robot-held pallet can be put away.")
        occupied = session.scalar(
            select(Pallet).where(Pallet.location == "pool", Pallet.pool_slot_number == payload.pool_slot_number)
        )
        if occupied:
            raise problem(409, f"Pool position {payload.pool_slot_number:02d} is occupied by {occupied.name}.")
        source_slot, destination_slot = None, payload.pool_slot_number

    motion = RobotMotion(
        id=str(uuid4()),
        pallet_id=pallet.id,
        operation=payload.operation,
        source_slot=source_slot,
        destination_slot=destination_slot,
        program_path=_motion_program(settings, payload.pool_slot_number, payload.operation) if settings.robot_connection_mode == "physical" else f"simulated://pool/{payload.pool_slot_number}/{payload.operation}",
        status="requested",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(motion)
    bump(settings)
    commit_or_conflict(session)
    if settings.robot_connection_mode == "simulated":
        _finish_motion(session, motion, True)
        return None
    return motion.id


def start_mill_pallet_transfer(session: Session, payload: StartMillPalletTransfer, automated: bool = False) -> str | None:
    """Queue a complete physical pool-to-mill or mill-to-pool transfer."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not automated:
        assert_run_mode_inactive(settings)
    locked = _locked_motion(session)
    if locked:
        raise problem(409, "Resolve or wait for the active robot pallet movement before commanding another move.")
    _assert_motion_ready(session, settings)

    if payload.operation == "load":
        if not payload.pallet_id:
            raise problem(422, "Choose a pallet to load into the mill.")
        pallet = session.get(Pallet, payload.pallet_id)
        if not pallet or pallet.location != "pool" or not pallet.pool_slot_number:
            raise problem(409, "That pallet must be in a pool position before Mongo can load it into the mill.")
        if session.scalar(select(Pallet).where(Pallet.location == "machine")):
            raise problem(409, "The mill already contains a pallet.")
        source_slot, destination_slot = pallet.pool_slot_number, None
        operation = "load_mill"
        program_path = (
            f"{_motion_program(settings, source_slot, 'pick')} -> {_mill_motion_program(settings, 'load')}"
            if settings.robot_connection_mode == "physical"
            else "simulated://mill/load"
        )
    else:
        pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
        if not pallet:
            raise problem(409, "There is no pallet currently marked as being in the mill.")
        if payload.pallet_id and payload.pallet_id != pallet.id:
            raise problem(409, "Only the pallet currently in the mill can be put away.")
        if not payload.pool_slot_number or payload.pool_slot_number > settings.pool_slot_count:
            raise problem(422, "Choose a valid empty pool position for the pallet.")
        occupant = session.scalar(select(Pallet).where(
            Pallet.location == "pool", Pallet.pool_slot_number == payload.pool_slot_number,
        ))
        if occupant:
            raise problem(409, f"Pool position {payload.pool_slot_number:02d} is occupied by {occupant.name}.")
        source_slot, destination_slot = None, payload.pool_slot_number
        operation = "unload_mill"
        program_path = (
            f"{_mill_motion_program(settings, 'unload')} -> {_motion_program(settings, destination_slot, 'put')}"
            if settings.robot_connection_mode == "physical"
            else "simulated://mill/unload"
        )

    motion = RobotMotion(
        id=str(uuid4()), pallet_id=pallet.id, operation=operation,
        source_slot=source_slot, destination_slot=destination_slot,
        program_path=program_path,
        status="requested", created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(motion)
    bump(settings)
    commit_or_conflict(session)
    if settings.robot_connection_mode == "simulated":
        _finish_motion(session, motion, True)
        return None
    return motion.id


def run_debug_pallet_motion(session: Session, payload: RunDebugPalletMotion) -> dict[str, object]:
    """Dispatch one generated pallet script for cell setup without changing board state."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active scheduled pallet movement before running a manual test.")
    if payload.pool_slot_number > settings.pool_slot_count:
        raise problem(422, "Pool position is outside the configured range.")
    _assert_motion_ready(session, settings)

    if settings.robot_connection_mode == "simulated":
        return {
            "status": "simulated",
            "operation": payload.operation,
            "pool_slot_number": payload.pool_slot_number,
            "message": "Simulated pallet-motion test accepted. No board state was changed.",
        }

    program_path = _motion_program(settings, payload.pool_slot_number, payload.operation)
    if not program_path:
        raise problem(422, "No generated program is assigned to that pool position. Rebuild the pallet-motion scripts first.")
    if PurePosixPath(program_path).suffix.lower() == ".script":
        local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(program_path).name
        if not local_script.is_file():
            raise problem(409, f"Generated local script is missing: {local_script.name}. Rebuild the pallet-motion scripts first.")
        run_robot_script(
            settings.robot_host.strip(),
            local_script.read_text(encoding="utf-8"),
            settings.robot_timeout_seconds,
        )
    else:
        run_robot_program(settings.robot_host.strip(), program_path, settings.robot_timeout_seconds)
    return {
        "status": "dispatched",
        "operation": payload.operation,
        "pool_slot_number": payload.pool_slot_number,
        "program_path": program_path,
        "message": "Robot command dispatched. Board state was not changed.",
    }


def _mill_motion_script_content(settings: AppSettings, operation: str) -> str:
    generation = pallet_motion_generation(settings)
    if not isinstance(generation.get("safe_pre_waypoint"), dict):
        raise problem(422, "Configure the shared safe waypoint before using a mill pallet transfer.")
    locations = pallet_location_positions(settings)
    mill_pose = locations["robot_mill_load_unload"]
    mill_entry_exit = locations["robot_mill_safe_entry_exit"]
    if not isinstance(mill_pose, dict) or not isinstance(mill_entry_exit, dict):
        raise problem(422, "Configure the robot mill load/unload and safe entry/exit poses before using a mill pallet transfer.")
    return build_mill_pallet_motion_script(
        function_name=f"mps_{operation}_mill",
        operation=operation,
        mill_pose=mill_pose,
        entry_exit_pose=mill_entry_exit,
        generation=generation,
    )


def run_debug_mill_pallet_motion(session: Session, payload: RunDebugMillPalletMotion) -> dict[str, object]:
    """Dispatch a generated mill transfer script without changing scheduled pallet state."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active scheduled pallet movement before running a manual test.")
    _assert_motion_ready(session, settings)

    if settings.robot_connection_mode == "simulated":
        return {
            "status": "simulated",
            "operation": payload.operation,
            "message": "Simulated mill transfer accepted. No board state was changed.",
        }

    program_path = _mill_motion_program(settings, payload.operation)
    local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(program_path).name
    if not local_script.is_file():
        raise problem(409, f"Generated local script is missing: {local_script.name}. Configure the mill poses and rebuild generated scripts first.")
    expected_script = _mill_motion_script_content(settings, payload.operation)
    script_content = local_script.read_text(encoding="utf-8")
    if script_content != expected_script:
        raise problem(409, "The generated mill-transfer script does not match the saved settings. Rebuild generated scripts before running it.")
    run_robot_script(
        settings.robot_host.strip(),
        script_content,
        settings.robot_timeout_seconds,
    )
    return {
        "status": "dispatched",
        "operation": payload.operation,
        "program_path": program_path,
        "message": "Robot command dispatched. The schedule and Robot-held state were not changed.",
    }


def execute_pallet_motion(session_factory, motion_id: str) -> None:
    """Run one persisted physical motion. Every terminal outcome is committed for recovery."""
    with session_factory() as session:
        motion = session.get(RobotMotion, motion_id)
        if not motion or motion.status != "requested":
            return
        try:
            settings = get_settings(session)
            def run_script(program_path: str) -> None:
                if PurePosixPath(program_path).suffix.lower() == ".script":
                    local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(program_path).name
                    if not local_script.is_file():
                        raise RobotFileAccessError(f"Generated local script is missing: {local_script.name}")
                    run_robot_script(settings.robot_host.strip(), local_script.read_text(encoding="utf-8"), settings.robot_timeout_seconds)
                else:
                    run_robot_program(settings.robot_host.strip(), program_path, settings.robot_timeout_seconds)

            def run_and_wait(program_path: str, allow_retry: bool) -> bool:
                for attempt in range(2 if allow_retry else 1):
                    try:
                        run_script(program_path)
                        motion.status = "running"
                        motion.started_at = motion.started_at or datetime.now(timezone.utc).isoformat()
                        motion.retry_count += attempt
                        commit_or_conflict(session)
                        break
                    except (RobotDashboardError, RobotFileAccessError) as exc:
                        motion.retry_count += 1
                        if attempt or not allow_retry:
                            _finish_motion(session, motion, False, f"Robot program start failed: {exc}")
                            return False
                        session.commit()
                deadline = time.monotonic() + settings.pallet_motion_timeout_seconds
                observed_stage_motion = False
                settled_polls = 0
                while time.monotonic() < deadline:
                    try:
                        moving, _ = _robot_motion_activity(session)
                    except HTTPException as exc:
                        _finish_motion(session, motion, False, str(exc.detail))
                        return False
                    if moving:
                        motion.observed_busy = True
                        observed_stage_motion = True
                        settled_polls = 0
                        session.commit()
                    elif observed_stage_motion:
                        settled_polls += 1
                        if settled_polls >= 4:
                            return True
                    time.sleep(0.25)
                _finish_motion(session, motion, False, "Timed out waiting for the generated script to move the TCP and settle.")
                return False

            if motion.operation == "load_mill":
                if not run_and_wait(_motion_program(settings, motion.source_slot or 0, "pick"), True):
                    return
                if not run_and_wait(_mill_motion_program(settings, "load"), False):
                    return
                _finish_motion(session, motion, True)
                return
            if motion.operation == "unload_mill":
                if not run_and_wait(_mill_motion_program(settings, "unload"), True):
                    return
                if not run_and_wait(_motion_program(settings, motion.destination_slot or 0, "put"), False):
                    return
                _finish_motion(session, motion, True)
                return

            started = False
            for attempt in range(2):
                try:
                    if PurePosixPath(motion.program_path).suffix.lower() == ".script":
                        local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(motion.program_path).name
                        if not local_script.is_file():
                            raise RobotDashboardError(f"Generated local script is missing: {local_script.name}")
                        run_robot_script(
                            settings.robot_host.strip(),
                            local_script.read_text(encoding="utf-8"),
                            settings.robot_timeout_seconds,
                        )
                    else:
                        run_robot_program(settings.robot_host.strip(), motion.program_path, settings.robot_timeout_seconds)
                    motion.status = "running"
                    motion.started_at = datetime.now(timezone.utc).isoformat()
                    motion.retry_count = attempt
                    commit_or_conflict(session)
                    started = True
                    break
                except RobotDashboardError as exc:
                    motion.retry_count = attempt + 1
                    if attempt == 1:
                        _finish_motion(session, motion, False, f"Dashboard start failed before Busy: {exc}")
                        return
                    session.commit()
                except RobotFileAccessError as exc:
                    motion.retry_count = attempt + 1
                    if attempt == 1:
                        _finish_motion(session, motion, False, f"Generated script start failed before Busy: {exc}")
                        return
                    session.commit()
            if not started:
                return

            deadline = time.monotonic() + settings.pallet_motion_timeout_seconds
            settled_polls = 0
            while time.monotonic() < deadline:
                try:
                    moving, _ = _robot_motion_activity(session)
                except HTTPException as exc:
                    _finish_motion(session, motion, False, str(exc.detail))
                    return
                if moving:
                    motion.observed_busy = True
                    settled_polls = 0
                    session.commit()
                elif motion.observed_busy:
                    settled_polls += 1
                    if settled_polls >= 4:
                        _finish_motion(session, motion, True)
                        return
                time.sleep(0.25)
            _finish_motion(session, motion, False, "Timed out waiting for the generated script to move the TCP and settle.")
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            session.rollback()
            motion = session.get(RobotMotion, motion_id)
            if motion and motion.status in {"requested", "running"}:
                _finish_motion(session, motion, False, f"Unexpected pallet-motion worker failure: {exc}")


def _set_run_mode_status(
    session: Session,
    state: str,
    detail: str,
    *,
    pallet_id: str | None = None,
    return_slot: int | None = None,
) -> None:
    settings = get_settings(session)
    settings.run_mode_state = state
    settings.run_mode_detail = detail
    if pallet_id is not None:
        settings.run_mode_current_pallet_id = pallet_id
    if return_slot is not None:
        settings.run_mode_return_slot = return_slot
    bump(settings)
    commit_or_conflict(session)


def _finish_run_mode(session_factory, state: str, detail: str) -> None:
    with session_factory() as session:
        settings = get_settings(session)
        settings.run_mode_enabled = False
        settings.run_mode_state = state
        settings.run_mode_detail = detail
        settings.run_mode_pending_action = ""
        settings.run_mode_confirmation_token = ""
        settings.run_mode_confirmation_granted = False
        settings.run_mode_current_pallet_id = None
        settings.run_mode_return_slot = None
        bump(settings)
        commit_or_conflict(session)


def interrupt_run_mode(session: Session) -> None:
    """Never resume production commands implicitly after a backend restart."""
    settings = get_settings(session)
    if not settings.run_mode_enabled:
        return
    settings.run_mode_enabled = False
    settings.run_mode_state = "interrupted"
    settings.run_mode_detail = "Run mode was interrupted by a backend restart. Inspect the cell before starting again."
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    bump(settings)
    commit_or_conflict(session)


def set_run_mode_safety(session: Session, payload: SetRunModeSafety) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled:
        raise problem(409, "Stop run mode before changing its confirmation safety setting.")
    settings.run_mode_safety_confirm = payload.enabled
    bump(settings)
    commit_or_conflict(session)


def start_run_mode(session: Session, payload: StartRunMode) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled:
        raise problem(409, "Run mode is already active.")
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before starting run mode.")
    if session.scalar(select(Pallet).where(Pallet.location == "machine")):
        raise problem(409, "Empty the mill before starting run mode.")
    queue = session.scalars(
        select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
    ).all()
    if not queue:
        raise problem(409, "Add at least one pallet to the production queue before starting run mode.")
    for pallet in queue:
        if pallet.location != "pool" or not pallet.pool_slot_number:
            raise problem(409, f"{pallet.name} must be in a pallet-pool position before run mode can start.")
        if not pallet.program_path:
            raise problem(409, f"Assign a mill program to {pallet.name} before starting run mode.")
        if pallet.content_status in {"complete_parts", "defective_parts"}:
            raise problem(409, f"{pallet.name} is already marked {pallet.content_status.replace('_', ' ')}.")
    if settings.robot_connection_mode == "physical":
        if not settings.pallet_motion_enabled:
            raise problem(403, "Enable physical pallet movements before starting run mode.")
        if not settings.cnc_telemetry_enabled or not settings.cnc_host.strip():
            raise problem(409, "Enable and configure CNC telemetry before starting physical run mode.")
    settings.run_mode_enabled = True
    settings.run_mode_safety_confirm = payload.safety_confirm
    settings.run_mode_state = "starting"
    settings.run_mode_detail = f"Preparing {len(queue)} queued pallet{'s' if len(queue) != 1 else ''}."
    settings.run_mode_current_pallet_id = None
    settings.run_mode_return_slot = None
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    bump(settings)
    commit_or_conflict(session)


def stop_run_mode(session: Session, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if not settings.run_mode_enabled:
        return
    settings.run_mode_enabled = False
    settings.run_mode_state = "stopping"
    settings.run_mode_detail = "Stop requested. The current controller command will finish, but no next step will start."
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    bump(settings)
    commit_or_conflict(session)


def confirm_run_mode_action(session: Session, payload: ConfirmRunModeAction) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.run_mode_enabled or settings.run_mode_confirmation_token != payload.token:
        raise problem(409, "That run-mode confirmation is no longer active.")
    if not payload.approved:
        settings.run_mode_enabled = False
        settings.run_mode_state = "stopped"
        settings.run_mode_detail = "Operator declined the pending action. Run mode stopped."
        settings.run_mode_pending_action = ""
        settings.run_mode_confirmation_token = ""
        settings.run_mode_confirmation_granted = False
    else:
        settings.run_mode_confirmation_granted = True
        settings.run_mode_state = "approved"
        settings.run_mode_detail = "Operator approved the pending action."
    bump(settings)
    commit_or_conflict(session)


def _await_run_mode_action(session_factory, action: str, detail: str) -> bool:
    with session_factory() as session:
        settings = get_settings(session)
        if not settings.run_mode_enabled:
            return False
        if not settings.run_mode_safety_confirm:
            settings.run_mode_state = action
            settings.run_mode_detail = detail
            bump(settings)
            commit_or_conflict(session)
            return True
        token = str(uuid4())
        settings.run_mode_state = "waiting_confirmation"
        settings.run_mode_detail = detail
        settings.run_mode_pending_action = action
        settings.run_mode_confirmation_token = token
        settings.run_mode_confirmation_granted = False
        bump(settings)
        commit_or_conflict(session)

    while True:
        time.sleep(0.25)
        with session_factory() as session:
            settings = get_settings(session)
            if not settings.run_mode_enabled:
                return False
            if settings.run_mode_confirmation_token != token:
                return False
            if not settings.run_mode_confirmation_granted:
                continue
            settings.run_mode_state = action
            settings.run_mode_detail = detail
            settings.run_mode_pending_action = ""
            settings.run_mode_confirmation_token = ""
            settings.run_mode_confirmation_granted = False
            bump(settings)
            commit_or_conflict(session)
            return True


def _run_mode_program_path(program_path: str, extensions: set[str]) -> str:
    relative = PurePosixPath(program_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise problem(422, "Queued mill program paths must remain inside the PathPilot Gcode folder.")
    if relative.suffix.lower() not in extensions:
        raise problem(422, f"Queued program {program_path} is not an allowed mill program type.")
    return str(MILL_PROGRAM_DIRECTORY.joinpath(*relative.parts))


def _run_mode_motion_succeeded(session_factory, motion_id: str | None) -> bool:
    if motion_id is None:
        return True
    execute_pallet_motion(session_factory, motion_id)
    with session_factory() as session:
        motion = session.get(RobotMotion, motion_id)
        return bool(motion and motion.status == "succeeded")


def _run_mode_machine_cycle(session_factory, pallet_id: str) -> None:
    with session_factory() as session:
        settings = get_settings(session)
        pallet = session.get(Pallet, pallet_id)
        if not pallet or pallet.location != "machine" or not pallet.program_path:
            raise problem(409, "The run-mode pallet is no longer ready in the mill.")
        if settings.robot_connection_mode == "simulated":
            time.sleep(0.25)
            return
        remote_program = _run_mode_program_path(
            pallet.program_path,
            set(json.loads(settings.mill_program_extensions)),
        )
        connection = (
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds,
        )
        require_a = settings.cnc_require_a_axis_homed

    run_linuxcnc_program(*connection, remote_program, require_a)
    started = time.monotonic()
    saw_running = False
    while time.monotonic() - started < 24 * 60 * 60:
        with session_factory() as session:
            if not get_settings(session).run_mode_enabled:
                return
        telemetry = read_linuxcnc_snapshot(*connection)
        interpreter_state = telemetry.get("interp_state")
        if interpreter_state != 1:
            saw_running = True
        elif saw_running or time.monotonic() - started >= 1.0:
            return
        time.sleep(0.5)
    raise problem(504, "The mill program did not return to Idle within 24 hours.")


def execute_run_mode(session_factory) -> None:
    """Process queued pallets serially, stopping on the first uncertain state."""
    try:
        while True:
            with session_factory() as session:
                settings = get_settings(session)
                if not settings.run_mode_enabled:
                    return
                pallet = session.scalar(
                    select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
                )
                if not pallet:
                    _finish_run_mode(session_factory, "complete", "All queued pallets completed successfully.")
                    return
                if pallet.location != "pool" or not pallet.pool_slot_number or not pallet.program_path:
                    raise problem(409, f"{pallet.name} is not ready in a pool position with an assigned program.")
                pallet_id = pallet.id
                pallet_name = pallet.name
                return_slot = pallet.pool_slot_number
                _set_run_mode_status(
                    session, "preparing", f"Preparing {pallet_name} from Pool {return_slot:02d}.",
                    pallet_id=pallet_id, return_slot=return_slot,
                )

            if not _await_run_mode_action(
                session_factory, "loading", f"Load {pallet_name} from Pool {return_slot:02d} into the mill?",
            ):
                return
            with session_factory() as session:
                settings = get_settings(session)
                motion_id = start_mill_pallet_transfer(session, StartMillPalletTransfer(
                    expected_revision=settings.revision, operation="load", pallet_id=pallet_id,
                ), automated=True)
            if not _run_mode_motion_succeeded(session_factory, motion_id):
                raise problem(409, f"Mongo could not load {pallet_name}. Resolve the robot-motion fault before restarting run mode.")

            if not _await_run_mode_action(
                session_factory, "machining", f"Start {pallet_name}'s assigned mill program?",
            ):
                return
            _run_mode_machine_cycle(session_factory, pallet_id)

            if not _await_run_mode_action(
                session_factory, "unloading", f"Unload {pallet_name} and return it to Pool {return_slot:02d}?",
            ):
                return
            with session_factory() as session:
                settings = get_settings(session)
                motion_id = start_mill_pallet_transfer(session, StartMillPalletTransfer(
                    expected_revision=settings.revision, operation="unload", pallet_id=pallet_id,
                    pool_slot_number=return_slot,
                ), automated=True)
            if not _run_mode_motion_succeeded(session_factory, motion_id):
                raise problem(409, f"Mongo could not return {pallet_name}. Resolve the robot-motion fault before restarting run mode.")

            with session_factory() as session:
                settings = get_settings(session)
                pallet = session.get(Pallet, pallet_id)
                if pallet:
                    pallet.content_status = "complete_parts"
                settings.run_mode_current_pallet_id = None
                settings.run_mode_return_slot = None
                settings.run_mode_state = "advancing"
                settings.run_mode_detail = f"{pallet_name} completed. Advancing to the next queued pallet."
                bump(settings)
                commit_or_conflict(session)
    except HTTPException as exc:
        _finish_run_mode(session_factory, "faulted", str(exc.detail))
    except (CncTelemetryError, RobotDashboardError, RobotFileAccessError) as exc:
        _finish_run_mode(session_factory, "faulted", str(exc))
    except Exception as exc:  # pragma: no cover - defensive coordinator boundary
        _finish_run_mode(session_factory, "faulted", f"Unexpected run-mode failure: {exc}")


def recover_pallet_motion(session: Session, motion_id: str, payload: RecoverPalletMotion) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    motion = session.get(RobotMotion, motion_id)
    if not motion or motion.status != "faulted":
        raise problem(409, "That pallet motion is not awaiting recovery.")
    pallet = session.get(Pallet, motion.pallet_id)
    if not pallet:
        raise problem(404, "Pallet no longer exists.")
    allowed = {
        "pick": {"source_pool", "robot_held"},
        "put": {"robot_held", "destination_pool"},
        "load_mill": {"source_pool", "robot_held", "machine"},
        "unload_mill": {"machine", "robot_held", "destination_pool"},
    }.get(motion.operation, set())
    if payload.resolution not in allowed:
        raise problem(422, "That recovery state is not valid for this movement.")
    if payload.resolution == "machine":
        occupant = session.scalar(select(Pallet).where(Pallet.location == "machine", Pallet.id != pallet.id))
        if occupant:
            raise problem(409, "Another pallet is already marked as being in the mill.")
        pallet.location, pallet.pool_slot_number = "machine", None
        settings.machine_state = "running"
    elif payload.resolution == "source_pool":
        pallet.location, pallet.pool_slot_number = "pool", motion.source_slot
    elif payload.resolution == "destination_pool":
        occupant = session.scalar(select(Pallet).where(Pallet.location == "pool", Pallet.pool_slot_number == motion.destination_slot, Pallet.id != pallet.id))
        if occupant:
            raise problem(409, "The destination pool position is now occupied.")
        pallet.location, pallet.pool_slot_number = "pool", motion.destination_slot
    else:
        held = session.scalar(select(Pallet).where(Pallet.location == "robot_held", Pallet.id != pallet.id))
        if held:
            raise problem(409, "Another pallet is already marked Robot-held.")
        pallet.location, pallet.pool_slot_number = "robot_held", None
    motion.status = "reconciled"
    motion.completed_at = datetime.now(timezone.utc).isoformat()
    motion.failure_detail = f"{motion.failure_detail or 'Robot movement fault.'} Reconciled as {payload.resolution}."
    bump(settings)
    commit_or_conflict(session)


def interrupt_active_pallet_motion(session: Session) -> None:
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if not motion:
        return
    motion.status = "faulted"
    motion.completed_at = datetime.now(timezone.utc).isoformat()
    motion.failure_detail = "Backend restarted while this motion was active. Inspect the cell and reconcile the pallet location."
    bump(get_settings(session))
    commit_or_conflict(session)


def rebuild_pallet_motion_scripts(session: Session) -> dict:
    settings = get_settings(session)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before rebuilding scripts.")
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Generated scripts require a configured physical robot.")
    if not settings.robot_file_access_enabled:
        raise problem(409, "Enable SFTP robot file access before rebuilding generated scripts.")
    if not settings.robot_file_username or not settings.robot_file_password:
        raise problem(409, "Enter robot SFTP credentials before rebuilding generated scripts.")
    generation = pallet_motion_generation(settings)
    grip = generation.get("grip_output")
    if grip is not None and (
        not isinstance(grip, dict)
        or grip.get("bank") not in {"standard", "configurable", "tool"}
        or not isinstance(grip.get("index"), int)
    ):
        raise problem(422, "The optional gripper output must be a valid robot output channel.")
    for action_key, action_label in (
        ("door_open_action", "door open"),
        ("door_close_action", "door close"),
        ("erowa_unlock_action", "Erowa unlock"),
        ("erowa_lock_action", "Erowa lock"),
    ):
        action = generation.get(action_key)
        output = action.get("output") if isinstance(action, dict) else None
        if action is not None and (
            not isinstance(output, dict)
            or output.get("bank") not in {"standard", "configurable", "tool"}
            or not isinstance(output.get("index"), int)
        ):
            raise problem(422, f"The {action_label} command must use a valid robot output channel.")
    if not isinstance(generation.get("safe_pre_waypoint"), dict):
        raise problem(422, "Configure the shared safe waypoint before rebuilding scripts.")
    waypoints = generation.get("travel_waypoints", [])
    names = [item.get("name", "").casefold() for item in waypoints if isinstance(item, dict)]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise problem(422, "Travel waypoint names must be present and unique.")

    locations = pallet_location_positions(settings)
    files: dict[str, str] = {}
    mappings: list[dict] = []
    for pool in locations["pool_locations"]:
        slot = pool["slot"]
        pick_name = f"pick_pool_{slot:03d}.script"
        put_name = f"put_pool_{slot:03d}.script"
        files[pick_name] = build_pallet_motion_script(
            function_name=f"mps_pick_pool_{slot:03d}", operation="pick", position=pool, generation=generation,
        )
        files[put_name] = build_pallet_motion_script(
            function_name=f"mps_put_pool_{slot:03d}", operation="put", position=pool, generation=generation,
        )
        mappings.append({"slot": slot, "pick_program": pick_name, "put_program": put_name})
    enabled_stations = []
    if settings.on_deck_enabled:
        enabled_stations.append(("on_deck", locations["on_deck_location"]))
    if settings.dripping_enabled:
        enabled_stations.append(("dripping", locations["dripping_location"]))
    for station, position in enabled_stations:
        for operation in ("pick", "put"):
            name = f"{operation}_{station}.script"
            files[name] = build_pallet_motion_script(
                function_name=f"mps_{operation}_{station}", operation=operation, position=position, generation=generation,
            )
    for operation in ("load", "unload"):
        name = f"{operation}_mill.script"
        files[name] = _mill_motion_script_content(settings, operation)

    try:
        remote_paths = sync_generated_scripts(
            host=settings.robot_file_host or settings.robot_host.strip(),
            port=settings.robot_file_port,
            username=settings.robot_file_username,
            password=settings.robot_file_password,
            root_directory=settings.robot_file_directory,
            timeout_seconds=settings.robot_timeout_seconds,
            local_directory=generated_script_directory(Path(__file__).parents[1]),
            files=files,
        )
    except RobotFileAccessError as exc:
        raise problem(502, str(exc)) from exc
    settings.pallet_motion_programs = json.dumps(
        [
            {
                "slot": item["slot"],
                "pick_program": remote_paths[item["pick_program"]],
                "put_program": remote_paths[item["put_program"]],
            }
            for item in mappings
        ],
        separators=(",", ":"),
    )
    settings.generated_motion_script_signature = _motion_script_signature(settings)
    bump(settings)
    commit_or_conflict(session)
    return {"files": sorted(remote_paths.values()), "local_directory": str(generated_script_directory(Path(__file__).parents[1]))}


def queue_pallet(
    session: Session,
    pallet_id: str,
    payload: QueuePallet,
) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    assert_run_mode_inactive(settings)
    _assert_no_locked_motion(session)
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
    assert_run_mode_inactive(settings)
    _assert_no_locked_motion(session)
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
    assert_run_mode_inactive(settings)
    _assert_no_locked_motion(session)
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
    assert_run_mode_inactive(settings)
    _assert_no_locked_motion(session)
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
    assert_run_mode_inactive(settings)
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
    if payload.on_deck_enabled is False and session.scalar(select(Pallet).where(Pallet.location == "on_deck")):
        raise problem(409, "Move the pallet out of On deck before disabling that station.")
    if payload.dripping_enabled is False and session.scalar(select(Pallet).where(Pallet.location == "dripping")):
        raise problem(409, "Move the pallet out of Dripping before disabling that station.")
    if payload.on_deck_enabled is not None:
        settings.on_deck_enabled = payload.on_deck_enabled
    if payload.dripping_enabled is not None:
        settings.dripping_enabled = payload.dripping_enabled

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
        payload.robot_mill_load_unload.model_dump() if payload.robot_mill_load_unload is not None else None,
        payload.robot_mill_safe_entry_exit.model_dump() if payload.robot_mill_safe_entry_exit is not None else None,
        payload.mill_load_unload_g53.model_dump() if payload.mill_load_unload_g53 is not None else None,
        payload.mill_pallet_change_g53.model_dump() if payload.mill_pallet_change_g53 is not None else None,
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
    if payload.debug_mill_program_button_count is not None:
        settings.debug_mill_program_button_count = payload.debug_mill_program_button_count
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
    if payload.cnc_require_a_axis_homed is not None:
        settings.cnc_require_a_axis_homed = payload.cnc_require_a_axis_homed
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
    if payload.workholding_library is not None:
        settings.workholding_library = json.dumps(payload.workholding_library, separators=(",", ":"))
    if payload.pallet_motion_enabled is not None:
        settings.pallet_motion_enabled = payload.pallet_motion_enabled
    if payload.pallet_motion_timeout_seconds is not None:
        settings.pallet_motion_timeout_seconds = payload.pallet_motion_timeout_seconds
    if payload.pallet_motion_programs is not None:
        mappings = [item.model_dump() for item in payload.pallet_motion_programs]
        slots = [item["slot"] for item in mappings]
        if len(slots) != len(set(slots)):
            raise problem(422, "Each pool position can have only one pallet-motion program mapping.")
        if any(slot > payload.pool_slot_count for slot in slots):
            raise problem(422, "A pallet-motion mapping is outside the configured pool capacity.")
        settings.pallet_motion_programs = json.dumps(mappings, separators=(",", ":"))
    if payload.pallet_motion_generation is not None:
        generation = payload.pallet_motion_generation.model_dump()
        waypoint_names = [item["name"].casefold() for item in generation["travel_waypoints"]]
        if len(waypoint_names) != len(set(waypoint_names)):
            raise problem(422, "Travel waypoint names must be unique.")
        settings.pallet_motion_generation = json.dumps(generation, separators=(",", ":"))
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


def _validate_mill_debug_program(settings: AppSettings, filename: str) -> str:
    path = PurePosixPath(filename)
    root = MILL_PROGRAM_DIRECTORY
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root):
        raise problem(422, "Mill program filename must be inside /home/operator/gcode/Gcode.")
    if path.suffix.lower() not in set(json.loads(settings.mill_program_extensions)):
        raise problem(422, "Mill program filename does not match the configured Mill program extensions.")
    return str(path)


def configure_debug_mill_program(session: Session, payload: ConfigureDebugMillProgram) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if payload.index >= settings.debug_mill_program_button_count:
        raise problem(422, "That mill program button is not enabled in Settings.")
    filename = payload.filename.strip()
    if filename:
        filename = _validate_mill_debug_program(settings, filename)
    buttons = _load_debug_program_buttons(settings, "mill")
    buttons[payload.index] = {
        "display_name": payload.display_name or f"Mill program {payload.index + 1}",
        "filename": filename,
        "color": payload.color,
    }
    _store_debug_program_buttons(settings, buttons, "mill")
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


def mill_program_files(session: Session) -> list[str]:
    settings = get_settings(session)
    if not settings.cnc_host.strip() or not settings.cnc_ssh_username:
        raise problem(409, "Configure the PathPilot SSH connection in Settings first.")
    try:
        return list_robot_program_files(
            host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
            username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
            directory=str(MILL_PROGRAM_DIRECTORY),
            extensions=set(json.loads(settings.mill_program_extensions)),
            timeout_seconds=settings.cnc_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, str(exc)) from exc


def run_debug_mill_program(session: Session, payload: RunDebugMillProgram) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.cnc_telemetry_enabled or not settings.cnc_host.strip() or not settings.cnc_ssh_username:
        raise problem(409, "Running mill programs requires configured, enabled PathPilot telemetry and SSH access.")
    buttons = _load_debug_program_buttons(settings, "mill")
    if payload.index >= len(buttons):
        raise problem(422, "That mill program button is not enabled in Settings.")
    filename = buttons[payload.index]["filename"]
    if not filename:
        raise problem(422, "Configure a mill filename for this program button first.")
    try:
        run_linuxcnc_program(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds, filename,
            settings.cnc_require_a_axis_homed,
        )
    except CncTelemetryError as exc:
        raise problem(502, f"Mill program was not started: {exc}") from exc


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
