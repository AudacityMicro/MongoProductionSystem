from __future__ import annotations

import json
import math
import hashlib
import os
import random
import re
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from copy import deepcopy
from threading import Event, RLock, Thread
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.diagnostics import diagnostics
from app.cameras import camera_manager, parse_camera_devices
from app.program_metadata import parse_program_metadata, unavailable_program_metadata, PROGRAM_METADATA_PREFIX_BYTES
from app.models import (
    AppSettings,
    MillSupervisorCommand,
    Pallet,
    ProgramCompletionStat,
    ProgramRuntime,
    RecoverySession,
    RobotMotion,
    RobotReliabilityRun,
    RobotSupervisorCommand,
)
from app.autoschedule import ScheduleJob, optimize_tool_schedule, simulate_tool_plan
from app.cnc_linuxcnc import (
    CncProgramFault,
    CncTelemetryError,
    read_linuxcnc_cycle_state,
    read_linuxcnc_io_labels,
    read_linuxcnc_snapshot,
    run_linuxcnc_network_ping_matrix,
    run_linuxcnc_program,
    start_mill_supervisor_agent,
    stop_mill_supervisor_agent,
    test_mill_supervisor_runtime,
)
from app.mill_supervisor import mill_supervisor
from app.robot_dashboard import (
    RobotDashboardError,
    clear_robot_fault,
    robot_dashboard_health,
    robot_program_status,
    robot_program_running,
    run_robot_program,
)
from app.robot_files import (
    RobotFileAccessError,
    delete_robot_path,
    list_robot_program_files,
    read_robot_file,
    read_robot_file_prefix,
    remote_file_signature,
    upload_robot_file,
    write_robot_text_file,
)
from app.robot_scripts import (
    PALLET_MOTION_SCRIPT_REVISION,
    GENERATED_REMOTE_DIRECTORY,
    build_mill_pallet_motion_script,
    build_pallet_motion_script,
    build_reliability_motion_script,
    build_robot_supervisor_script,
    generated_script_directory,
    RobotScriptTransferUncertain,
    run_robot_script,
    sync_generated_scripts,
    with_pallet_payload,
    with_supervisor_sequence,
)
from app.robot_supervisor import (
    EVENT_ACCEPTED,
    EVENT_COMPLETED,
    EVENT_FAULTED,
    EVENT_LATCHED,
    EVENT_RUNNING,
    FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION,
    OP_CLEAR_LATCH,
    OP_ALIGN_PALLET_PICK,
    OP_ENTER_MAINTENANCE,
    OP_LOAD_MILL,
    OP_PICK_POOL,
    OP_PUT_POOL,
    OP_RELIABILITY_POOL,
    OP_SET_CONFIGURABLE_OUTPUT,
    OP_SET_STANDARD_OUTPUT,
    OP_SET_TOOL_OUTPUT,
    OP_UNLOAD_MILL,
    robot_supervisor,
)
from app.pallet_names import PALLET_NAMES
from app.robot_rtde import (
    RobotTelemetryError,
    read_robot_snapshot,
    reset_robot_connections,
    set_robot_digital_output,
    set_robot_digital_outputs,
    toggle_robot_digital_output,
)
from app.schemas import (
    ClearRobotFault,
    MillSupervisorActivation,
    MillSupervisorReconcile,
    CreatePallet,
    MovePallet,
    ManualReturnPallet,
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
    SupervisorReconcile,
    ToggleDebugIo,
    RunDebugProgram,
    RunDebugMillProgram,
    RunDebugMillPalletMotion,
    RunDebugPalletMotion,
    RecoveryAnswer,
    UpdatePallet,
)


# Long machining cycles are observed over an unreliable network. The monitor
# must never equate one lost SSH response with a stopped or failed program.
_CNC_LONG_CYCLE_MAXIMUM_SECONDS = 30 * 24 * 60 * 60
_CNC_RUNNING_POLL_SECONDS = 2.0
_CNC_TELEMETRY_RETRY_MAX_SECONDS = 30.0
_CNC_TELEMETRY_STATUS_INTERVAL_SECONDS = 30.0
_RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS = 8
_RUN_MODE_PRE_DISPATCH_RECOVERY_MAX_SECONDS = 30.0
# Before a program is dispatched, the mill is still Idle and the robot is
# waiting.  A lossy network is therefore safe to wait out for longer than the
# initial Run Mode startup check.  No machining command is retried here.
_RUN_MODE_CNC_PRE_DISPATCH_RECOVERY_ATTEMPTS = 20
_RUN_MODE_CNC_PRE_DISPATCH_HELPER_RESTART_ATTEMPT = 5
_RUN_MODE_CNC_PRE_DISPATCH_HELPER_RESTART_INTERVAL = 4


class CncPreDispatchTelemetryError(CncTelemetryError):
    """A PathPilot telemetry failure observed before a program was sent."""


def _is_transient_robot_pre_dispatch_detail(detail: str) -> bool:
    normalized = detail.casefold()
    return any(
        marker in normalized
        for marker in (
            "live robot telemetry",
            "robot telemetry",
            "realtime telemetry",
            "no realtime telemetry packet",
            "supervisor is not connected",
            "supervisor connection",
            "connection reset",
            "network is unreachable",
            "timed out",
        )
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


_RUN_MODE_SAFE_SETTINGS_FIELDS = frozenset({
    "weight_unit",
    "camera_devices",
    "camera_idle_id",
    "camera_loading_id",
    "camera_machining_id",
    "camera_recording_enabled",
    "camera_recording_path",
    "camera_recording_retention_days",
    "camera_width",
    "camera_height",
    "camera_fps",
    "camera_segment_seconds",
    "push_notifications_enabled",
    "push_notification_server",
    "push_notification_topic",
    "push_notification_token",
    "push_notify_errors",
    "push_notify_completed_pallets",
    "push_notify_queue_empty",
    "background_stack_light_intensity",
    "fusion_tool_library_path",
    "workholding_library",
})


def assert_settings_safe_during_run(settings: AppSettings, payload: SettingsUpdate) -> None:
    """Allow only settings that cannot change robot or mill behavior while Run Mode owns the cell."""
    if not settings.run_mode_enabled:
        return
    requested = payload.model_fields_set - {"expected_revision"}
    unsafe = requested - _RUN_MODE_SAFE_SETTINGS_FIELDS
    if unsafe:
        raise problem(
            409,
            "Run Mode is active. Save camera, notification, display-unit, tool-library, or workholding changes separately; "
            "stop Run Mode before changing robot, mill, pool, queue, stack-light, or controller settings.",
        )


def assert_pallet_manageable_during_run(settings: AppSettings, pallet: Pallet) -> None:
    if settings.run_mode_enabled and pallet.location == "machine":
        raise problem(409, "The pallet in the mill cannot be changed while Run Mode is active.")


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


_PROGRAM_SCAN_CACHE: dict[tuple[str, str], tuple[float, list[str], str | None]] = {}
_PROGRAM_SCAN_LOCK = RLock()
_PALLET_PROGRAM_REMOTE_CACHE: dict[tuple[object, ...], tuple[float, list[str]]] = {}
_PALLET_PROGRAM_REMOTE_LOCK = RLock()
_NETWORK_TEST_LOCK = RLock()
_NETWORK_TEST_ACTIVE = False
_NETWORK_TEST_LAST_AUTOMATIC_START = 0.0
_NETWORK_TEST_LAST_MANUAL_START = 0.0
_NETWORK_TEST_LATEST: dict[str, object] | None = None


def _scan_available_programs(settings: AppSettings) -> tuple[list[str], str | None]:
    if not settings.source_folder.strip():
        return [], "No program source folder is configured."
    root = Path(settings.source_folder).expanduser()
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return [], "The configured program source folder is unavailable."
    if not root.is_dir():
        return [], "The configured program source path is not a folder."

    if root.name.casefold() == "gcode":
        program_root = root
    else:
        try:
            program_root = next(
                (child for child in root.iterdir() if child.is_dir() and child.name.casefold() == "gcode"),
                None,
            )
        except OSError:
            return [], "The configured program source folder could not be read completely."
        if program_root is None:
            return [], "The configured program source is missing its Gcode subfolder."

    extensions = set(json.loads(settings.program_extensions))
    programs: list[str] = []
    try:
        for path in program_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in extensions:
                resolved = path.resolve()
                if resolved.is_relative_to(program_root):
                    programs.append(resolved.relative_to(program_root).as_posix())
    except OSError:
        return [], "The program source folder could not be read completely."
    return sorted(programs, key=str.casefold), None


def available_programs(settings: AppSettings, *, force: bool = False) -> tuple[list[str], str | None]:
    key = (settings.source_folder.strip(), settings.program_extensions)
    with _PROGRAM_SCAN_LOCK:
        cached = _PROGRAM_SCAN_CACHE.get(key)
        now = time.monotonic()
        # Network program folders are operator-refreshed. Rescanning an SMB share
        # from every board poll can freeze scheduling whenever the mill is offline.
        if not force and cached:
            return list(cached[1]), cached[2]
        programs, warning = _scan_available_programs(settings)
        _PROGRAM_SCAN_CACHE[key] = (now, list(programs), warning)
        return programs, warning


_NETWORK_PING_COUNT = 5


def _desktop_ping_path(label: str, target: str, count: int = _NETWORK_PING_COUNT) -> dict[str, object]:
    """Run one bounded ICMP path from the application computer."""
    command = ["ping", "-n", str(count), "-w", "1000", target] if os.name == "nt" else ["ping", "-c", str(count), "-W", "1", target]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=count + 5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        return {"source": "Desktop", "target": label, "host": target, "method": "icmp", "supported": True, "error": "The system ping utility is unavailable."}
    except subprocess.TimeoutExpired as exc:
        return {"source": "Desktop", "target": label, "host": target, "method": "icmp", "supported": True, "error": "Ping timed out before completing."}
    except OSError as exc:
        return {"source": "Desktop", "target": label, "host": target, "method": "icmp", "supported": True, "error": str(exc)}

    output = f"{result.stdout}\n{result.stderr}"
    values = [float(value.replace(",", ".")) for value in re.findall(r"(?:time|zeit|temps|tiempo)\s*[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", output, flags=re.IGNORECASE)]
    received_match = re.search(r"received\s*=\s*(\d+)", output, flags=re.IGNORECASE)
    received = min(count, int(received_match.group(1))) if received_match else min(count, len(values))
    loss_percent = round((count - received) * 100 / count, 1)
    transit_times = [round(value, 3) for value in values]
    return {
        "source": "Desktop",
        "target": label,
        "host": target,
        "method": "icmp",
        "supported": True,
        "sent": count,
        "received": received,
        "packet_loss_percent": loss_percent,
        "minimum_ms": min(transit_times) if transit_times else None,
        "average_ms": round(sum(transit_times) / len(transit_times), 3) if transit_times else None,
        "maximum_ms": max(transit_times) if transit_times else None,
        "transit_times_ms": transit_times,
    }


def _network_targets(settings: AppSettings | None) -> list[dict[str, str]]:
    targets = [{"label": "Internet (8.8.8.8)", "host": "8.8.8.8"}]
    if settings:
        targets[:0] = [
            {"label": "Mongo", "host": settings.robot_host.strip()},
            {"label": "Mill", "host": settings.cnc_host.strip()},
        ]
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in targets:
        host = item["host"]
        if host and host not in seen:
            unique.append(item)
            seen.add(host)
    return unique


def _run_network_diagnostic(settings: AppSettings | None = None) -> dict[str, object]:
    """Build a bounded, capability-aware connectivity matrix for the cell."""
    targets = _network_targets(settings)
    paths: list[dict[str, object]] = [_desktop_ping_path(item["label"], item["host"]) for item in targets]
    if settings and settings.cnc_host.strip() and settings.cnc_ssh_username:
        mill_targets = [
            {"label": "Application", "host": settings.robot_supervisor_hostname.strip()},
            {"label": "Mongo", "host": settings.robot_host.strip()},
            {"label": "Internet (8.8.8.8)", "host": "8.8.8.8"},
        ]
        mill_targets = [item for item in mill_targets if item["host"] and item["host"] != settings.cnc_host.strip()]
        try:
            paths.extend(run_linuxcnc_network_ping_matrix(
                settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                settings.cnc_ssh_password, settings.cnc_timeout_seconds, mill_targets, _NETWORK_PING_COUNT,
            ))
        except CncTelemetryError as exc:
            paths.extend({
                "source": "Mill", "target": item["label"], "host": item["host"], "method": "icmp",
                "supported": True, "error": str(exc),
            } for item in mill_targets)
    elif settings:
        paths.append({"source": "Mill", "target": "Network", "method": "icmp", "supported": False, "detail": "PathPilot SSH is not configured."})

    supervisor = robot_supervisor().status()
    desktop_host = settings.robot_supervisor_hostname.strip() if settings else "Application"
    paths.append({
        "source": "Mongo", "target": "Application", "host": desktop_host, "method": "supervisor-tcp",
        "supported": True, "connected": bool(supervisor.get("connected")),
        "detail": "Observed through the robot-originated supervisor connection; Universal Robots does not expose an OS ICMP shell.",
    })
    for item in targets:
        if item["label"] == "Mongo":
            continue
        paths.append({
            "source": "Mongo", "target": item["label"], "host": item["host"], "method": "icmp",
            "supported": False, "detail": "Universal Robots does not expose a supported OS ICMP command interface.",
        })
    public = next((path for path in paths if path.get("source") == "Desktop" and path.get("host") == "8.8.8.8"), {})
    return {
        "version": "MPS-NETWORK-MATRIX-V1",
        "packet_count": _NETWORK_PING_COUNT,
        "paths": paths,
        # Retain the established public-path fields for existing integrations.
        "target": "8.8.8.8",
        "sent": public.get("sent", 0),
        "received": public.get("received", 0),
        "packet_loss_percent": public.get("packet_loss_percent", 100.0),
        "minimum_ms": public.get("minimum_ms"),
        "average_ms": public.get("average_ms"),
        "maximum_ms": public.get("maximum_ms"),
        "transit_times_ms": public.get("transit_times_ms", []),
    }


def _finish_network_diagnostic(trigger: str, settings: AppSettings | None = None) -> None:
    global _NETWORK_TEST_ACTIVE, _NETWORK_TEST_LATEST
    try:
        result = _run_network_diagnostic(settings)
        latest: dict[str, object] = {"trigger": trigger, "completed_at": datetime.now(timezone.utc).isoformat(), "result": result}
    except HTTPException as exc:
        latest = {"trigger": trigger, "completed_at": datetime.now(timezone.utc).isoformat(), "error": str(exc.detail)}
    except Exception as exc:  # Keep an automatic diagnostic failure from affecting robot telemetry.
        latest = {"trigger": trigger, "completed_at": datetime.now(timezone.utc).isoformat(), "error": f"Network test failed: {exc}"}
    with _NETWORK_TEST_LOCK:
        _NETWORK_TEST_LATEST = latest
        _NETWORK_TEST_ACTIVE = False
    diagnostics().record(
        "network",
        "diagnostic_completed",
        "Network diagnostic completed.",
        severity="error" if latest.get("error") else "info",
        details=latest,
    )


def network_diagnostic(settings: AppSettings | None = None) -> dict[str, object]:
    """Run a user-requested network test, limited to one start per 30 seconds."""
    global _NETWORK_TEST_ACTIVE, _NETWORK_TEST_LAST_MANUAL_START, _NETWORK_TEST_LATEST
    now = time.monotonic()
    with _NETWORK_TEST_LOCK:
        if _NETWORK_TEST_ACTIVE:
            raise problem(409, "A network test is already running.")
        remaining = 30 - (now - _NETWORK_TEST_LAST_MANUAL_START)
        if remaining > 0:
            raise problem(429, f"Wait {math.ceil(remaining)} seconds before starting another manual network test.")
        _NETWORK_TEST_ACTIVE = True
        _NETWORK_TEST_LAST_MANUAL_START = now
    _finish_network_diagnostic("manual", settings)
    with _NETWORK_TEST_LOCK:
        latest = deepcopy(_NETWORK_TEST_LATEST)
    if latest and isinstance(latest.get("result"), dict):
        return latest["result"]
    raise problem(503, str(latest.get("error") if latest else "Network test did not return a result."))


def trigger_network_diagnostic_on_robot_loss() -> None:
    """Start at most one background test every three minutes after live telemetry fails."""
    global _NETWORK_TEST_ACTIVE, _NETWORK_TEST_LAST_AUTOMATIC_START
    now = time.monotonic()
    with _NETWORK_TEST_LOCK:
        if _NETWORK_TEST_ACTIVE or now - _NETWORK_TEST_LAST_AUTOMATIC_START < 180:
            return
        _NETWORK_TEST_ACTIVE = True
        _NETWORK_TEST_LAST_AUTOMATIC_START = now
    Thread(target=_finish_network_diagnostic, args=("automatic_robot_disconnect",), daemon=True, name="network-diagnostic").start()


def network_diagnostic_status() -> dict[str, object]:
    with _NETWORK_TEST_LOCK:
        return {"active": _NETWORK_TEST_ACTIVE, "latest": deepcopy(_NETWORK_TEST_LATEST)}


def diagnostic_snapshot(session: Session, limit: int = 200) -> dict[str, object]:
    """Return a redacted support snapshot without contacting either controller."""
    settings = get_settings(session)
    supervisor = robot_supervisor().status()
    commands = session.scalars(
        select(RobotSupervisorCommand)
        .order_by(RobotSupervisorCommand.sequence.desc())
        .limit(100)
    ).all()
    motions = session.scalars(
        select(RobotMotion)
        .order_by(RobotMotion.created_at.desc())
        .limit(100)
    ).all()
    reliability_runs = session.scalars(
        select(RobotReliabilityRun)
        .order_by(RobotReliabilityRun.created_at.desc())
        .limit(20)
    ).all()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings_revision": settings.revision,
        "controller_configuration": {
            "robot_mode": settings.robot_connection_mode,
            "robot_host": settings.robot_host,
            "robot_port": settings.robot_port,
            "robot_poll_hz": settings.robot_poll_hz,
            "robot_timeout_seconds": settings.robot_timeout_seconds,
            "supervisor_enabled": settings.robot_supervisor_enabled,
            "supervisor_activation_verified": settings.robot_supervisor_activation_verified,
            "supervisor_hostname": settings.robot_supervisor_hostname,
            "supervisor_listen_host": settings.robot_supervisor_listen_host,
            "supervisor_port": settings.robot_supervisor_port,
            "supervisor_heartbeat_seconds": settings.robot_supervisor_heartbeat_seconds,
            "supervisor_telemetry_hz": settings.robot_supervisor_telemetry_hz,
            "cnc_enabled": settings.cnc_telemetry_enabled,
            "cnc_host": settings.cnc_host,
            "cnc_ssh_port": settings.cnc_ssh_port,
            "run_mode_enabled": settings.run_mode_enabled,
            "run_mode_state": settings.run_mode_state,
        },
        "supervisor": supervisor,
        "network_test": network_diagnostic_status(),
        "recent_supervisor_commands": [
            {
                "id": item.id,
                "sequence": item.sequence,
                "robot_session": item.robot_session,
                "app_session": item.app_session,
                "motion_id": item.robot_motion_id,
                "operation": item.operation,
                "opcode": item.opcode,
                "argument": item.argument,
                "transport": item.transport,
                "status": item.status,
                "attempted": item.attempted,
                "created_at": item.created_at,
                "sent_at": item.sent_at,
                "accepted_at": item.accepted_at,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
                "result_code": item.result_code,
                "fault_detail": item.fault_detail,
            }
            for item in commands
        ],
        "recent_robot_motions": [
            {
                "id": item.id,
                "pallet_id": item.pallet_id,
                "operation": item.operation,
                "source_slot": item.source_slot,
                "destination_slot": item.destination_slot,
                "status": item.status,
                "retry_count": item.retry_count,
                "created_at": item.created_at,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
                "failure_detail": item.failure_detail,
            }
            for item in motions
        ],
        "recent_reliability_runs": [_reliability_run_item(item) for item in reliability_runs],
        "events": diagnostics().recent(limit),
    }


def validate_program(program_path: str | None, programs: set[str]) -> str | None:
    if not program_path:
        return None
    normalized = Path(program_path.replace("\\", "/")).as_posix()
    if normalized.startswith("../") or Path(normalized).is_absolute():
        raise problem(422, "Program path must be relative to the Gcode folder.")
    if normalized not in programs:
        raise problem(422, "Selected program is not available in the Gcode folder.")
    return normalized


def program_metadata(
    program_path: str | None,
    content_status: str,
    tools: list[str] | None = None,
    expected_cycle_seconds: int | None = None,
    state: str = "unavailable",
    detail: str = "",
    cycle_basis: str | None = None,
    wcs: list[str] | None = None,
    tool_counts: dict[str, int] | None = None,
) -> dict:
    hidden = not program_path or content_status in {"complete_parts", "defective_parts"}
    return {
        "program_tools": [] if hidden else list(tools or []),
        "program_tool_counts": {} if hidden else dict(tool_counts or {}),
        "program_wcs": [] if hidden else list(wcs or []),
        "expected_cycle_seconds": None if hidden else expected_cycle_seconds,
        "program_metadata_state": state,
        "program_metadata_detail": detail,
        "program_cycle_basis": cycle_basis,
    }


def _local_program_path(settings: AppSettings, program_path: str) -> Path | None:
    if not settings.source_folder.strip():
        return None
    root = Path(settings.source_folder).expanduser()
    try:
        root = root.resolve(strict=True)
        program_root = root if root.name.casefold() == "gcode" else next(
            (child for child in root.iterdir() if child.is_dir() and child.name.casefold() == "gcode"),
            None,
        )
        if program_root is None:
            return None
        target = (program_root / program_path).resolve(strict=True)
        return target if target.is_file() and target.is_relative_to(program_root.resolve()) else None
    except (OSError, RuntimeError):
        return None


def read_assigned_program_metadata(settings: AppSettings, program_path: str) -> dict[str, object]:
    remote_configured = bool(
        settings.mill_programs_page_enabled
        and settings.cnc_host.strip()
        and settings.cnc_ssh_username
        and settings.cnc_ssh_password
    )
    if remote_configured:
        try:
            prefix = read_robot_file_prefix(
                host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
                username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
                directory=settings.mill_file_directory, path=program_path,
                timeout_seconds=settings.cnc_timeout_seconds,
                limit=PROGRAM_METADATA_PREFIX_BYTES,
            )
            return parse_program_metadata(str(prefix["text"]))
        except RobotFileAccessError as exc:
            return unavailable_program_metadata(f"Could not read the assigned PathPilot program header: {exc}")

    local_path = _local_program_path(settings, program_path)
    if local_path is None:
        return unavailable_program_metadata("The assigned program file is unavailable for metadata inspection.")
    try:
        with local_path.open("r", encoding="utf-8", errors="replace") as source:
            return parse_program_metadata(source.read(PROGRAM_METADATA_PREFIX_BYTES))
    except OSError as exc:
        return unavailable_program_metadata(f"Could not read the assigned program header: {exc}")


def _store_pallet_program_metadata(pallet: Pallet, metadata: dict[str, object]) -> None:
    pallet.program_tools_json = json.dumps(metadata.get("program_tools") or [], separators=(",", ":"))
    pallet.program_tool_counts_json = json.dumps(metadata.get("program_tool_counts") or {}, separators=(",", ":"), sort_keys=True)
    pallet.program_wcs_json = json.dumps(metadata.get("program_wcs") or [], separators=(",", ":"))
    pallet.expected_cycle_seconds = metadata.get("expected_cycle_seconds")
    pallet.program_metadata_state = str(metadata.get("program_metadata_state") or "unavailable")
    pallet.program_metadata_detail = str(metadata.get("program_metadata_detail") or "")[:500]
    pallet.program_cycle_basis = str(metadata["program_cycle_basis"])[:100] if metadata.get("program_cycle_basis") else None


def _clear_pallet_program_metadata(pallet: Pallet) -> None:
    _store_pallet_program_metadata(pallet, unavailable_program_metadata("No program is assigned."))


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


def _reliability_program(settings: AppSettings, slot: int) -> str:
    mapping = next((item for item in pallet_motion_programs(settings) if item["slot"] == slot), None)
    program = mapping.get("reliability_program") if mapping else None
    if not isinstance(program, str) or not program.strip():
        raise problem(409, f"Pool {slot:02d} has no generated reliability program. Rebuild generated scripts first.")
    return program


def pallet_motion_generation(settings: AppSettings) -> dict:
    defaults = {
        "approach_y_clearance_mm": 100.0,
        "mill_approach_x_clearance_mm": 100.0,
        "lift_z_clearance_mm": 100.0,
        "mill_lift_z_clearance_mm": 100.0,
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
        "mill_pre_entry_waypoint": None,
        "safe_pre_waypoint": None,
        "safe_post_waypoint": None,
        "intermediate_safe_poses": [],
    }
    try:
        stored = json.loads(settings.pallet_motion_generation or "{}")
    except json.JSONDecodeError:
        stored = {}
    if not isinstance(stored, dict):
        return defaults
    stored.pop("travel_waypoints", None)
    return {**defaults, **stored}


def _motion_script_signature(settings: AppSettings) -> str:
    """Fingerprint every saved value that changes generated files or their destination."""
    deployment_host = settings.robot_file_host.strip() or settings.robot_host.strip()
    inputs = {
        "generator_revision": PALLET_MOTION_SCRIPT_REVISION,
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
        "supervisor": {
            "hostname": settings.robot_supervisor_hostname,
            "port": settings.robot_supervisor_port,
            "heartbeat_seconds": settings.robot_supervisor_heartbeat_seconds,
            "telemetry_hz": settings.robot_supervisor_telemetry_hz,
            "reconnect_limit_seconds": settings.robot_supervisor_reconnect_limit_seconds,
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
    if path.suffix.lower() == ".script" and motion_scripts_need_rebuild(settings):
        raise problem(409, "Generated pallet scripts do not match the saved safety and transition settings. Rebuild them before commanding a move.")
    return filename


def _assert_pool_motion_position_configured(settings: AppSettings, slot: int) -> None:
    position = next(
        (item for item in pallet_location_positions(settings)["pool_locations"] if item["slot"] == slot),
        None,
    )
    if position is None:
        raise problem(422, f"No configured position exists for Pool {slot:02d}.")
    coordinates = [float(position[axis]) for axis in ("x_mm", "y_mm", "z_mm")]
    if not all(math.isfinite(value) for value in coordinates) or all(abs(value) < 0.001 for value in coordinates):
        raise problem(422, f"Teach a valid robot position for Pool {slot:02d} before commanding movement.")


def _mill_motion_program(settings: AppSettings, operation: str) -> str:
    filename = f"{operation}_mill.script"
    root = PurePosixPath(settings.robot_file_directory or "/programs")
    return str(root / GENERATED_REMOTE_DIRECTORY / filename)


MILL_LOAD_POSITION_PROGRAM_NAME = "mongo_mill_load_position.nc"
MILL_PROGRAM_DIRECTORY = PurePosixPath("/home/operator/gcode/Gcode")


def _mill_program_root(settings: AppSettings) -> PurePosixPath:
    """Use one validated PathPilot root for browsing, validation, and launches."""
    root = PurePosixPath(settings.mill_file_directory.strip() or str(MILL_PROGRAM_DIRECTORY))
    if not root.is_absolute() or ".." in root.parts:
        raise problem(422, "The PathPilot program directory must be an absolute path without '..'.")
    return root


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
( Hold long enough for PathPilot remote status to observe this generated cycle. )
G4 P1.0
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
            directory=settings.mill_file_directory, destination=str(_mill_program_root(settings)), filename=MILL_LOAD_POSITION_PROGRAM_NAME,
            content=BytesIO(content.encode("ascii")), timeout_seconds=settings.cnc_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, f"Could not upload the mill loading program: {exc}") from exc
    return {"filename": MILL_LOAD_POSITION_PROGRAM_NAME, "local_path": str(local_path), "remote_path": remote_path}


def _pathpilot_controller_data_root(settings: AppSettings) -> PurePosixPath:
    program_root = PurePosixPath(settings.mill_file_directory)
    if not program_root.is_absolute() or ".." in program_root.parts:
        raise problem(422, "The PathPilot program directory must be an absolute path without '..'.")
    return program_root.parent if program_root.name.casefold() == "gcode" else program_root


def _mill_status_file_path(settings: AppSettings) -> str:
    controller_root = _pathpilot_controller_data_root(settings)
    path = PurePosixPath(settings.mill_status_file_path)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parts[:len(controller_root.parts)] != controller_root.parts
        or path == controller_root
    ):
        raise problem(422, "The mill completion status file must remain inside the PathPilot controller data directory.")
    return str(path)


def _mill_file_connection(settings: AppSettings) -> dict[str, object]:
    return {
        "host": settings.cnc_host.strip(),
        "port": settings.cnc_ssh_port,
        "username": settings.cnc_ssh_username,
        "password": settings.cnc_ssh_password,
        "directory": settings.mill_file_directory,
        "timeout_seconds": settings.cnc_timeout_seconds,
    }


def _mill_status_file_connection(settings: AppSettings) -> dict[str, object]:
    connection = _mill_file_connection(settings)
    connection["directory"] = str(_pathpilot_controller_data_root(settings))
    return connection


def _legacy_mill_status_file_path(settings: AppSettings, status_path: str | None = None) -> str:
    """Return the accidental doubled path emitted by the first status-file post."""
    path = PurePosixPath(status_path or _mill_status_file_path(settings))
    return str(PurePosixPath(_pathpilot_controller_data_root(settings)) / path.relative_to("/"))


def _mill_status_record(
    settings: AppSettings,
    expected_program: str,
    previous_signature: dict[str, int | str] | None,
    previous_legacy_signature: dict[str, int | str] | None = None,
) -> tuple[bool, str]:
    """Return whether the post has freshly acknowledged normal completion."""
    path = _mill_status_file_path(settings)
    connection = _mill_status_file_connection(settings)
    signature = remote_file_signature(path=path, **connection)
    selected_path = path
    selected_signature = signature
    selected_previous_signature = previous_signature
    legacy_path = _legacy_mill_status_file_path(settings, path)
    if signature is None or signature == previous_signature:
        legacy_signature = remote_file_signature(path=legacy_path, **connection)
        if (
            previous_legacy_signature is not None
            and legacy_signature is not None
            and legacy_signature != previous_legacy_signature
        ):
            selected_path = legacy_path
            selected_signature = legacy_signature
            selected_previous_signature = previous_legacy_signature
        elif signature is None:
            return False, "The mill completion status file has not been created. Repost this program with the updated Fusion post."
        else:
            return False, "The mill completion status file did not change for this program."
    if selected_signature == selected_previous_signature:
        return False, "The mill completion status file did not change for this program."
    try:
        text = str(read_robot_file(path=selected_path, **connection).get("text") or "")
    except RobotFileAccessError as exc:
        return False, f"The mill completion status file could not be read: {exc}"
    fields: dict[str, str] = {}
    states: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip().upper()
        value = value.strip()
        if key == "STATE":
            states.append(value.upper())
        else:
            fields[key] = value
    expected_name = PurePosixPath(expected_program).name
    if fields.get("VERSION") != "MPS-MILL-STATUS-V1":
        return False, "The mill completion status file has an unsupported format. Repost the program with the updated Fusion post."
    if fields.get("PROGRAM") != expected_name:
        return False, f"The mill completion status file identifies {fields.get('PROGRAM') or 'no program'}, not {expected_name}."
    if not states or states[0] != "STARTED" or states[-1] != "COMPLETED":
        return False, "The mill program did not report a normal completed status."
    if selected_path == legacy_path:
        return True, "Completion was read from the legacy doubled status-file path. Repost this program with the corrected Fusion post."
    return True, ""


def test_mill_status_file_access(settings: AppSettings) -> dict[str, str]:
    """Verify the dedicated application folder without touching production status."""
    path = PurePosixPath(_mill_status_file_path(settings))
    connection = _mill_status_file_connection(settings)
    test_path = str(path.parent / ".mps-status-connection-test.txt")
    token = str(uuid4())
    try:
        write_robot_text_file(path=test_path, text=token, **connection)
        observed = str(read_robot_file(path=test_path, **connection).get("text") or "")
        if observed != token:
            raise RobotFileAccessError("The controller returned different content than was written.")
    finally:
        try:
            delete_robot_path(path=test_path, **connection)
        except RobotFileAccessError:
            pass
    return {"path": str(path), "message": "Mill status-file folder is writable and readable."}


_MILL_SUPERVISOR_REMOTE_DIRECTORY = "/home/operator/gcode/MongoProduction"
_MILL_SUPERVISOR_AGENT_PATH = _MILL_SUPERVISOR_REMOTE_DIRECTORY + "/mps_mill_supervisor.py"
_MILL_SUPERVISOR_CONFIG_PATH = _MILL_SUPERVISOR_REMOTE_DIRECTORY + "/mps_mill_supervisor.json"
_MILL_SUPERVISOR_STATE_PATH = _MILL_SUPERVISOR_REMOTE_DIRECTORY + "/mps_mill_supervisor_state.json"
_MILL_SUPERVISOR_PID_PATH = "/tmp/mps-mill-supervisor.pid"


def mill_supervisor_status(session: Session) -> dict[str, object]:
    settings = get_settings(session)
    status = mill_supervisor().status()
    last_result = status.get("last_result") or {}
    result_sequence = int(last_result.get("sequence", 0) or 0)
    result_event = str(last_result.get("event", ""))
    if result_sequence > 0 and result_event in {"completed", "faulted", "latched"}:
        persisted = session.scalar(
            select(MillSupervisorCommand).where(MillSupervisorCommand.sequence == result_sequence)
        )
        if persisted and persisted.status in {
            "created", "dispatching", "sent", "accepted", "running", "monitoring_released", "uncertain"
        }:
            persisted.status = result_event
            persisted.completed_at = persisted.completed_at or datetime.now(timezone.utc).isoformat()
            persisted.result_json = json.dumps(last_result.get("result") or {}, sort_keys=True, separators=(",", ":"))
            persisted.fault_detail = str(last_result.get("detail") or "") or None
            session.commit()
    commands = session.scalars(
        select(MillSupervisorCommand).order_by(MillSupervisorCommand.sequence.desc()).limit(25)
    ).all()
    controller_sequence = status.get("mill_last_sequence")
    latest = commands[0] if commands else None
    sequence_mismatch = bool(
        status.get("connected")
        and controller_sequence is not None
        and int(controller_sequence) != settings.mill_supervisor_last_sequence
    )
    terminal_uncertain = bool(
        latest and latest.status in {"uncertain", "faulted", "latched"}
    )
    reconciliation = None
    if terminal_uncertain and latest:
        reconciliation = {
            "sequence": latest.sequence,
            "operation": latest.operation,
            "command_status": latest.status,
            "detail": latest.fault_detail,
        }
    status.update({
        "enabled": settings.mill_supervisor_enabled,
        "activation_verified": settings.mill_supervisor_activation_verified,
        "expected_sequence": settings.mill_supervisor_last_sequence,
        "staged": not settings.mill_supervisor_enabled,
        "configured": bool(settings.cnc_host.strip() and settings.cnc_ssh_username and settings.cnc_ssh_password),
        "sequence_mismatch": sequence_mismatch,
        "reconciliation_required": sequence_mismatch or terminal_uncertain,
        "reconciliation": reconciliation,
        "recent_commands": [
            {
                "sequence": item.sequence,
                "operation": item.operation,
                "status": item.status,
                "created_at": item.created_at,
                "completed_at": item.completed_at,
                "fault_detail": item.fault_detail,
            }
            for item in commands
        ],
    })
    return status


def topbar_connection_status(session: Session) -> dict[str, dict[str, str]]:
    """Cheap local supervisor-status summary for the persistent page header."""
    settings = get_settings(session)
    robot_configured = settings.robot_connection_mode == "physical" and bool(settings.robot_host.strip())
    mill_configured = bool(settings.cnc_telemetry_enabled and settings.cnc_host.strip())
    robot = robot_supervisor().status()
    mill = mill_supervisor().status()
    robot_age = robot.get("telemetry_age_seconds")
    mill_age = mill.get("telemetry_age_seconds")
    robot_live = bool(robot.get("connected") and robot_age is not None and float(robot_age) <= settings.robot_supervisor_heartbeat_seconds * 4)
    mill_live = bool(mill.get("connected") and mill_age is not None and float(mill_age) <= settings.mill_supervisor_heartbeat_seconds * 3)

    def status(configured: bool, connected: bool, live: bool, name: str, simulated: bool = False) -> dict[str, str]:
        if simulated:
            return {"state": "neutral", "label": f"{name}: Simulated"}
        if not configured:
            return {"state": "neutral", "label": f"{name}: Not configured"}
        if live:
            return {"state": "online", "label": f"{name}: Live"}
        if connected:
            return {"state": "warning", "label": f"{name}: Telemetry stale"}
        return {"state": "offline", "label": f"{name}: Disconnected"}

    return {
        "backend": {"state": "online", "label": "Backend: Online"},
        "robot": status(robot_configured, bool(robot.get("connected")), robot_live, "Robot", settings.robot_connection_mode == "simulated"),
        "mill": status(mill_configured, bool(mill.get("connected")), mill_live, "Mill"),
    }


def stop_mill_supervisor_listener() -> None:
    """Release a manually bootstrapped local listener during backend shutdown."""
    mill_supervisor().stop()


def start_mill_supervisor_listener(session: Session) -> None:
    """Restore the listener automatically only after explicit activation."""
    settings = get_settings(session)
    if settings.mill_supervisor_enabled and settings.mill_supervisor_activation_verified:
        mill_supervisor().start(settings.mill_supervisor_listen_host, settings.mill_supervisor_port)


def start_mill_supervisor_recovery_watchdog(session_factory) -> None:
    """Recover a crashed helper at low frequency without touching LinuxCNC."""
    global _MILL_SUPERVISOR_RECOVERY_THREAD
    if _MILL_SUPERVISOR_RECOVERY_THREAD and _MILL_SUPERVISOR_RECOVERY_THREAD.is_alive():
        return
    _MILL_SUPERVISOR_RECOVERY_STOP.clear()

    def watch() -> None:
        disconnected_since: float | None = None
        last_attempt = 0.0
        while not _MILL_SUPERVISOR_RECOVERY_STOP.wait(5.0):
            try:
                with session_factory() as session:
                    settings = get_settings(session)
                    if not (settings.mill_supervisor_enabled and settings.mill_supervisor_activation_verified):
                        disconnected_since = None
                        continue
                    status = mill_supervisor().status()
                    if status.get("connected"):
                        disconnected_since = None
                        continue
                    now = time.monotonic()
                    disconnected_since = disconnected_since or now
                    if (
                        now - disconnected_since < _SUPERVISOR_RECOVERY_GRACE_SECONDS
                        or now - last_attempt < _SUPERVISOR_RECOVERY_COOLDOWN_SECONDS
                    ):
                        continue
                    last_attempt = now
                    start_mill_supervisor_agent(
                        settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                        settings.cnc_ssh_password, settings.cnc_timeout_seconds,
                        _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
                    )
                    diagnostics().record(
                        "mill_supervisor",
                        "agent_recovery_check",
                        "Checked the disconnected PathPilot helper and started it only if its saved PID was not running.",
                        severity="warning",
                    )
            except Exception as exc:
                diagnostics().record(
                    "mill_supervisor",
                    "agent_recovery_error",
                    "The bounded mill helper recovery check failed.",
                    severity="warning",
                    details={"error": str(exc)},
                )

    _MILL_SUPERVISOR_RECOVERY_THREAD = Thread(target=watch, daemon=True, name="mill-supervisor-recovery")
    _MILL_SUPERVISOR_RECOVERY_THREAD.start()


def stop_mill_supervisor_recovery_watchdog() -> None:
    _MILL_SUPERVISOR_RECOVERY_STOP.set()


def _new_mill_supervisor_command(
    session: Session,
    operation: str,
    arguments: dict[str, object],
) -> MillSupervisorCommand:
    for attempt in range(5):
        settings = get_settings(session)
        if settings.mill_supervisor_last_sequence >= 2_000_000_000:
            raise problem(409, "Mill supervisor sequence space is exhausted. Reconcile and start a new mill agent session.")
        settings.mill_supervisor_last_sequence += 1
        status = mill_supervisor().status()
        command = MillSupervisorCommand(
            id=str(uuid4()),
            sequence=settings.mill_supervisor_last_sequence,
            mill_session=status.get("mill_session"),
            app_session=status.get("app_session"),
            operation=operation,
            arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
            status="created",
            attempted=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(command)
        bump(settings)
        try:
            commit_or_conflict(session)
            return command
        except HTTPException as exc:
            if exc.status_code != 409 or attempt == 4:
                raise
            session.expire_all()
            diagnostics().record(
                "mill_supervisor",
                "command_ledger_commit_retry",
                "Retrying an unsent mill command reservation after a concurrent board update.",
                severity="warning",
                details={"operation": operation, "attempt": attempt + 1},
            )
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("Mill command reservation retry loop exited unexpectedly.")


def _dispatch_mill_supervisor_command(session: Session, command: MillSupervisorCommand) -> tuple[bool, str]:
    """Durably mark the first command byte before the future activation path uses it."""
    command.attempted = True
    command.status = "dispatching"
    command.sent_at = datetime.now(timezone.utc).isoformat()
    session.commit()
    sent, detail = mill_supervisor().dispatch(
        command.sequence,
        command.operation,
        json.loads(command.arguments_json),
    )
    if not sent:
        command.status = "uncertain"
        command.fault_detail = detail
        session.commit()
        return False, detail
    command.status = "sent"
    session.commit()
    return True, ""


def bootstrap_mill_supervisor(session: Session) -> dict[str, object]:
    """Explicitly deploy and start the telemetry-only mill agent.

    This is intentionally not called by Settings saves, startup, or Run Mode.
    It is retained behind the staged rollout API until an operator performs the
    firewall and no-motion activation procedure.
    """
    settings = get_settings(session)
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before bootstrapping the mill supervisor.")
    if not settings.cnc_host.strip() or not settings.cnc_ssh_username or not settings.cnc_ssh_password:
        raise problem(409, "Configure PathPilot SSH before bootstrapping the mill supervisor.")
    try:
        test_mill_supervisor_runtime(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds,
        )
    except CncTelemetryError as exc:
        raise problem(409, f"The staged mill agent cannot use this PathPilot runtime: {exc}") from exc
    mill_supervisor().start(settings.mill_supervisor_listen_host, settings.mill_supervisor_port)
    asset = Path(__file__).with_name("mill_supervisor_agent.py").read_text(encoding="utf-8")
    connection = _mill_status_file_connection(settings)
    config = {
        "host": settings.mill_supervisor_hostname,
        "port": settings.mill_supervisor_port,
        "state_path": _MILL_SUPERVISOR_STATE_PATH,
        "telemetry_seconds": round(1.0 / settings.mill_supervisor_telemetry_hz, 3),
        "program_root": settings.mill_file_directory,
        "status_path": _mill_status_file_path(settings),
        # This staged deployment may observe only. A future activation change is
        # required before the agent can accept any controller command.
        "execution_enabled": False,
    }
    try:
        write_robot_text_file(path=_MILL_SUPERVISOR_AGENT_PATH, text=asset, **connection)
        write_robot_text_file(path=_MILL_SUPERVISOR_CONFIG_PATH, text=json.dumps(config, sort_keys=True), **connection)
        # The helper reads its configuration only at process startup. An old
        # but live PID can otherwise keep retrying a stale backend address
        # while the launcher incorrectly reports that it is already running.
        stop_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds, _MILL_SUPERVISOR_PID_PATH,
        )
        start_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds,
            _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
        )
    except (RobotFileAccessError, CncTelemetryError) as exc:
        raise problem(409, f"Could not bootstrap the staged mill supervisor: {exc}") from exc
    if not mill_supervisor().wait_for_event(0, {"connected"}, 0):
        # wait_for_event is event-sequence based; use a small explicit connection wait.
        deadline = time.monotonic() + max(10.0, settings.cnc_timeout_seconds * 3)
        while time.monotonic() < deadline:
            if mill_supervisor().status().get("connected"):
                settings.mill_supervisor_activation_verified = True
                bump(settings)
                commit_or_conflict(session)
                return mill_supervisor_status(session)
            time.sleep(0.2)
    raise problem(409, "The mill agent was started, but did not complete an outbound telemetry-only handshake. Verify the firewall before activation.")


def set_mill_supervisor_activation(session: Session, payload) -> dict[str, object]:
    """Switch routing only after an idle, sequence-aligned supervisor handshake."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not payload.confirmed:
        raise problem(422, "Confirm that PathPilot is Idle before changing mill supervisor activation.")
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if settings.run_mode_enabled or active_motion:
        raise problem(409, "Stop Run Mode and wait for robot pallet motion before changing mill supervisor activation.")
    status = mill_supervisor().status()
    telemetry = status.get("telemetry") or {}
    if not (
        status.get("connected")
        and status.get("mill_last_sequence") == settings.mill_supervisor_last_sequence
        and telemetry.get("interp_state") == 1
        and telemetry.get("estop") is False
        and telemetry.get("enabled") is True
    ):
        raise problem(409, "The telemetry-only mill supervisor must be connected, sequence-aligned, enabled, and Idle before activation changes.")
    if payload.enabled and not settings.mill_supervisor_activation_verified:
        raise problem(409, "Complete the telemetry-only mill supervisor handshake before enabling command routing.")

    connection = _mill_status_file_connection(settings)
    config = {
        "host": settings.mill_supervisor_hostname,
        "port": settings.mill_supervisor_port,
        "state_path": _MILL_SUPERVISOR_STATE_PATH,
        "telemetry_seconds": round(1.0 / settings.mill_supervisor_telemetry_hz, 3),
        "program_root": settings.mill_file_directory,
        "status_path": _mill_status_file_path(settings),
        "execution_enabled": bool(payload.enabled),
    }
    previous_generation = int(status.get("connection_generation") or 0)
    try:
        asset = Path(__file__).with_name("mill_supervisor_agent.py").read_text(encoding="utf-8")
        write_robot_text_file(path=_MILL_SUPERVISOR_AGENT_PATH, text=asset, **connection)
        write_robot_text_file(
            path=_MILL_SUPERVISOR_CONFIG_PATH,
            text=json.dumps(config, sort_keys=True),
            **connection,
        )
        stop_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds, _MILL_SUPERVISOR_PID_PATH,
        )
        start_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds,
            _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
        )
        deadline = time.monotonic() + max(10.0, settings.cnc_timeout_seconds * 4)
        while time.monotonic() < deadline:
            current = mill_supervisor().status()
            if (
                current.get("connected")
                and int(current.get("connection_generation") or 0) > previous_generation
                and current.get("mill_last_sequence") == settings.mill_supervisor_last_sequence
            ):
                settings.mill_supervisor_enabled = bool(payload.enabled)
                settings.mill_supervisor_activation_verified = True
                bump(settings)
                commit_or_conflict(session)
                diagnostics().record(
                    "mill_supervisor",
                    "activation_changed",
                    "Mill supervisor command routing was enabled." if payload.enabled else "Mill supervisor returned to telemetry-only mode.",
                    details={"enabled": bool(payload.enabled)},
                )
                return mill_supervisor_status(session)
            time.sleep(0.2)
        raise CncTelemetryError("The mill agent did not reconnect with the expected durable sequence.")
    except (RobotFileAccessError, CncTelemetryError) as exc:
        fallback_config = dict(config)
        fallback_config["execution_enabled"] = False
        try:
            write_robot_text_file(
                path=_MILL_SUPERVISOR_CONFIG_PATH,
                text=json.dumps(fallback_config, sort_keys=True),
                **connection,
            )
            stop_mill_supervisor_agent(
                settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                settings.cnc_ssh_password, settings.cnc_timeout_seconds, _MILL_SUPERVISOR_PID_PATH,
            )
            start_mill_supervisor_agent(
                settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                settings.cnc_ssh_password, settings.cnc_timeout_seconds,
                _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
            )
        except (RobotFileAccessError, CncTelemetryError) as restore_exc:
            diagnostics().record(
                "mill_supervisor",
                "telemetry_only_restore_failed",
                "Activation failed and the telemetry-only agent could not be restored automatically.",
                severity="error",
                details={"error": str(restore_exc)},
            )
        settings.mill_supervisor_enabled = False
        settings.mill_supervisor_activation_verified = False
        bump(settings)
        commit_or_conflict(session)
        raise problem(409, f"Mill supervisor activation was not verified; command routing remains disabled: {exc}") from exc


def update_mill_supervisor_agent(session: Session, payload) -> dict[str, object]:
    """Deploy the current helper and restart it without changing routing mode."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not payload.confirmed:
        raise problem(422, "Confirm that PathPilot is Idle before updating the mill supervisor agent.")
    payload.enabled = bool(settings.mill_supervisor_enabled)
    return set_mill_supervisor_activation(session, payload)


def test_mill_supervisor_command_path(session: Session) -> dict[str, object]:
    """Exercise sequencing and acknowledgments without touching LinuxCNC command()."""
    settings = get_settings(session)
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if settings.run_mode_enabled or active_motion:
        raise problem(409, "The no-motion mill supervisor probe requires idle automation.")
    status = mill_supervisor().status()
    telemetry = status.get("telemetry") or {}
    if not (
        settings.mill_supervisor_enabled
        and settings.mill_supervisor_activation_verified
        and status.get("connected")
        and status.get("mill_last_sequence") == settings.mill_supervisor_last_sequence
        and telemetry.get("interp_state") == 1
    ):
        raise problem(409, "The active mill supervisor must be connected, aligned, and Idle before the no-motion probe.")
    command = _new_mill_supervisor_command(session, "probe", {})
    sent, detail = _dispatch_mill_supervisor_command(session, command)
    if not sent:
        raise problem(409, f"The no-motion mill supervisor probe became uncertain: {detail}")
    event = mill_supervisor().wait_for_event(command.sequence, {"completed", "faulted", "latched"}, 10.0)
    if event is None:
        command.status = "uncertain"
        command.fault_detail = "No terminal result arrived for the no-motion probe."
        session.commit()
        raise problem(504, command.fault_detail)
    command.completed_at = event.received_at
    command.result_json = json.dumps(event.result or {}, sort_keys=True, separators=(",", ":"))
    command.status = event.name
    command.fault_detail = event.detail or None
    session.commit()
    if event.name != "completed":
        raise problem(409, f"The no-motion mill supervisor probe reported {event.name}: {event.detail}")
    return {
        "message": "Persistent mill command sequencing and acknowledgments are healthy; no LinuxCNC command was issued.",
        "sequence": command.sequence,
    }


def reconcile_mill_supervisor_command(session: Session, payload) -> dict[str, object]:
    """Reconcile only the ledger; never issue or retry a PathPilot command."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled or _locked_motion(session):
        raise problem(409, "Stop Run Mode and wait for pallet motion before reconciling a mill command.")
    command = session.scalar(
        select(MillSupervisorCommand).where(MillSupervisorCommand.sequence == payload.sequence)
    )
    if not command:
        raise problem(404, "Mill supervisor command sequence was not found.")
    if command.status not in {"latched", "faulted", "uncertain", "monitoring_released"}:
        raise problem(409, f"Mill supervisor sequence {command.sequence} is not awaiting reconciliation.")
    command.status = "operator_completed" if payload.resolution == "accept_completed" else "operator_faulted"
    command.completed_at = command.completed_at or datetime.now(timezone.utc).isoformat()
    command.fault_detail = (
        "Operator confirmed the program completed; no command was resent."
        if payload.resolution == "accept_completed"
        else "Operator confirmed the program did not complete; no command was resent."
    )
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record(
        "mill_supervisor",
        "command_reconciled",
        f"Mill supervisor sequence {command.sequence} was reconciled as {command.status}.",
        severity="warning",
        details={"sequence": command.sequence, "resolution": payload.resolution},
    )
    return mill_supervisor_status(session)


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
    try:
        tools = json.loads(pallet.program_tools_json or "[]")
    except (TypeError, json.JSONDecodeError):
        tools = []
    try:
        tool_counts = json.loads(pallet.program_tool_counts_json or "{}")
    except (TypeError, json.JSONDecodeError):
        tool_counts = {}
    try:
        wcs = json.loads(pallet.program_wcs_json or "[]")
    except (TypeError, json.JSONDecodeError):
        wcs = []
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
        "return_pool_slot_number": pallet.return_pool_slot_number,
        **program_metadata(
            pallet.program_path,
            pallet.content_status,
            tools,
            pallet.expected_cycle_seconds,
            pallet.program_metadata_state,
            pallet.program_metadata_detail,
            pallet.program_cycle_basis,
            wcs,
            tool_counts,
        ),
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


def _joint_waypoint(value: object, name: str) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("joints_rad"), list):
        return None
    joints = value["joints_rad"]
    if len(joints) != 6:
        return None
    try:
        parsed = [float(joint) for joint in joints]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(joint) for joint in parsed):
        return None
    return {"name": str(value.get("name") or name), "joints_rad": parsed}


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


_STACK_LIGHT_CHANNELS = ("red", "amber", "green", "blue", "white", "alarm")
_STACK_LIGHT_COLORS = ("red", "amber", "green", "blue", "white")
_STACK_LIGHT_SYSTEM_STATES = (
    "alarm", "idle", "running", "loading", "machining", "unloading",
    "waiting", "automatic_recovery", "guided_recovery", "manual_control",
)
_DEFAULT_STACK_LIGHT_STATE_COLORS = {
    "alarm": "red",
    "idle": "green",
    "running": "blue",
    "loading": "blue",
    "machining": "blue",
    "unloading": "blue",
    "waiting": "amber",
    "automatic_recovery": "amber",
    "guided_recovery": "white",
    "manual_control": "amber",
}
_PRE_MOTION_ALARM_IDLE_SECONDS = 120
_PRE_MOTION_ALARM_BURSTS = 3
_PRE_MOTION_ALARM_BURST_SECONDS = 0.25


def _stack_light_outputs(settings: AppSettings) -> dict[str, dict[str, object]]:
    try:
        raw = json.loads(settings.stack_light_outputs or "{}")
    except json.JSONDecodeError:
        raw = {}
    return {
        name: value
        for name, value in raw.items()
        if name in _STACK_LIGHT_CHANNELS and isinstance(value, dict)
        and value.get("bank") in {"standard", "configurable", "tool"}
        and isinstance(value.get("index"), int)
    }


def _stack_light_state_colors(settings: AppSettings) -> dict[str, str]:
    try:
        raw = json.loads(settings.stack_light_state_colors or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    return {
        name: str(raw.get(name, default))
        if raw.get(name, default) in {*_STACK_LIGHT_COLORS, "off"}
        else default
        for name, default in _DEFAULT_STACK_LIGHT_STATE_COLORS.items()
    }


def _stack_light_system_state(settings: AppSettings) -> str:
    state = settings.run_mode_state
    if state in {"faulted", "interrupted"}:
        return "alarm"
    if state == "manual_robot_pause":
        return "manual_control"
    if state == "loading":
        return "loading"
    if state in {"machining", "telemetry_unavailable", "telemetry_restored"}:
        return "machining"
    if state == "unloading":
        return "unloading"
    if state in {"recovering_startup_telemetry", "recovering_cnc_telemetry", "recovering_robot_telemetry", "recovery_requested"}:
        return "automatic_recovery"
    if settings.run_mode_enabled and state in {"waiting_confirmation", "idle_waiting_queue", "start_requested", "resuming"}:
        return "waiting"
    if settings.run_mode_enabled:
        return "running"
    return "idle"


def _stack_light_state(settings: AppSettings, system_state: str | None = None) -> dict[str, bool]:
    active = {name: False for name in _STACK_LIGHT_CHANNELS}
    selected_state = system_state or _stack_light_system_state(settings)
    color = _stack_light_state_colors(settings).get(selected_state, "off")
    if color in _STACK_LIGHT_COLORS:
        active[color] = True
    return active


def _effective_stack_light_state(session: Session, settings: AppSettings) -> dict[str, bool]:
    system_state = _stack_light_system_state(settings)
    recovery_active = session.scalar(
        select(RecoverySession.id).where(RecoverySession.status.in_(_RECOVERY_ACTIVE_STATUSES)).limit(1)
    )
    if system_state != "alarm" and recovery_active:
        system_state = "guided_recovery"
    elif system_state != "alarm" and _SUPERVISOR_BACKGROUND_RECOVERY.is_set():
        system_state = "automatic_recovery"
    return _stack_light_state(settings, system_state)


def apply_stack_light(session: Session) -> None:
    """Drive only explicitly configured non-motion outputs; disabled by default."""
    settings = get_settings(session)
    if not (settings.stack_light_enabled and settings.robot_connection_mode == "physical" and settings.robot_host.strip()):
        return
    state = _effective_stack_light_state(session, settings)
    outputs = _stack_light_outputs(settings)
    set_robot_digital_outputs(
        settings.robot_host.strip(), settings.robot_port, settings.robot_timeout_seconds,
        [(str(output["bank"]), int(output["index"]), state[name]) for name, output in outputs.items()],
    )


def refresh_stack_light(session: Session) -> None:
    try:
        apply_stack_light(session)
    except RobotTelemetryError as exc:
        diagnostics().record("stack_light", "output_unavailable", str(exc), severity="warning")


def _pre_motion_alarm_required(session: Session) -> bool:
    """Warn before either controller moves after known system-wide inactivity."""
    completed_values = [
        session.scalar(
            select(RobotMotion.completed_at)
            .where(RobotMotion.completed_at.is_not(None))
            .order_by(RobotMotion.completed_at.desc())
            .limit(1)
        ),
        session.scalar(
            select(MillSupervisorCommand.completed_at)
            .where(
                MillSupervisorCommand.operation == "run_program",
                MillSupervisorCommand.completed_at.is_not(None),
            )
            .order_by(MillSupervisorCommand.completed_at.desc())
            .limit(1)
        ),
        session.scalar(
            select(ProgramRuntime.completed_at)
            .where(ProgramRuntime.completed_at.is_not(None))
            .order_by(ProgramRuntime.completed_at.desc())
            .limit(1)
        ),
    ]
    completed_at = []
    for value in completed_values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        completed_at.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    if not completed_at:
        return False
    idle_since = max(completed_at)
    return (datetime.now(timezone.utc) - idle_since).total_seconds() >= _PRE_MOTION_ALARM_IDLE_SECONDS


def emit_pre_motion_alarm(session: Session, operation: str) -> bool:
    """Emit three short alarm pulses before a physical robot move resumes after idling."""
    settings = get_settings(session)
    output = _stack_light_outputs(settings).get("alarm")
    if not (
        output
        and settings.stack_light_enabled
        and settings.robot_connection_mode == "physical"
        and settings.robot_host.strip()
        and _pre_motion_alarm_required(session)
    ):
        return False
    diagnostics().record(
        "stack_light", "pre_motion_alarm",
        "Emitting three short audible warnings before machine movement resumes after two idle minutes.",
        details={"operation": operation, "bursts": _PRE_MOTION_ALARM_BURSTS},
    )
    for _ in range(_PRE_MOTION_ALARM_BURSTS):
        set_robot_digital_output(
            settings.robot_host.strip(), settings.robot_port, settings.robot_timeout_seconds,
            str(output["bank"]), int(output["index"]), True,
        )
        time.sleep(_PRE_MOTION_ALARM_BURST_SECONDS)
        set_robot_digital_output(
            settings.robot_host.strip(), settings.robot_port, settings.robot_timeout_seconds,
            str(output["bank"]), int(output["index"]), False,
        )
        time.sleep(_PRE_MOTION_ALARM_BURST_SECONDS)
    return True


def board_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    programs, warning = available_programs(settings)
    current_run_pallet = session.get(Pallet, settings.run_mode_current_pallet_id) if settings.run_mode_current_pallet_id else None
    return {
        "revision": settings.revision,
        "capabilities": {"automatic_put_away": True, "pool_return_ghosts": True},
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
            "run_mode_safety_confirm": settings.run_mode_safety_confirm,
            **pallet_location_positions(settings),
            "debug_menu_enabled": settings.debug_menu_enabled,
            "manual_io_control_enabled": settings.manual_io_control_enabled,
            "stack_light_enabled": settings.stack_light_enabled,
            "stack_light_outputs": _stack_light_outputs(settings),
            "stack_light_state_colors": _stack_light_state_colors(settings),
            "stack_light_system_state": _stack_light_system_state(settings),
            "stack_light_state": _effective_stack_light_state(session, settings),
            "push_notifications_enabled": settings.push_notifications_enabled,
            "push_notification_server": settings.push_notification_server,
            "push_notification_topic": settings.push_notification_topic,
            "push_notification_token_configured": bool(settings.push_notification_token),
            "push_notify_errors": settings.push_notify_errors,
            "push_notify_completed_pallets": settings.push_notify_completed_pallets,
            "push_notify_queue_empty": settings.push_notify_queue_empty,
            "background_stack_light_intensity": settings.background_stack_light_intensity,
            "machine_state": settings.machine_state,
            "robot_connection_mode": settings.robot_connection_mode,
            "robot_host": settings.robot_host,
            "robot_port": settings.robot_port,
            "robot_poll_hz": settings.robot_poll_hz,
            "robot_timeout_seconds": settings.robot_timeout_seconds,
            "robot_supervisor_enabled": settings.robot_supervisor_enabled,
            "robot_supervisor_activation_verified": settings.robot_supervisor_activation_verified,
            "robot_supervisor_hostname": settings.robot_supervisor_hostname,
            "robot_supervisor_listen_host": settings.robot_supervisor_listen_host,
            "robot_supervisor_port": settings.robot_supervisor_port,
            "robot_supervisor_heartbeat_seconds": settings.robot_supervisor_heartbeat_seconds,
            "robot_supervisor_telemetry_hz": settings.robot_supervisor_telemetry_hz,
            "robot_supervisor_reconnect_limit_seconds": settings.robot_supervisor_reconnect_limit_seconds,
            "robot_supervisor_pre_dispatch_fallback": settings.robot_supervisor_pre_dispatch_fallback,
            "robot_supervisor_maintenance_mode": settings.robot_supervisor_maintenance_mode,
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
            "mill_supervisor_enabled": settings.mill_supervisor_enabled,
            "mill_supervisor_activation_verified": settings.mill_supervisor_activation_verified,
            "mill_supervisor_hostname": settings.mill_supervisor_hostname,
            "mill_supervisor_listen_host": settings.mill_supervisor_listen_host,
            "mill_supervisor_port": settings.mill_supervisor_port,
            "mill_supervisor_heartbeat_seconds": settings.mill_supervisor_heartbeat_seconds,
            "mill_supervisor_telemetry_hz": settings.mill_supervisor_telemetry_hz,
            "mill_file_directory": settings.mill_file_directory,
            "mill_program_extensions": json.loads(settings.mill_program_extensions),
            "mill_programs_page_enabled": settings.mill_programs_page_enabled,
            "mill_programs_filter_enabled": settings.mill_programs_filter_enabled,
            "mill_editor_command": settings.mill_editor_command,
            "mill_status_file_path": settings.mill_status_file_path,
            "camera_devices": parse_camera_devices(settings.camera_devices_json),
            "camera_idle_id": settings.camera_idle_id,
            "camera_loading_id": settings.camera_loading_id,
            "camera_machining_id": settings.camera_machining_id,
            "camera_recording_enabled": settings.camera_recording_enabled,
            "camera_recording_path": settings.camera_recording_path,
            "camera_recording_retention_days": settings.camera_recording_retention_days,
            "camera_width": settings.camera_width,
            "camera_height": settings.camera_height,
            "camera_fps": settings.camera_fps,
            "camera_segment_seconds": settings.camera_segment_seconds,
            "fusion_tool_library_path": settings.fusion_tool_library_path,
            "fusion_tool_libraries": [{"path": path, "name": Path(path).name} for path in fusion_tool_library_paths(settings)],
        },
        "run_mode": {
            "enabled": settings.run_mode_enabled,
            "safety_confirm": settings.run_mode_safety_confirm,
            "state": settings.run_mode_state,
            "detail": settings.run_mode_detail,
            "alert": settings.run_mode_alert or None,
            "current_pallet_id": settings.run_mode_current_pallet_id,
            "current_pallet_name": current_run_pallet.name if current_run_pallet else None,
            "return_slot": settings.run_mode_return_slot,
            "pending_action": settings.run_mode_pending_action or None,
            "loaded_machine_action": settings.run_mode_loaded_machine_action or None,
            "confirmation_token": settings.run_mode_confirmation_token or None,
            "manual_robot_pause": settings.run_mode_manual_robot_pause,
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


_CNC_TELEMETRY_CACHE: dict[tuple[object, ...], tuple[float, dict | None, str | None]] = {}
_CNC_TELEMETRY_REFRESHING: set[tuple[object, ...]] = set()
_CNC_TELEMETRY_LOCK = RLock()


def _cnc_telemetry_key(settings: AppSettings) -> tuple[object, ...]:
    return (
        settings.cnc_host.strip(),
        settings.cnc_ssh_port,
        settings.cnc_ssh_username,
        settings.cnc_ssh_password,
        settings.cnc_timeout_seconds,
    )


def _refresh_cnc_telemetry(
    key: tuple[object, ...],
    connection: tuple[str, int, str, str, float],
    completed: Event,
) -> None:
    telemetry: dict | None = None
    error: str | None = None
    try:
        telemetry = read_linuxcnc_snapshot(*connection)
    except CncTelemetryError as exc:
        error = str(exc)
    finally:
        with _CNC_TELEMETRY_LOCK:
            _CNC_TELEMETRY_CACHE[key] = (time.monotonic(), telemetry, error)
            _CNC_TELEMETRY_REFRESHING.discard(key)
        completed.set()


def _clear_telemetry_caches() -> None:
    with _CNC_TELEMETRY_LOCK:
        _CNC_TELEMETRY_CACHE.clear()
    with _ROBOT_TELEMETRY_LOCK:
        _ROBOT_TELEMETRY_CACHE.clear()


def _configured_cnc_telemetry(settings: AppSettings) -> tuple[dict | None, str]:
    if not settings.cnc_telemetry_enabled:
        return None, "Mill telemetry is not connected yet."
    if not settings.cnc_host.strip():
        return None, "CNC telemetry is enabled, but no controller host is configured."
    key = _cnc_telemetry_key(settings)
    now = time.monotonic()
    start_refresh = False
    completed = Event()
    with _CNC_TELEMETRY_LOCK:
        cached = _CNC_TELEMETRY_CACHE.get(key)
        if cached and now - cached[0] < 5.0:
            if cached[1] is not None:
                return deepcopy(cached[1]), "Live PathPilot zbot carousel assignments."
            return None, f"PathPilot telemetry is unavailable: {cached[2] or 'connection failed'}"
        if key not in _CNC_TELEMETRY_REFRESHING:
            _CNC_TELEMETRY_REFRESHING.add(key)
            start_refresh = True

    if start_refresh:
        connection = (
            settings.cnc_host.strip(),
            settings.cnc_ssh_port,
            settings.cnc_ssh_username,
            settings.cnc_ssh_password,
            settings.cnc_timeout_seconds,
        )
        Thread(
            target=_refresh_cnc_telemetry,
            args=(key, connection, completed),
            daemon=True,
            name="cnc-telemetry-refresh",
        ).start()
        # Fast mocked readers complete here in tests. Real network reads never hold
        # a page request for longer than this small first-sample allowance.
        completed.wait(0.1)

    with _CNC_TELEMETRY_LOCK:
        refreshed = _CNC_TELEMETRY_CACHE.get(key)
    if refreshed and refreshed[1] is not None:
        source = "Live PathPilot zbot carousel assignments." if now - refreshed[0] < 5.0 else (
            "Showing the last PathPilot telemetry while a refresh is running."
        )
        return deepcopy(refreshed[1]), source
    if cached and cached[1] is not None:
        return deepcopy(cached[1]), "Showing the last PathPilot telemetry while a refresh is running."
    if refreshed and refreshed[2]:
        return None, f"PathPilot telemetry is unavailable: {refreshed[2]}"
    return None, "PathPilot telemetry refresh is in progress."


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


def _tool_color_states(
    library: list[dict],
    telemetry: dict | None,
    atc_slots: list[dict],
    extra_numbers: set[int] | None = None,
) -> dict[str, dict]:
    if not telemetry:
        return {}
    loaded_numbers = {slot["number"] for slot in atc_slots if slot["number"] is not None}
    lengths = {
        row.get("tool_number"): row.get("length_offset")
        for row in telemetry.get("tool_table", [])
        if row.get("tool_number") is not None
    }
    states = {}
    numbers = {tool["number"] for tool in library}
    numbers.update(extra_numbers or set())
    for number in sorted(numbers):
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
    runtime_rows = session.scalars(select(ProgramRuntime).order_by(ProgramRuntime.completed_at.desc())).all()
    runtime_totals: dict[str, tuple[int, int]] = {}
    for item in runtime_rows:
        total, count = runtime_totals.get(item.program_path, (0, 0))
        runtime_totals[item.program_path] = (total + item.duration_seconds, count + 1)

    def estimated_cycle(item: Pallet) -> tuple[int | None, str, int]:
        program_path = (item.program_path or "").strip()
        total, count = runtime_totals.get(program_path, (0, 0))
        if count:
            return max(1, round(total / count)), "Measured average", count
        return item.expected_cycle_seconds, "Posted estimate", 0

    def dashboard_pallet(item: Pallet) -> dict[str, object]:
        result = serialize_pallet(item)
        estimate, source, samples = estimated_cycle(item)
        result.update({
            "estimated_cycle_seconds": estimate,
            "estimate_source": source,
            "measured_run_count": samples,
        })
        return result

    queue_items = [dashboard_pallet(item) for item in queue]
    machine_item = serialize_pallet(machine) if machine else None
    machine_estimate, _machine_source, _machine_samples = estimated_cycle(machine) if machine else (None, "Posted estimate", 0)
    started_at = settings.run_mode_program_started_at if settings.run_mode_state in {"machining", "telemetry_unavailable", "telemetry_restored"} else None
    elapsed_seconds = 0
    if started_at:
        try:
            elapsed_seconds = max(0, round((datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()))
        except ValueError:
            started_at = None
    current_remaining_seconds = max(0, (machine_estimate or 0) - elapsed_seconds) if machine else None
    queue_cycle_seconds = sum(item["estimated_cycle_seconds"] or 0 for item in queue_items)
    if machine and not any(item.get("id") == machine.id for item in queue_items):
        queue_cycle_seconds += current_remaining_seconds or 0
    elif machine and machine_estimate:
        queue_cycle_seconds = max(0, queue_cycle_seconds - elapsed_seconds)
    queue_tools = sorted(
        {tool for item in queue_items for tool in item["program_tools"]}
        | set(machine_item["program_tools"] if machine_item else []),
        key=lambda tool: int(tool[1:]),
    )
    telemetry, atc_source = _configured_cnc_telemetry(settings)
    atc_slots = _atc_inventory(telemetry)
    queue_tool_numbers = {int(tool[1:]) for tool in queue_tools if tool.startswith("T") and tool[1:].isdigit()}
    queue_tool_states = _tool_color_states([], telemetry, atc_slots, queue_tool_numbers)
    cameras = camera_manager().snapshot(settings)
    # Keep the dashboard focused on programs physically in the cell's pool or
    # mill, plus those actively queued. Historical/staged programs remain
    # stored but do not clutter the live production counter.
    program_paths = {
        item.program_path.strip()
        for item in pallets
        if (
            item.program_path
            and item.program_path.strip()
            and (item.location in {"pool", "machine"} or item.queue_position is not None)
        )
    }
    completion_counts = {
        item.program_path: item.completed_count
        for item in session.scalars(select(ProgramCompletionStat)).all()
    }
    return {
        "revision": settings.revision,
        "queue": queue_items,
        "queue_cycle_seconds": queue_cycle_seconds,
        "queue_tools": queue_tools,
        "queue_tool_states": queue_tool_states,
        "machine_pallet": machine_item,
        "current_cycle_seconds": machine_estimate,
        "current_cycle_started_at": started_at,
        "current_cycle_elapsed_seconds": elapsed_seconds if started_at else None,
        "current_cycle_remaining_seconds": current_remaining_seconds,
        "atc_tools": [slot["tool"] for slot in atc_slots if slot["tool"]],
        "atc_source": atc_source,
        "cameras": cameras,
        "program_completions": [
            {
                "program_path": path,
                "completed_count": completion_counts.get(path, 0),
                "measured_run_count": runtime_totals.get(path, (0, 0))[1],
                "average_run_seconds": (
                    round(runtime_totals[path][0] / runtime_totals[path][1])
                    if runtime_totals.get(path, (0, 0))[1] else None
                ),
            }
            for path in sorted(program_paths, key=str.casefold)
        ],
        "summary": _board_summary(settings, pallets),
    }


def update_program_completion_stat(session: Session, program_path: str, completed_count: int, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    stat = session.get(ProgramCompletionStat, program_path)
    if stat is None:
        stat = ProgramCompletionStat(program_path=program_path, completed_count=completed_count)
        session.add(stat)
    else:
        stat.completed_count = completed_count
    bump(settings)
    commit_or_conflict(session)


def reset_program_completion_stat(session: Session, program_path: str, expected_revision: int) -> None:
    update_program_completion_stat(session, program_path, 0, expected_revision)


def mark_pallet_program_completed(session: Session, pallet: Pallet) -> None:
    """Mark a finished pallet and record exactly one completed program run."""
    if pallet.content_status == "complete_parts":
        return
    pallet.content_status = "complete_parts"
    program_path = (pallet.program_path or "").strip()
    if not program_path:
        return
    stat = session.get(ProgramCompletionStat, program_path)
    if stat is None:
        session.add(ProgramCompletionStat(program_path=program_path, completed_count=1))
    else:
        stat.completed_count += 1
    _send_push_notification(
        get_settings(session),
        "completed_pallet",
        f"MPS: pallet {pallet.name} completed {program_path}.",
    )


def _send_push_notification(settings: AppSettings, event: str, message: str, *, force: bool = False) -> None:
    """Queue an optional ntfy push notification; delivery never stops production."""
    enabled = force or (settings.push_notifications_enabled and (
        (event == "error" and settings.push_notify_errors)
        or (event == "completed_pallet" and settings.push_notify_completed_pallets)
        or (event == "queue_empty" and settings.push_notify_queue_empty)
    ))
    if not enabled or not settings.push_notification_topic.strip():
        return
    server = settings.push_notification_server.strip().rstrip("/") or "https://ntfy.sh"
    topic = settings.push_notification_topic.strip()
    token = settings.push_notification_token.strip()

    def deliver() -> None:
        try:
            title = "MPS test notification" if event == "test" else "MPS error" if event == "error" else "MPS queue complete" if event == "queue_empty" else "MPS pallet complete"
            tags = "test_tube" if event == "test" else "warning" if event == "error" else "white_check_mark"
            headers = {"Title": title, "Tags": tags}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = Request(f"{server}/{quote(topic, safe='')}", data=message[:1500].encode("utf-8"), headers=headers, method="POST")
            with urlopen(request, timeout=10):
                pass
            diagnostics().record("push_notification", "sent", f"Sent {event} push notification.")
        except Exception as exc:  # Notification delivery is non-critical.
            diagnostics().record("push_notification", "failed", "Could not send push notification.", severity="warning", details={"event": event, "error": str(exc)})

    Thread(target=deliver, daemon=True, name="mps-push-notification").start()


def queue_push_notification_test(session: Session) -> dict[str, str]:
    """Queue a sample push using the saved notification destination."""
    settings = get_settings(session)
    if not settings.push_notification_topic.strip():
        raise problem(422, "Save a private ntfy topic before sending a test notification.")
    _send_push_notification(settings, "test", "MPS test notification: push alerts are configured.", force=True)
    return {"message": "Test notification queued. Check the subscribed phone."}


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
    program_usage: dict[str, list[dict[str, object]]] = {}
    counted_programs: set[str] = set()
    for pallet in session.scalars(select(Pallet).where(Pallet.program_path.is_not(None))).all():
        if not pallet.program_path or pallet.program_path in counted_programs:
            continue
        counted_programs.add(pallet.program_path)
        try:
            counts = json.loads(pallet.program_tool_counts_json or "{}")
        except (TypeError, json.JSONDecodeError):
            counts = {}
        if not counts:
            try:
                counts = {tool: 1 for tool in json.loads(pallet.program_tools_json or "[]")}
            except (TypeError, json.JSONDecodeError):
                counts = {}
        if not isinstance(counts, dict):
            continue
        for tool, count in counts.items():
            if isinstance(tool, str) and isinstance(count, int) and count > 0:
                program_usage.setdefault(tool, []).append({"program_path": pallet.program_path, "uses": count})
    return {
        "revision": settings.revision,
        "atc_slots": atc_slots,
        "atc_tools": [slot for slot in atc_slots if slot["tool"]],
        "atc_source": atc_source,
        "tool_states": _tool_color_states(library, telemetry, atc_slots),
        "program_usage": program_usage,
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


def _supervisor_robot_snapshot(settings: AppSettings, summary: dict) -> dict:
    status = robot_supervisor().status()
    telemetry = status.get("telemetry") or {}
    connected = bool(status.get("connected") and telemetry)
    age = status.get("telemetry_age_seconds")
    fresh = connected and age is not None and age <= settings.robot_supervisor_heartbeat_seconds * 4

    def rows(mask_name: str, prefix: str, count: int, direction: str, bank: str) -> list[dict]:
        mask = telemetry.get(mask_name) if fresh else None
        if not isinstance(mask, int):
            return [
                {
                    "channel": f"{prefix}{index}", "index": index, "bit": index,
                    "value": None, "writable": False, "direction": direction, "bank": bank,
                }
                for index in range(count)
            ]
        return _mask_rows(mask, prefix, count, writable=direction == "output", direction=direction, bank=bank)

    tcp = telemetry.get("tcp_pose") if fresh else []
    tcp_speed = telemetry.get("tcp_speed") if fresh else []
    joints = telemetry.get("joint_positions_rad") if fresh else []
    joint_speeds = telemetry.get("joint_velocities_rad_s") if fresh else []
    joint_currents = telemetry.get("joint_currents_a") if fresh else []
    joint_temperatures = telemetry.get("joint_temperatures_c") if fresh else []
    axes = ("X", "Y", "Z", "Rx", "Ry", "Rz")
    joint_names = ("Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3")
    snapshot = {
        "revision": settings.revision,
        "timestamp": telemetry.get("received_at") or datetime.now(timezone.utc).isoformat(),
        "source": "robot-supervisor",
        "connected": bool(fresh),
        "telemetry_connected": bool(fresh),
        "connection_state": "live" if fresh else ("degraded" if status.get("connected") else "unavailable"),
        "connection_label": "Supervisor live" if fresh else "Supervisor connected - awaiting telemetry" if status.get("connected") else "Supervisor disconnected",
        "machine_state": settings.machine_state,
        "summary": summary,
        "robot": {
            "mode": settings.robot_connection_mode,
            "host": settings.robot_host.strip(),
            "port": settings.robot_supervisor_port,
            "controller_version": None,
            "recipe_fields": ["supervisor-v1"],
        },
        "supervisor": status,
        "digital_input_groups": [
            {"title": "Standard inputs", "rows": rows("standard_inputs", "DI", 8, "input", "standard")},
            {"title": "Configurable inputs", "rows": rows("configurable_inputs", "CI", 8, "input", "configurable")},
            {"title": "Tool inputs", "rows": rows("tool_inputs", "TI", 2, "input", "tool")},
        ],
        "digital_output_groups": [
            {"title": "Standard outputs", "rows": rows("standard_outputs", "DO", 8, "output", "standard")},
            {"title": "Configurable outputs", "rows": rows("configurable_outputs", "CO", 8, "output", "configurable")},
            {"title": "Tool outputs", "rows": rows("tool_outputs", "TO", 2, "output", "tool")},
        ],
        "analog_inputs": [],
        "analog_outputs": [],
        "state_rows": [
            {"label": "Robot mode", "value": telemetry.get("robot_mode") if fresh else "Unavailable"},
            {"label": "Safety mode", "value": telemetry.get("safety_mode") if fresh else "Unavailable"},
            {"label": "Runtime state", "value": telemetry.get("runtime_state") if fresh else "Unavailable"},
            {"label": "Supervisor sequence", "value": status.get("robot_last_sequence")},
            {"label": "Supervisor latch", "value": "Latched" if status.get("latched") else "Clear"},
        ],
        "pose_rows": [
            {"channel": axis, "label": f"TCP {axis}", "value": tcp[index]}
            for index, axis in enumerate(axes) if index < len(tcp)
        ],
        "tcp_speed_rows": [
            {"channel": axis, "label": f"TCP speed {axis}", "value": tcp_speed[index]}
            for index, axis in enumerate(axes) if index < len(tcp_speed)
        ],
        "joint_rows": [
            {"channel": f"J{index}", "label": name, "value": joints[index]}
            for index, name in enumerate(joint_names) if index < len(joints)
        ],
        "tcp_detail_rows": [
            {
                "axis": axis,
                "actual_pose": tcp[index] if index < len(tcp) else None,
                "actual_speed": tcp_speed[index] if index < len(tcp_speed) else None,
                "actual_force": None,
                "target_pose": None,
                "target_speed": None,
            }
            for index, axis in enumerate(axes)
        ],
        "joint_detail_rows": [
            {
                "joint": name,
                "actual_position": joints[index] if index < len(joints) else None,
                "actual_velocity": joint_speeds[index] if index < len(joint_speeds) else None,
                "actual_current": joint_currents[index] if index < len(joint_currents) else None,
                "actual_temperature": joint_temperatures[index] if index < len(joint_temperatures) else None,
                "target_position": None,
                "target_velocity": None,
                "target_current": None,
            }
            for index, name in enumerate(joint_names)
        ],
        "extra_actual_rows": [],
        "notes": "Telemetry and commands are using the robot-originated supervisor connection." if fresh else status.get("last_disconnect_detail") or "Waiting for supervisor telemetry.",
    }
    return _apply_debug_labels(_apply_debug_program_controls(snapshot, settings), settings)


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

    telemetry, telemetry_source = _configured_cnc_telemetry(settings)
    if telemetry is None:
        return _apply_debug_mill_program_controls(
            _cnc_unavailable_snapshot("Controller unavailable", telemetry_source), settings,
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
        "source": telemetry_source,
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


_ROBOT_TELEMETRY_CACHE: dict[
    tuple[object, ...], tuple[float, dict | None, str | None, float | None]
] = {}
_ROBOT_TELEMETRY_REFRESHING: set[tuple[object, ...]] = set()
_ROBOT_TELEMETRY_LOCK = RLock()
_ROBOT_TELEMETRY_STALE_GRACE_SECONDS = 60.0
_SUPERVISOR_RECOVERY_STOP = Event()
_SUPERVISOR_RECOVERY_THREAD: Thread | None = None
_SUPERVISOR_BACKGROUND_RECOVERY = Event()
_SUPERVISOR_RECOVERY_COOLDOWN_SECONDS = 15.0
_SUPERVISOR_RECOVERY_GRACE_SECONDS = 10.0
_SUPERVISOR_PROGRAM_RECOVERY_COOLDOWN_SECONDS = 30.0
_MILL_SUPERVISOR_RECOVERY_STOP = Event()
_MILL_SUPERVISOR_RECOVERY_THREAD: Thread | None = None
_MOTION_TELEMETRY_READ_RETRY_SECONDS = 20.0
# A temporary loss of RTDE must not mark a dispatched robot program as failed.
# The reconnect circuit remains bounded, while the motion monitor waits long
# enough for a lossy local network to recover before requiring reconciliation.
_MOTION_TELEMETRY_OUTAGE_GRACE_SECONDS = 45.0


def _refresh_robot_telemetry(
    key: tuple[object, ...],
    connection: tuple[str, int, int, float],
    completed: Event,
) -> None:
    snapshot: dict | None = None
    error: str | None = None
    try:
        snapshot = read_robot_snapshot(*connection)
    except RobotTelemetryError as exc:
        error = str(exc)
    finally:
        now = time.monotonic()
        with _ROBOT_TELEMETRY_LOCK:
            previous = _ROBOT_TELEMETRY_CACHE.get(key)
            if snapshot is not None:
                _ROBOT_TELEMETRY_CACHE[key] = (now, snapshot, None, now)
            else:
                previous_snapshot = previous[1] if previous else None
                previous_success_at = previous[3] if previous else None
                _ROBOT_TELEMETRY_CACHE[key] = (
                    now, previous_snapshot, error, previous_success_at,
                )
            _ROBOT_TELEMETRY_REFRESHING.discard(key)
        completed.set()


def _cached_robot_telemetry(settings: AppSettings) -> tuple[dict | None, str | None]:
    key = (
        settings.robot_host.strip(),
        settings.robot_port,
        settings.robot_poll_hz,
        settings.robot_timeout_seconds,
    )
    now = time.monotonic()
    start_refresh = False
    completed = Event()
    with _ROBOT_TELEMETRY_LOCK:
        cached = _ROBOT_TELEMETRY_CACHE.get(key)
        fresh_for = 1.0 if cached and cached[1] is not None and not cached[2] else 4.0
        if cached and now - cached[0] < fresh_for:
            snapshot = deepcopy(cached[1])
            if snapshot is not None and cached[2] and cached[3] is not None:
                age = now - cached[3]
                if age <= _ROBOT_TELEMETRY_STALE_GRACE_SECONDS:
                    snapshot["telemetry_stale"] = True
                    snapshot["last_live_sample_age_seconds"] = round(age, 1)
                    return snapshot, cached[2]
                return None, cached[2]
            return snapshot, cached[2]
        if key not in _ROBOT_TELEMETRY_REFRESHING:
            _ROBOT_TELEMETRY_REFRESHING.add(key)
            start_refresh = True

    if start_refresh:
        connection = (
            settings.robot_host.strip(),
            settings.robot_port,
            settings.robot_poll_hz,
            settings.robot_timeout_seconds,
        )
        Thread(
            target=_refresh_robot_telemetry,
            args=(key, connection, completed),
            daemon=True,
            name="robot-telemetry-refresh",
        ).start()
        completed.wait(0.1)

    with _ROBOT_TELEMETRY_LOCK:
        refreshed = _ROBOT_TELEMETRY_CACHE.get(key)
    if refreshed and refreshed[1] is not None:
        snapshot = deepcopy(refreshed[1])
        if refreshed[2] and refreshed[3] is not None:
            age = time.monotonic() - refreshed[3]
            if age > _ROBOT_TELEMETRY_STALE_GRACE_SECONDS:
                return None, refreshed[2]
            snapshot["telemetry_stale"] = True
            snapshot["last_live_sample_age_seconds"] = round(age, 1)
        return snapshot, refreshed[2]
    if cached and cached[1] is not None:
        age = now - cached[3] if cached[3] is not None else float("inf")
        if age <= _ROBOT_TELEMETRY_STALE_GRACE_SECONDS:
            snapshot = deepcopy(cached[1])
            snapshot["telemetry_stale"] = True
            snapshot["last_live_sample_age_seconds"] = round(age, 1)
            return snapshot, "Telemetry refresh is in progress; showing the last live sample."
    if refreshed and refreshed[2]:
        return None, refreshed[2]
    return None, "Telemetry refresh is in progress."


def robot_io_snapshot(session: Session) -> dict:
    settings = get_settings(session)
    pallets = session.scalars(select(Pallet)).all()
    summary = _board_summary(settings, pallets)

    if settings.robot_connection_mode == "physical":
        # The CB-series Primary stream can block URControl when a lossy link
        # stops draining high-rate packets. Once the app listener is available,
        # reserve controller telemetry for the compact robot-originated channel
        # instead of repeatedly opening another legacy stream from the UI.
        if settings.robot_supervisor_enabled or robot_supervisor().status().get("listening"):
            return _supervisor_robot_snapshot(settings, summary)
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
        snapshot, telemetry_error = _cached_robot_telemetry(settings)
        if snapshot is not None:
            snapshot["revision"] = settings.revision
            snapshot["summary"] = summary
            snapshot["machine_state"] = settings.machine_state
            snapshot["robot"]["mode"] = settings.robot_connection_mode
            snapshot = _apply_debug_program_controls(snapshot, settings)
            snapshot["program_controls"]["file_list_note"] = (
                "Loaded-program polling is disabled to protect the controller connection. "
                "Program buttons still query Dashboard when an operator runs a program."
            )
            if telemetry_error:
                snapshot["warning"] = telemetry_error
                if snapshot.get("telemetry_stale"):
                    age = snapshot.get("last_live_sample_age_seconds", 0)
                    snapshot["connection_label"] = f"Telemetry degraded - last live sample {age:.1f}s ago"
                    snapshot["connection_state"] = "degraded"
                    snapshot["telemetry_connected"] = False
            else:
                snapshot["connection_state"] = "live"
                snapshot["telemetry_connected"] = True
            return _apply_debug_labels(snapshot, settings)
        controller_health = robot_dashboard_health(
            settings.robot_host.strip(), settings.robot_timeout_seconds,
        )
        controller_reachable = bool(controller_health.get("reachable"))
        if not controller_reachable:
            trigger_network_diagnostic_on_robot_loss()
        unavailable = {
            "revision": settings.revision,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "robot-dashboard" if controller_reachable else "unavailable",
            "connected": controller_reachable,
            "telemetry_connected": False,
            "connection_state": "degraded" if controller_reachable else "unavailable",
            "connection_label": (
                "Controller reachable - live telemetry unavailable"
                if controller_reachable else "Physical robot unavailable"
            ),
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
            "notes": (
                f"The robot Dashboard is reachable, but its live state stream is unavailable: {telemetry_error}"
                if controller_reachable
                else f"Physical robot mode is selected, but live telemetry is unavailable: {telemetry_error}. "
                f"Dashboard probe: {controller_health.get('error') or 'unavailable'}"
            ),
            "warning": telemetry_error,
        }
        unavailable = _apply_debug_program_controls(unavailable, settings)
        unavailable["program_controls"]["file_list_note"] = (
            "Controller commands are reachable, but pallet movement remains blocked until fresh live telemetry returns."
            if controller_reachable
            else "Program controls are unavailable until the robot controller reconnects."
        )
        return _apply_debug_labels(unavailable, settings)

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


def _run_mode_allows_robot_reconnect(settings: AppSettings) -> bool:
    """True only when production is paused on a mill-only retry, not robot work."""
    return settings.run_mode_enabled and settings.run_mode_state in {
        "retry_cnc_program",
        "retry_cnc_preflight",
        "recovering_cnc_telemetry",
    }


def reconnect_robot_after_manual_control(session: Session) -> dict[str, object]:
    """Restore MPS telemetry after manual jogging without advancing production."""
    settings = get_settings(session)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Configure a physical Mongo controller before reconnecting it.")
    if not (settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified):
        raise problem(409, "Enable and verify the Mongo supervisor before reconnecting it.")
    if settings.run_mode_enabled and not _run_mode_allows_robot_reconnect(settings):
        raise problem(409, "Robot reconnect is available only when Run Mode is stopped or paused on a mill-only retry.")
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if motion:
        raise problem(409, "Wait for the active pallet movement before reconnecting Mongo.")
    if robot_supervisor_status(session).get("reconciliation_required"):
        raise problem(409, "Reconcile the uncertain robot command before reconnecting Mongo.")
    result = recover_robot_supervisor_program(
        session,
        trigger="manual-control-reconnect",
        allow_run_mode_paused=True,
    )
    return {
        "status": "reconnected" if result.get("connected") else "waiting",
        "message": "Mongo supervisor restored. Run Mode remains paused and no robot movement was commanded.",
        "supervisor": result,
    }


def retry_robot_telemetry(session: Session) -> dict:
    """Allow an explicit telemetry reset only while no automated operation is active."""
    settings = get_settings(session)
    if settings.run_mode_enabled and not _run_mode_allows_robot_reconnect(settings):
        raise problem(409, "Stop Run Mode before resetting the robot connection.")
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if motion:
        raise problem(409, "Wait for the active robot movement before resetting its connection.")
    reset_robot_connections()
    with _ROBOT_TELEMETRY_LOCK:
        _ROBOT_TELEMETRY_CACHE.clear()
        _ROBOT_TELEMETRY_REFRESHING.clear()
    if settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified:
        recover_robot_supervisor_connection(session, trigger="operator", allow_run_mode_paused=True)
        return {"status": "retrying", "message": "Mongo supervisor socket was reset locally; waiting for the existing robot supervisor to reconnect."}
    return {"status": "retrying", "message": "Mongo connection state was reset; one reconnect sequence is starting."}


def clear_robot_controller_fault(session: Session, payload: ClearRobotFault) -> dict:
    """Acknowledge one recoverable controller fault without powering or moving the arm."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not payload.confirmed:
        raise problem(422, "Confirm that the cell was inspected before clearing a robot fault.")
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Configure a physical Mongo controller before clearing its fault.")
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before clearing a robot fault.")
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if motion:
        raise problem(409, "Wait for the active robot movement before clearing its controller fault.")
    try:
        return clear_robot_fault(settings.robot_host.strip(), settings.robot_timeout_seconds)
    except RobotDashboardError as exc:
        raise problem(409, f"Robot fault was not cleared: {exc}") from exc


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

    result = {
        "x_mm": round(pose[0] * 1000, 3),
        "y_mm": round(pose[1] * 1000, 3),
        "z_mm": round(pose[2] * 1000, 3),
        "rx_rad": round(pose[3], 6),
        "ry_rad": round(pose[4], 6),
        "rz_rad": round(pose[5], 6),
        "timestamp": snapshot.get("timestamp"),
    }
    joints = _actual_joint_positions(snapshot)
    if joints is not None:
        result["joints_rad"] = [round(joint, 6) for joint in joints]
    return result


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
    occupied.update(
        session.scalars(
            select(Pallet.return_pool_slot_number).where(
                Pallet.location.in_(("machine", "robot_held")),
                Pallet.return_pool_slot_number.is_not(None),
                Pallet.id != exclude_id,
            )
        ).all()
    )
    for number in range(1, settings.pool_slot_count + 1):
        if number not in occupied:
            return number
    raise problem(409, "The pallet pool is full.")


def _pool_slot_reservation_owner(
    session: Session,
    pool_slot_number: int,
    *,
    exclude_id: str | None = None,
) -> Pallet | None:
    return session.scalar(
        select(Pallet).where(
            Pallet.location.in_(("machine", "robot_held")),
            Pallet.return_pool_slot_number == pool_slot_number,
            Pallet.id != exclude_id,
        )
    )


def best_pool_return_slot(session: Session, settings: AppSettings, pallet: Pallet) -> int:
    occupied = set(session.scalars(
        select(Pallet.pool_slot_number).where(Pallet.location == "pool", Pallet.id != pallet.id)
    ).all())
    reserved = set(session.scalars(
        select(Pallet.return_pool_slot_number).where(
            Pallet.location.in_(("machine", "robot_held")),
            Pallet.return_pool_slot_number.is_not(None),
            Pallet.id != pallet.id,
        )
    ).all())
    available = [
        slot for slot in range(1, settings.pool_slot_count + 1)
        if slot not in occupied and slot not in reserved
    ]
    if not available:
        raise problem(409, "No unoccupied, unreserved pallet-pool position is available.")
    preferred = pallet.return_pool_slot_number
    if preferred in available:
        return preferred
    if preferred is not None:
        return min(available, key=lambda slot: (abs(slot - preferred), slot))
    return available[0]


def next_pallet_name(session: Session) -> str:
    used_names = {
        name.casefold() for name in session.scalars(select(Pallet.name)).all()
    }
    available_names = [name for name in PALLET_NAMES if name.casefold() not in used_names]
    if available_names:
        return random.choice(available_names)
    raise problem(409, "All configured pallet names are currently in use.")


def create_pallet(session: Session, payload: CreatePallet) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not math.isfinite(payload.weight_kg):
        raise problem(422, "Weight must be a finite positive number.")
    program_path = None
    if payload.program_path:
        program_path = validate_program(payload.program_path, set(pallet_program_files(session)))
    pallet = Pallet(
        id=str(uuid4()),
        name=next_pallet_name(session),
        location="pool",
        queue_position=None,
        pool_slot_number=first_open_pool_slot(session, settings),
        **payload.model_dump(exclude={"expected_revision", "program_path"}),
        program_path=program_path,
    )
    if program_path:
        _store_pallet_program_metadata(pallet, read_assigned_program_metadata(settings, program_path))
    else:
        _clear_pallet_program_metadata(pallet)
    session.add(pallet)
    bump(settings)
    commit_or_conflict(session)


def update_pallet(session: Session, pallet_id: str, payload: UpdatePallet) -> str | None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    assert_pallet_manageable_during_run(settings, pallet)
    if not math.isfinite(payload.weight_kg):
        raise problem(422, "Weight must be a finite positive number.")
    program_path = None
    if payload.program_path:
        program_path = validate_program(payload.program_path, set(pallet_program_files(session)))
    values = payload.model_dump(exclude={"expected_revision", "program_path"})
    for key, value in values.items():
        setattr(pallet, key, value)
    pallet.program_path = program_path
    if program_path:
        _store_pallet_program_metadata(pallet, read_assigned_program_metadata(settings, program_path))
    else:
        _clear_pallet_program_metadata(pallet)
    bump(settings)
    commit_or_conflict(session)
    refresh_stack_light(session)
    return resume_armed_run_mode(session)


def duplicate_pallet(session: Session, pallet_id: str, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    source = session.get(Pallet, pallet_id)
    if not source:
        raise problem(404, "Pallet not found.")
    assert_pallet_manageable_during_run(settings, source)
    session.add(
        Pallet(
            id=str(uuid4()),
            name=next_pallet_name(session),
            workholding=source.workholding,
            weight_kg=source.weight_kg,
            content_status=source.content_status,
            program_path=source.program_path,
            program_tools_json=source.program_tools_json,
            program_tool_counts_json=source.program_tool_counts_json,
            program_wcs_json=source.program_wcs_json,
            expected_cycle_seconds=source.expected_cycle_seconds,
            program_metadata_state=source.program_metadata_state,
            program_metadata_detail=source.program_metadata_detail,
            program_cycle_basis=source.program_cycle_basis,
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
    _assert_no_locked_motion(session)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    assert_pallet_manageable_during_run(settings, pallet)
    if settings.run_mode_enabled and payload.destination == "machine":
        raise problem(409, "The mill position cannot be changed manually while Run Mode is active.")
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
        reserved_by = _pool_slot_reservation_owner(session, pool_slot, exclude_id=pallet_id)
        if reserved_by:
            raise problem(409, f"Pool position {pool_slot} is reserved for {reserved_by.name}.")
    elif payload.pool_slot_number is not None:
        raise problem(422, "Pool position is only valid for a pool destination.")

    was_queued = pallet.queue_position is not None
    if was_queued and payload.destination not in {"pool", "on_deck"}:
        pallet.queue_position = None
        session.flush()
        compact_queue(session, pallet.id)

    previous_location = pallet.location
    previous_pool_slot = pallet.pool_slot_number
    if previous_location == "machine" and payload.destination != "machine":
        settings.machine_state = "idle"
    pallet.location = payload.destination
    pallet.pool_slot_number = pool_slot if payload.destination == "pool" else None
    if payload.destination == "machine":
        pallet.return_pool_slot_number = previous_pool_slot or pallet.return_pool_slot_number
    elif payload.destination == "pool" or previous_location in {"machine", "robot_held"}:
        pallet.return_pool_slot_number = None

    bump(settings)
    commit_or_conflict(session)


def manually_return_mill_pallet_to_pool(
    session: Session,
    pallet_id: str,
    payload: ManualReturnPallet,
) -> int:
    """Reconcile a mill record after an operator physically returned the pallet.

    This deliberately changes only the database. It must remain independent of
    robot telemetry, Dashboard, supervisor, and CNC communications so it is
    usable after a controller or network fault.
    """
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    _assert_no_locked_motion(session)
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before manually reconciling a pallet from the mill.")
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if pallet.location != "machine":
        raise problem(409, "Only a pallet currently recorded in the mill can be manually returned to the Pool.")

    pool_slot = best_pool_return_slot(session, settings, pallet)
    previous_return_slot = pallet.return_pool_slot_number
    pallet.location = "pool"
    pallet.pool_slot_number = pool_slot
    pallet.return_pool_slot_number = None
    settings.machine_state = "idle"
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record(
        "pallet_reconciliation",
        "manual_mill_return",
        "Operator manually reconciled a pallet from the mill to the pallet pool; no controller command was sent.",
        severity="warning",
        details={
            "pallet_id": pallet.id,
            "pallet_name": pallet.name,
            "pool_slot": pool_slot,
            "reserved_return_slot": previous_return_slot,
        },
    )
    return pool_slot


def _robot_motion_activity(session: Session) -> tuple[bool, dict[str, object]]:
    settings = get_settings(session)

    def motion_snapshot() -> dict:
        if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
            return robot_io_snapshot(session)
        deadline = time.monotonic() + _MOTION_TELEMETRY_READ_RETRY_SECONDS
        last_error: RobotTelemetryError | None = None
        while True:
            try:
                return read_robot_snapshot(
                    settings.robot_host.strip(),
                    settings.robot_port,
                    settings.robot_poll_hz,
                    settings.robot_timeout_seconds,
                )
            except RobotTelemetryError as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    raise problem(
                        409,
                        f"Live robot telemetry remained unavailable after bounded retries. "
                        f"Pallet movement is blocked: {last_error}",
                    ) from exc
                time.sleep(0.25)

    # Motion interlocks use the controller stream directly. Building the full
    # Debugging-page snapshot also queries Dashboard and database display data,
    # which adds latency but no safety information to this check.
    first_snapshot = motion_snapshot()
    if not first_snapshot.get("connected"):
        raise problem(409, "Live robot telemetry is unavailable. Pallet movement is blocked.")

    # RTDE velocity values can have a noticeable noise floor while a robot is holding
    # position. Compare two actual TCP poses instead, so stationary noise cannot block a move.
    time.sleep(0.2)
    snapshot = motion_snapshot()
    if not snapshot.get("connected"):
        raise problem(409, "Live robot telemetry is unavailable. Pallet movement is blocked.")
    first_pose = _actual_tcp_pose(first_snapshot)
    second_pose = _actual_tcp_pose(snapshot)
    state = {row.get("label"): row.get("value") for row in snapshot.get("state_rows", [])}
    state["_tcp_pose"] = second_pose
    state["_joint_detail_rows"] = snapshot.get("joint_detail_rows", [])
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
            moving = str(state.get("Runtime state", "")).casefold() in {"running", "playing", "resuming"}
    return moving, state


def _motion_safety_error(state: dict[str, object]) -> str | None:
    safety_mode = state.get("Safety mode")
    if safety_mode is not None and safety_mode not in {1, "normal", "NORMAL"}:
        return f"Robot safety mode changed during movement ({safety_mode!s})."
    return None


def _motion_runtime_is_idle(state: dict[str, object]) -> bool:
    runtime_state = state.get("Runtime state")
    return runtime_state is None or runtime_state in {1, "stopped", "idle", "STOPPED", "IDLE"}


def _legacy_paused_runtime_is_empty(settings: AppSettings, state: dict[str, object]) -> bool:
    """Accept CB realtime's stale paused state only when Dashboard proves no program exists."""
    if str(state.get("Runtime state", "")).casefold() != "paused":
        return False
    if state.get("Safety mode") not in {1, "normal", "NORMAL"}:
        return False
    try:
        dashboard = robot_program_status(settings.robot_host.strip(), settings.robot_timeout_seconds)
    except RobotDashboardError:
        return False
    return dashboard.get("running") is False and dashboard.get("loaded_program") is None


def _motion_final_pose_error(
    state: dict[str, object],
    settings: AppSettings,
    program_path: str,
) -> str | None:
    if PurePosixPath(program_path).suffix.lower() != ".script":
        return None
    safe = _joint_waypoint(pallet_motion_generation(settings).get("safe_pre_waypoint"), "shared safe waypoint")
    if safe is None:
        return "The generated script stopped, but its configured joint-space safe waypoint is invalid."
    actual = _actual_joint_positions({"joint_detail_rows": state.get("_joint_detail_rows", [])})
    if actual is None:
        return "The generated script stopped, but its final joint positions could not be verified."
    largest_delta = max(abs(actual[index] - safe["joints_rad"][index]) for index in range(6))
    if largest_delta > 0.05:
        return f"The generated script stopped {largest_delta:.3f} rad away from its configured joint-space safe waypoint."
    return None


def _mark_pick_as_held_after_lift(
    session: Session,
    motion: RobotMotion,
    state: dict[str, object],
    settings: AppSettings,
) -> bool:
    """Reflect a completed physical pickup while the robot safely retreats."""
    if motion.operation != "pick" or PurePosixPath(motion.program_path).suffix.lower() != ".script":
        return False
    pallet = session.get(Pallet, motion.pallet_id)
    if not pallet or pallet.location == "robot_held":
        return False
    if pallet.location != "pool" or pallet.pool_slot_number != motion.source_slot:
        return False
    pose = state.get("_tcp_pose")
    if not isinstance(pose, tuple) or len(pose) < 3:
        return False

    locations = pallet_location_positions(settings).get("pool_locations", [])
    location = next((item for item in locations if item.get("slot") == motion.source_slot), None)
    generation = pallet_motion_generation(settings)
    if not location:
        return False
    try:
        target_x = float(location["x_mm"]) / 1000
        target_y = float(location["y_mm"]) / 1000
        target_z = (float(location["z_mm"]) + float(generation["lift_z_clearance_mm"])) / 1000
        horizontal_error = math.hypot(float(pose[0]) - target_x, float(pose[1]) - target_y)
        lifted_clear = float(pose[2]) >= target_z - 0.02
    except (KeyError, TypeError, ValueError):
        return False
    if horizontal_error > 0.04 or not lifted_clear:
        return False

    held = session.scalar(select(Pallet).where(Pallet.location == "robot_held", Pallet.id != pallet.id))
    if held:
        return False
    pallet.location = "robot_held"
    pallet.pool_slot_number = None
    pallet.return_pool_slot_number = motion.source_slot
    bump(settings)
    commit_or_conflict(session)
    return True


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


def _actual_joint_positions(snapshot: dict) -> tuple[float, float, float, float, float, float] | None:
    rows = snapshot.get("joint_detail_rows", [])
    if not isinstance(rows, list) or len(rows) < 6:
        return None
    try:
        values = tuple(float(row["actual_position"]) for row in rows[:6])
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
    if settings.robot_supervisor_enabled:
        if not settings.robot_supervisor_activation_verified:
            raise problem(409, "Supervisor mode has not passed its no-motion handshake test.")
        status = robot_supervisor().status()
        if not status["connected"]:
            if settings.robot_supervisor_pre_dispatch_fallback:
                return
            raise problem(409, "Mongo supervisor is not connected.")
        if status["latched"]:
            raise problem(409, "Mongo supervisor is latched. Reconcile it before starting another movement.")
        unresolved = session.scalar(
            select(RobotSupervisorCommand).where(
                RobotSupervisorCommand.sequence == settings.robot_supervisor_last_sequence,
                RobotSupervisorCommand.status.in_(("uncertain", "latched", "faulted", "dispatching", "sent", "accepted", "running")),
            )
        )
        if unresolved:
            raise problem(409, "Mongo has an unresolved supervisor command. Reconcile it before starting another movement.")
        telemetry = status.get("telemetry") or {}
        age = status.get("telemetry_age_seconds")
        if not telemetry or age is None or age > settings.robot_supervisor_heartbeat_seconds * 4:
            raise problem(409, "Mongo supervisor telemetry is stale or unavailable.")
        if telemetry.get("safety_mode") != 1:
            raise problem(409, f"Robot safety mode is not normal ({telemetry.get('safety_mode')!s}).")
        if telemetry.get("runtime_state") != 1:
            raise problem(409, f"Robot runtime is not idle ({telemetry.get('runtime_state')!s}).")
        return
    moving, state = _robot_motion_activity(session)
    if moving:
        raise problem(409, "Robot TCP is moving. Wait until the robot is stationary before starting a pallet movement.")
    safety_mode = state.get("Safety mode")
    runtime_state = state.get("Runtime state")
    if safety_mode not in {1, "normal", "NORMAL"}:
        raise problem(409, f"Robot safety mode is not normal ({safety_mode!s}).")
    if runtime_state not in {1, "stopped", "idle", "STOPPED", "IDLE"} and not _legacy_paused_runtime_is_empty(settings, state):
        raise problem(409, f"Robot runtime is not idle ({runtime_state!s}).")


def _locked_motion(session: Session) -> RobotMotion | None:
    return session.scalar(
        select(RobotMotion)
        .where(RobotMotion.status.in_(("requested", "running", "faulted")))
        .order_by(RobotMotion.created_at.desc())
    )


def _active_reliability_run(session: Session) -> RobotReliabilityRun | None:
    return session.scalar(
        select(RobotReliabilityRun)
        .where(RobotReliabilityRun.status.in_(("requested", "running")))
        .order_by(RobotReliabilityRun.created_at.desc())
    )


def _assert_reliability_inactive(session: Session) -> None:
    if _active_reliability_run(session):
        raise problem(409, "Stop or wait for the queue reliability test before using other robot or schedule controls.")


def _assert_no_locked_motion(session: Session) -> None:
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before changing pallet records.")
    _assert_reliability_inactive(session)


def _assert_queue_edit_allowed(session: Session, pallet_ids: set[str]) -> RobotMotion | None:
    """Allow queue planning during motion without changing the motion's pallet."""
    _assert_reliability_inactive(session)
    motion = _locked_motion(session)
    if motion and motion.pallet_id in pallet_ids:
        raise problem(
            409,
            "The pallet assigned to the active robot movement cannot be changed in the Queue until that movement finishes.",
        )
    return motion


def _finish_motion(session: Session, motion: RobotMotion, success: bool, detail: str | None = None) -> None:
    """Persist a confirmed physical result without discarding concurrent queue edits."""
    motion_id = motion.id
    for completion_attempt in range(3):
        pallet = session.get(Pallet, motion.pallet_id)
        if not pallet:
            motion.status = "faulted"
            motion.failure_detail = "The pallet was deleted while its robot movement was active."
        elif success and motion.operation == "pick":
            pallet.location = "robot_held"
            pallet.pool_slot_number = None
            pallet.return_pool_slot_number = motion.source_slot
            motion.status = "succeeded"
        elif success and motion.operation == "put":
            pallet.location = "pool"
            pallet.pool_slot_number = motion.destination_slot
            pallet.return_pool_slot_number = None
            motion.status = "succeeded"
        elif success and motion.operation == "load_mill":
            # Queue membership is virtual; a pallet leaves its run position only once it is in the mill.
            if pallet.queue_position is not None:
                pallet.queue_position = None
                session.flush()
                compact_queue(session, pallet.id)
            pallet.location = "machine"
            pallet.pool_slot_number = None
            pallet.return_pool_slot_number = motion.source_slot or pallet.return_pool_slot_number
            get_settings(session).machine_state = "running"
            motion.status = "succeeded"
        elif success and motion.operation == "unload_mill":
            pallet.location = "pool"
            pallet.pool_slot_number = motion.destination_slot
            pallet.return_pool_slot_number = None
            get_settings(session).machine_state = "idle"
            motion.status = "succeeded"
        else:
            motion.status = "faulted"
            motion.failure_detail = detail or "Robot motion failed. Inspect the cell and reconcile the pallet location."
        motion.completed_at = datetime.now(timezone.utc).isoformat()
        settings = get_settings(session)
        bump(settings)
        try:
            commit_or_conflict(session)
        except HTTPException as exc:
            if exc.status_code != 409 or completion_attempt == 2:
                raise
            diagnostics().record(
                "robot_motion",
                "completion_commit_retry",
                "Retrying a confirmed robot-motion completion after a concurrent schedule update.",
                severity="warning",
                details={"motion_id": motion_id, "attempt": completion_attempt + 1},
            )
            motion = session.get(RobotMotion, motion_id)
            if not motion or motion.status not in {"requested", "running"}:
                return
            continue
        diagnostics().record(
            "robot_motion",
            "completed" if motion.status == "succeeded" else "faulted",
            f"Robot motion {motion.operation} {motion.status}.",
            severity="info" if motion.status == "succeeded" else "error",
            details={
                "motion_id": motion.id,
                "pallet_id": motion.pallet_id,
                "operation": motion.operation,
                "source_slot": motion.source_slot,
                "destination_slot": motion.destination_slot,
                "retry_count": motion.retry_count,
                "failure_detail": motion.failure_detail,
            },
        )
        if not success:
            _send_push_notification(
                settings,
                "error",
                f"MPS robot error: {motion.operation} for pallet {pallet.name if pallet else motion.pallet_id} failed. {motion.failure_detail or 'Inspect the cell.'}",
            )
        return


def start_robot_supervisor_listener(session: Session) -> None:
    settings = get_settings(session)
    robot_supervisor().start(
        settings.robot_supervisor_listen_host,
        settings.robot_supervisor_port,
        settings.robot_supervisor_heartbeat_seconds,
        settings.robot_supervisor_telemetry_hz,
    )


def stop_robot_supervisor_listener() -> None:
    robot_supervisor().stop()


def recover_robot_supervisor_connection(session: Session, *, trigger: str, allow_run_mode_paused: bool = False) -> bool:
    """Reset only the desktop listener so Mongo's existing supervisor reconnects.

    This has no Dashboard, RTDE, file-transfer, or motion side effect. It is
    forbidden while an automated operation is active because an in-flight move
    must be reconciled, never treated as a normal reconnect.
    """
    settings = get_settings(session)
    if not (settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified):
        return False
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if active_motion or (settings.run_mode_enabled and not (allow_run_mode_paused and _run_mode_allows_robot_reconnect(settings))):
        return False
    if robot_supervisor().status().get("connected"):
        return False
    robot_supervisor().stop()
    robot_supervisor().start(
        settings.robot_supervisor_listen_host,
        settings.robot_supervisor_port,
        settings.robot_supervisor_heartbeat_seconds,
        settings.robot_supervisor_telemetry_hz,
    )
    diagnostics().record(
        "robot_supervisor",
        "listener_recovery",
        "Reset the local supervisor listener and is waiting for Mongo to reconnect.",
        severity="warning",
        details={"trigger": trigger, "port": settings.robot_supervisor_port},
    )
    return True


def restart_robot_supervisor_listener(session: Session, *, trigger: str) -> dict[str, object]:
    """Restart only the local TCP listener while the cell is idle.

    Mongo owns the reconnect loop. This does not send a robot command, but it
    is prohibited while automation may be in flight so a transport reset is
    never mistaken for motion recovery.
    """
    settings = get_settings(session)
    if not (settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified):
        raise problem(409, "Enable and verify the Mongo supervisor before restarting its listener.")
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if settings.run_mode_enabled or active_motion:
        raise problem(409, "Stop Run Mode and wait for every pallet movement before restarting the supervisor listener.")
    robot_supervisor().stop()
    robot_supervisor().start(
        settings.robot_supervisor_listen_host,
        settings.robot_supervisor_port,
        settings.robot_supervisor_heartbeat_seconds,
        settings.robot_supervisor_telemetry_hz,
    )
    diagnostics().record(
        "robot_supervisor",
        "listener_restart_requested",
        "Operator restarted only the local supervisor listener; waiting for Mongo to reconnect.",
        severity="warning",
        details={"trigger": trigger, "port": settings.robot_supervisor_port},
    )
    return {
        "status": "retrying",
        "message": "The local listener restarted. Mongo will reconnect using its bounded retry loop; no robot command was sent.",
    }


def recover_robot_supervisor_program(
    session: Session,
    *,
    trigger: str,
    allow_run_mode_manual_pause: bool = False,
    allow_run_mode_paused: bool = False,
) -> dict[str, object]:
    """Restore Mongo's no-motion supervisor without replacing operator work."""
    settings = get_settings(session)
    if not (settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified):
        raise problem(409, "Enable and verify the Mongo supervisor before recovering it.")
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if active_motion or (
        settings.run_mode_enabled
        and not (
            (allow_run_mode_manual_pause and settings.run_mode_manual_robot_pause)
            or (allow_run_mode_paused and _run_mode_allows_robot_reconnect(settings))
        )
    ):
        raise problem(409, "Stop Run Mode and wait for active pallet motion before recovering the Mongo supervisor.")
    if robot_supervisor().status().get("connected"):
        return robot_supervisor_status(session)
    try:
        controller = robot_program_status(settings.robot_host.strip(), settings.robot_timeout_seconds)
    except RobotDashboardError as exc:
        raise problem(502, f"Cannot confirm Mongo is idle before supervisor recovery: {exc}") from exc
    loaded_program = str(controller.get("loaded_program") or "").strip()
    if controller.get("running"):
        raise problem(409, "Mongo is running a controller program. Supervisor recovery will not interrupt it.")
    supervisor_selected = PurePosixPath(loaded_program).name.casefold() == "mongo_supervisor.script"
    if loaded_program and not supervisor_selected:
        raise problem(
            409,
            "Mongo has a controller program selected (" + loaded_program + "). "
            "Clear it or finish operator work before recovering the supervisor.",
        )
    status = (
        bootstrap_robot_supervisor(
            session,
            allow_run_mode_manual_pause=allow_run_mode_manual_pause,
            allow_run_mode_paused=allow_run_mode_paused,
        )
        if allow_run_mode_manual_pause or allow_run_mode_paused
        else bootstrap_robot_supervisor(session)
    )
    diagnostics().record(
        "robot_supervisor",
        "program_recovered",
        "Restored Mongo's no-motion supervisor after it was not running or after manual jogging paused it.",
        severity="warning",
        details={"trigger": trigger},
    )
    return status


def auto_recover_controller_connections(session: Session, progress=None) -> dict[str, object]:
    """Apply bounded, non-motion recovery and return every decision made."""
    settings = get_settings(session)
    results: list[dict[str, object]] = []
    reconciliation: dict[str, object] | None = None
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    automation_idle = not settings.run_mode_enabled and active_motion is None

    if progress:
        progress("Checking the Mongo controller connection.", results)

    robot_status = robot_supervisor_status(session)
    robot_configured = settings.robot_connection_mode == "physical" and bool(settings.robot_host.strip())
    robot_recovery_handled = False
    if robot_configured and not (
        settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified
    ):
        robot_recovery_handled = True
        if not automation_idle:
            results.append({"controller": "Mongo", "action": "Deferred", "detail": "Mongo supervisor recovery waits for Run Mode and pallet motion to become idle."})
        elif robot_status.get("connected"):
            if robot_status.get("reconciliation_required"):
                repairable = _repairable_supervisor_maintenance_gap(session, settings, robot_status)
                if repairable is None:
                    results.append({"controller": "Mongo", "action": "Deferred", "detail": "Mongo is connected, but its command sequence requires physical reconciliation before routing can be enabled."})
                else:
                    bootstrap_robot_supervisor(session)
                    current = get_settings(session)
                    current.robot_supervisor_enabled = True
                    current.robot_supervisor_activation_verified = True
                    bump(current)
                    commit_or_conflict(session)
                    robot_status = robot_supervisor_status(session)
                    results.append({
                        "controller": "Mongo",
                        "action": "Recovered",
                        "detail": "Closed the interrupted no-motion maintenance sequence and restored verified supervisor routing.",
                    })
            else:
                settings.robot_supervisor_enabled = True
                settings.robot_supervisor_activation_verified = True
                bump(settings)
                commit_or_conflict(session)
                results.append({"controller": "Mongo", "action": "Enabled", "detail": "Mongo supervisor is connected and verified command routing is enabled."})
        else:
            try:
                controller = robot_program_status(settings.robot_host.strip(), settings.robot_timeout_seconds)
                loaded_program = str(controller.get("loaded_program") or "").strip()
                supervisor_program = "mongo_supervisor" in loaded_program.casefold()
                if controller.get("running"):
                    results.append({"controller": "Mongo", "action": "Deferred", "detail": "Mongo is running a controller program; recovery will not interrupt it."})
                else:
                    if loaded_program and not supervisor_program:
                        results.append({
                            "controller": "Mongo",
                            "action": "Selected program preserved",
                            "detail": f"{loaded_program} is selected but stopped. The no-motion supervisor will run without unloading or starting it.",
                        })
                    bootstrap_robot_supervisor(session)
                    current = get_settings(session)
                    current.robot_supervisor_enabled = True
                    current.robot_supervisor_activation_verified = True
                    bump(current)
                    commit_or_conflict(session)
                    refreshed = robot_supervisor().status()
                    results.append({
                        "controller": "Mongo",
                        "action": "Recovered" if refreshed.get("connected") else "Recovery pending",
                        "detail": "Restored the no-motion Mongo supervisor and local listener." if refreshed.get("connected") else "The no-motion Mongo supervisor was requested but has not reconnected yet.",
                    })
            except (HTTPException, RobotDashboardError, RobotFileAccessError, RobotTelemetryError) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append({"controller": "Mongo", "action": "Deferred", "detail": str(detail)})
        try:
            dashboard = robot_dashboard_health(settings.robot_host.strip(), settings.robot_timeout_seconds)
            results.append({"controller": "Mongo", "action": "Dashboard checked", "detail": "Dashboard responded." if dashboard else "Dashboard returned no health detail."})
        except RobotDashboardError as exc:
            results.append({"controller": "Mongo", "action": "Dashboard unavailable", "detail": str(exc)})

    if not robot_recovery_handled and settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified:
        heartbeat_limit = max(5.0, settings.robot_supervisor_heartbeat_seconds * 4)
        telemetry_age = robot_status.get("telemetry_age_seconds")
        stale = telemetry_age is None or float(telemetry_age) > heartbeat_limit
        if robot_status.get("connected") and not stale:
            results.append({"controller": "Mongo", "action": "Healthy", "detail": "Supervisor connection and telemetry are live."})
        else:
            issue = "telemetry is stale" if robot_status.get("connected") else "supervisor is disconnected"
            if not automation_idle:
                results.append({"controller": "Mongo", "action": "Deferred", "detail": f"Mongo {issue}; recovery waits for Run Mode and pallet motion to become idle."})
            else:
                try:
                    retry_robot_telemetry(session)
                    results.append({"controller": "Mongo", "action": "Transport reset", "detail": f"Mongo {issue}; reset the local connection cache and listener."})
                    deadline = time.monotonic() + max(3.0, settings.robot_supervisor_heartbeat_seconds * 3)
                    while time.monotonic() < deadline:
                        refreshed = robot_supervisor().status()
                        refreshed_age = refreshed.get("telemetry_age_seconds")
                        if refreshed.get("connected") and refreshed_age is not None and float(refreshed_age) <= heartbeat_limit:
                            results.append({"controller": "Mongo", "action": "Recovered", "detail": "Mongo supervisor reconnected with fresh telemetry."})
                            break
                        time.sleep(0.2)
                    else:
                        recover_robot_supervisor_program(session, trigger="auto_recover")
                        results.append({"controller": "Mongo", "action": "Supervisor restored", "detail": "Started the no-motion supervisor after Dashboard confirmed no controller program is selected."})
                except (HTTPException, RobotTelemetryError) as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    results.append({"controller": "Mongo", "action": "Deferred", "detail": str(detail)})
        try:
            dashboard = robot_dashboard_health(settings.robot_host.strip(), settings.robot_timeout_seconds)
            results.append({"controller": "Mongo", "action": "Dashboard checked", "detail": "Dashboard responded." if dashboard else "Dashboard returned no health detail."})
        except RobotDashboardError as exc:
            results.append({"controller": "Mongo", "action": "Dashboard unavailable", "detail": str(exc)})
        robot_status = robot_supervisor_status(session)
        if robot_status.get("reconciliation"):
            reconciliation = robot_status["reconciliation"]
            results.append({
                "controller": "Mongo",
                "action": "Physical confirmation required",
                "detail": "The connection is available, but the last robot command has an uncertain physical result. Confirm the observed pallet location before more motion is allowed.",
            })
    elif not robot_recovery_handled:
        results.append({"controller": "Mongo", "action": "Skipped", "detail": "Robot supervisor recovery is not enabled and verified."})

    if progress:
        progress("Checking the PathPilot mill connection.", results)

    mill_status = mill_supervisor().status()
    mill_configured = bool(
        settings.cnc_telemetry_enabled
        and settings.cnc_host.strip()
        and settings.cnc_ssh_username
        and settings.cnc_ssh_password
    )
    mill_recovery_handled = False
    if mill_configured:
        try:
            observed_cycle = read_linuxcnc_cycle_state(
                settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                settings.cnc_ssh_password, settings.cnc_timeout_seconds,
            )
            observed_program = str(observed_cycle.get("program") or "").strip()
            machine_pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
            expected_program = (machine_pallet.program_path or "").strip() if machine_pallet else ""
            if (
                observed_cycle.get("interp_state") != 1 and observed_program
                and (not expected_program or PurePosixPath(observed_program).name != PurePosixPath(expected_program).name)
            ):
                mill_recovery_handled = True
                results.append({
                    "controller": "Mill",
                    "action": "Different program running",
                    "detail": f"PathPilot is running {observed_program}. It does not match Mongo's expected program ({expected_program or 'none'}), so recovery left it untouched and continued with other services.",
                })
        except CncTelemetryError:
            # The normal recovery path below records telemetry failures with a
            # user-facing action; do not mask it during this non-invasive check.
            pass
    if not mill_recovery_handled and mill_configured and (
        not settings.mill_supervisor_activation_verified or not mill_status.get("connected")
    ):
        mill_recovery_handled = True
        if not automation_idle:
            results.append({"controller": "Mill", "action": "Deferred", "detail": "Mill supervisor recovery waits for Run Mode and pallet motion to become idle."})
        else:
            desired_enabled = bool(settings.mill_supervisor_enabled)
            try:
                bootstrap_mill_supervisor(session)
                if desired_enabled:
                    current = get_settings(session)
                    set_mill_supervisor_activation(
                        session,
                        MillSupervisorActivation(
                            expected_revision=current.revision,
                            enabled=True,
                            confirmed=True,
                        ),
                    )
                refreshed = mill_supervisor().status()
                results.append({
                    "controller": "Mill",
                    "action": "Recovered" if refreshed.get("connected") else "Recovery pending",
                    "detail": "Restored the no-motion PathPilot supervisor and local listener." if refreshed.get("connected") else "The no-motion PathPilot supervisor was requested but has not reconnected yet.",
                })
            except (HTTPException, CncTelemetryError, RobotFileAccessError) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append({"controller": "Mill", "action": "Deferred", "detail": str(detail)})

    if not mill_recovery_handled and settings.mill_supervisor_enabled and settings.mill_supervisor_activation_verified:
        mill_heartbeat_limit = max(10.0, settings.mill_supervisor_heartbeat_seconds * 3)
        mill_age = mill_status.get("telemetry_age_seconds")
        mill_stale = mill_age is None or float(mill_age) > mill_heartbeat_limit
        try:
            mill_cycle = read_linuxcnc_cycle_state(
                settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                settings.cnc_ssh_password, settings.cnc_timeout_seconds,
            )
            mill_idle = mill_cycle.get("interp_state") == 1
            running_program = str(mill_cycle.get("program") or "").strip()
            machine_pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
            expected_program = (machine_pallet.program_path or "").strip() if machine_pallet else ""
            different_program_running = bool(
                not mill_idle and running_program
                and (not expected_program or PurePosixPath(running_program).name != PurePosixPath(expected_program).name)
            )
            if different_program_running:
                results.append({
                    "controller": "Mill",
                    "action": "Different program running",
                    "detail": f"PathPilot is running {running_program}. It does not match Mongo's expected program ({expected_program or 'none'}), so recovery left it untouched and continued with other services.",
                })
            else:
                results.append({"controller": "Mill", "action": "PathPilot checked", "detail": "PathPilot reports Idle." if mill_idle else "PathPilot is not idle; no helper restart was attempted."})
        except CncTelemetryError as exc:
            mill_idle = False
            different_program_running = False
            results.append({"controller": "Mill", "action": "PathPilot unavailable", "detail": str(exc)})
        if mill_status.get("connected") and not mill_stale:
            results.append({"controller": "Mill", "action": "Healthy", "detail": "Mill supervisor connection and telemetry are live."})
        else:
            if different_program_running:
                # A separately operated program is never stopped, reloaded, or
                # used as a reason to block recovery of independent services.
                pass
            elif not automation_idle or not mill_idle:
                results.append({"controller": "Mill", "action": "Deferred", "detail": "Mill supervisor is stale or disconnected; helper recovery waits for idle automation and an idle PathPilot interpreter."})
            else:
                try:
                    mill_supervisor().start(settings.mill_supervisor_listen_host, settings.mill_supervisor_port)
                    start_mill_supervisor_agent(
                        settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
                        settings.cnc_ssh_password, settings.cnc_timeout_seconds,
                        _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
                    )
                    deadline = time.monotonic() + max(5.0, settings.mill_supervisor_heartbeat_seconds * 3)
                    while time.monotonic() < deadline:
                        refreshed = mill_supervisor().status()
                        refreshed_age = refreshed.get("telemetry_age_seconds")
                        if refreshed.get("connected") and refreshed_age is not None and float(refreshed_age) <= mill_heartbeat_limit:
                            results.append({"controller": "Mill", "action": "Recovered", "detail": "Mill helper reconnected with fresh telemetry."})
                            break
                        time.sleep(0.2)
                    else:
                        results.append({"controller": "Mill", "action": "Recovery pending", "detail": "The idle mill helper restart was requested; it has not reconnected yet."})
                except CncTelemetryError as exc:
                    results.append({"controller": "Mill", "action": "Deferred", "detail": str(exc)})
    elif not mill_recovery_handled:
        results.append({"controller": "Mill", "action": "Skipped", "detail": "Mill supervisor recovery is not enabled and verified."})

    if any(item["action"] in {"Dashboard unavailable", "PathPilot unavailable", "Recovery pending"} for item in results):
        trigger_network_diagnostic_on_robot_loss()
        results.append({"controller": "Network", "action": "Diagnostic queued", "detail": "Queued the rate-limited connectivity matrix for the next recovery diagnosis."})

    diagnostics().record(
        "controller_recovery",
        "auto_recover_requested",
        "Applied bounded non-motion controller recovery steps.",
        severity="warning",
        details={"results": results},
    )
    response: dict[str, object] = {"results": results}
    if reconciliation:
        response["reconciliation"] = reconciliation
    return response


_RECOVERY_ACTIVE_STATUSES = {"awaiting_safety", "running", "awaiting_restart", "ready", "handoff"}


def _recovery_json(value: str, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _recovery_motion_options(motion: RobotMotion, pallet: Pallet | None) -> list[dict[str, str]]:
    name = pallet.name if pallet else "The pallet"
    options: list[dict[str, str]] = []
    if motion.operation == "pick":
        if motion.source_slot is not None:
            options.append({"value": "source_pool", "label": f"{name} is still in pool slot {motion.source_slot}."})
        options.append({"value": "robot_held", "label": f"The robot is holding {name}."})
    elif motion.operation == "put":
        options.append({"value": "robot_held", "label": f"The robot is still holding {name}."})
        if motion.destination_slot is not None:
            options.append({"value": "destination_pool", "label": f"{name} is in pool slot {motion.destination_slot}."})
    elif motion.operation == "load_mill":
        if motion.source_slot is not None:
            options.append({"value": "source_pool", "label": f"{name} is still in pool slot {motion.source_slot}."})
        options.append({"value": "robot_held", "label": f"The robot is holding {name}."})
        options.append({"value": "machine", "label": f"{name} is physically loaded in the mill."})
    elif motion.operation == "unload_mill":
        options.append({"value": "machine", "label": f"{name} is still physically in the mill."})
        options.append({"value": "robot_held", "label": f"The robot is holding {name}."})
        if motion.destination_slot is not None:
            options.append({"value": "destination_pool", "label": f"{name} is in pool slot {motion.destination_slot}."})
    return options


def _recovery_choice(
    key: str,
    title: str,
    detail: str,
    options: list[dict[str, str]],
) -> dict[str, object]:
    return {"type": "choice", "key": key, "title": title, "detail": detail, "options": options}


def _recovery_instruction(key: str, title: str, detail: str) -> dict[str, object]:
    return {"type": "instruction", "key": key, "title": title, "detail": detail}


def _robot_recovery_command_guidance(reconciliation: dict[str, object]) -> dict[str, object]:
    """Describe an uncertain supervisor command without exposing protocol jargon.

    The sequence number is useful in diagnostics, but it gives an operator no
    way to decide what to observe.  Recovery questions must name the physical
    action (and pallet when known), then make it explicit when the command was
    only an MPS/robot communication action with no possible robot motion.
    """
    operation = str(reconciliation.get("operation") or "").strip()
    pallet_name = str(reconciliation.get("pallet_name") or "").strip()
    actions = {
        "pick": "pick a pallet from the pool",
        "pick_pool": "pick a pallet from the pool",
        "put": "place a pallet in the pool",
        "put_pool": "place a pallet in the pool",
        "load": "load a pallet into the mill",
        "load_mill": "load a pallet into the mill",
        "unload": "unload a pallet from the mill",
        "unload_mill": "unload a pallet from the mill",
        "align_pallet_pick": "move to the pallet-pick alignment position",
    }
    no_motion_actions = {
        "bootstrap_restart": "restart the Mongo supervisor connection",
        "clear_latch": "clear the Mongo supervisor communication latch",
        "clear_link_loss_latch": "clear the Mongo connection-loss latch",
        "enter_maintenance": "enter robot maintenance mode",
        "set_output": "change a robot output signal",
    }
    if operation in no_motion_actions:
        action = no_motion_actions[operation]
        return _recovery_choice(
            "robot_command",
            "Confirm the Mongo connection action",
            f"MPS did not receive confirmation that it could {action}. This action does not move the robot or a pallet.",
            [
                {"value": "accept_completed", "label": "It completed — continue."},
                {"value": "mark_faulted", "label": "It did not complete — record the issue."},
            ],
        )

    action = actions.get(operation, "perform the last requested robot action")
    pallet = f" for pallet {pallet_name}" if pallet_name else ""
    return _recovery_choice(
        "robot_command",
        "Check the last robot action",
        f"MPS did not receive completion confirmation after telling the robot to {action}{pallet}. Look at the cell before choosing. This choice records what already happened; it will not move or retry the robot.",
        [
            {"value": "accept_completed", "label": "The action finished — continue."},
            {"value": "mark_faulted", "label": "The action did not finish — record the issue."},
        ],
    )


def _mill_recovery_command_guidance(reconciliation: dict[str, object]) -> dict[str, object]:
    operation = str(reconciliation.get("operation") or "").strip()
    actions = {
        "run_program": "run the selected CNC program",
        "load_program": "load the selected CNC program",
        "stop": "stop the CNC program",
        "reset": "reset PathPilot",
    }
    action = actions.get(operation, "perform the last requested mill action")
    return _recovery_choice(
        "mill_command",
        "Check the last mill action",
        f"MPS did not receive completion confirmation after asking PathPilot to {action}. Inspect PathPilot and the workpiece before choosing. This only records what already happened; it will not rerun a program.",
        [
            {"value": "accept_completed", "label": "The action finished — continue."},
            {"value": "mark_faulted", "label": "The action did not finish — record the issue."},
        ],
    )


def _recovery_observe(session: Session) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Collect recovery facts without issuing motion or controller commands."""
    settings = get_settings(session)
    faults: list[dict[str, object]] = []
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(
        ("requested", "running", "faulted")
    )))
    if motion:
        pallet = session.get(Pallet, motion.pallet_id)
        motion_options = _recovery_motion_options(motion, pallet) if motion.status == "faulted" else []
        motion_guidance = (
            _recovery_choice(
                "robot_motion",
                f"Where is {pallet.name if pallet else 'the pallet'} physically now?",
                "Select only the location you directly verified. This updates the ledger and does not move the robot.",
                motion_options,
            )
            if motion_options
            else _recovery_instruction(
                "active_robot_motion",
                "Wait for the active robot movement to settle",
                "Request a Run Mode stop if offered, then wait until the current atomic movement completes or faults. Recovery will not interrupt it mid-motion.",
            )
        )
        faults.append({
            "code": "active_or_faulted_motion",
            "severity": "handoff",
            "title": "Robot pallet movement needs physical reconciliation",
            "detail": f"{motion.operation} for {motion.pallet_id} is {motion.status}. The wizard will not guess its physical result.",
            "motion_id": motion.id,
            "operation": motion.operation,
            "pallet_name": pallet.name if pallet else None,
            "board_location": pallet.location if pallet else None,
            "options": motion_options,
            "guidance": motion_guidance,
        })
    machine_pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
    if settings.run_mode_enabled:
        faults.append({
            "code": "run_mode_active",
            "severity": "handoff",
            "title": "Run Mode is active",
            "detail": "Stop or physically verify the current operation before recovery can continue.",
            "guidance": _recovery_choice(
                "run_mode",
                "Stop automated production safely?",
                "This prevents another automated step from starting. It does not abort a mill program or interrupt an atomic robot movement already in progress.",
                [{"value": "request_stop", "label": "Request a safe Run Mode stop."}],
            ),
        })
    if settings.run_mode_state in {"faulted", "interrupted", "stopping"}:
        run_guidance = None
        if machine_pallet:
            run_guidance = _recovery_choice(
                "run_mode_fault",
                f"Leave {machine_pallet.name} recorded in the mill and stop the scheduler?",
                "Use this after physically verifying the pallet remains in the mill. No robot or mill motion will be commanded.",
                [{"value": "stop_keep_machine", "label": "Stop Run Mode and keep the pallet recorded in the mill."}],
            )
        faults.append({
            "code": "run_mode_fault",
            "severity": "handoff" if machine_pallet else "recoverable",
            "title": "Run Mode needs attention",
            "detail": settings.run_mode_detail or f"Run Mode is {settings.run_mode_state}.",
            "guidance": run_guidance,
        })

    robot_status = robot_supervisor_status(session)
    robot_configured = settings.robot_connection_mode == "physical" and bool(settings.robot_host.strip())
    robot_enabled = settings.robot_supervisor_enabled and settings.robot_supervisor_activation_verified
    if robot_configured and robot_status.get("reconciliation_required"):
        robot_reconciliation = robot_status.get("reconciliation") or {}
        robot_guidance = (
            _robot_recovery_command_guidance(robot_reconciliation)
            if robot_reconciliation.get("sequence") and not motion
            else None
        )
        if not robot_guidance and not motion:
            robot_guidance = _recovery_instruction(
                "robot_sequence_mismatch",
                "Robot command sequences do not agree",
                "Keep the robot stopped. Inspect the latest backend and controller sequence records; automatic recovery will not invent or discard a command.",
            )
        faults.append({
            "code": "robot_reconciliation_required",
            "severity": "handoff",
            "title": "Robot command result is uncertain",
            "detail": "Inspect the cell and reconcile the pallet location before clearing the supervisor latch.",
            "guidance": robot_guidance,
        })
    if robot_configured and not robot_status.get("connected"):
        faults.append({
            "code": "robot_supervisor_disconnected",
            "severity": "recoverable",
            "title": "Robot supervisor is disconnected",
            "detail": str(robot_status.get("last_disconnect_detail") or "The robot listener has no live controller session."),
            "guidance": _recovery_instruction(
                "robot_connectivity",
                "Restore robot controller connectivity",
                "Verify Mongo is powered on, its Ethernet cable and switch link are active, the configured IP is correct, and the Windows firewall permits the supervisor connection. Then retry.",
            ),
        })
    if robot_configured and not robot_enabled:
        faults.append({
            "code": "robot_supervisor_activation_required",
            "severity": "recoverable",
            "title": "Robot supervisor needs activation recovery",
            "detail": "The robot is configured, but its local supervisor is disabled or not yet verified. Recovery will restore the no-motion connection without changing the configured routing setting.",
            "guidance": _recovery_instruction(
                "robot_activation",
                "Verify the robot can accept its no-motion supervisor",
                "Keep the robot idle, stop any controller program, and verify the configured robot address. Recovery will redeploy and verify the supervisor without commanding motion.",
            ),
        })

    mill_status = mill_supervisor_status(session)
    mill_configured = bool(
        settings.cnc_telemetry_enabled
        and settings.cnc_host.strip()
        and settings.cnc_ssh_username
        and settings.cnc_ssh_password
    )
    mill_enabled = settings.mill_supervisor_enabled and settings.mill_supervisor_activation_verified
    if mill_configured and mill_status.get("reconciliation_required"):
        mill_reconciliation = mill_status.get("reconciliation") or {}
        mill_guidance = (
            _mill_recovery_command_guidance(mill_reconciliation)
            if mill_reconciliation.get("sequence")
            else _recovery_instruction(
                "mill_sequence_mismatch",
                "Mill command sequences do not agree",
                "Keep PathPilot idle. Inspect the latest backend and helper sequence records; automatic recovery will not invent or discard a command.",
            )
        )
        faults.append({
            "code": "mill_reconciliation_required",
            "severity": "handoff",
            "title": "Mill supervisor command state needs reconciliation",
            "detail": "Inspect the mill and reconcile the last command before another command is allowed.",
            "guidance": mill_guidance,
        })
    if mill_configured and not mill_status.get("connected"):
        faults.append({
            "code": "mill_supervisor_disconnected",
            "severity": "recoverable",
            "title": "Mill supervisor is disconnected",
            "detail": str(mill_status.get("last_disconnect_detail") or "The mill helper has no live session."),
            "guidance": _recovery_instruction(
                "mill_connectivity",
                "Restore PathPilot connectivity",
                "Verify the mill is powered on, PathPilot is running and Idle, Ethernet is connected, and the configured SSH address and credentials are correct. Then retry.",
            ),
        })
    if mill_configured and not mill_enabled:
        faults.append({
            "code": "mill_supervisor_activation_required",
            "severity": "recoverable",
            "title": "Mill supervisor needs activation recovery",
            "detail": "PathPilot is configured, but its local supervisor is disabled or not yet verified. Recovery will restore the no-motion telemetry connection and reapply the saved routing setting only after an idle handshake.",
            "guidance": _recovery_instruction(
                "mill_activation",
                "Verify PathPilot is idle for helper recovery",
                "Leave PathPilot at an Idle interpreter state and verify its SSH settings. Recovery will restart only the telemetry helper and will not start a CNC program.",
            ),
        })

    services = [
        {
            "name": "MPS backend",
            "state": "online",
            "enabled": True,
            "connected": True,
        },
        {
            "name": "Robot supervisor",
            "state": "connected" if robot_status.get("connected") else "activation required" if robot_configured and not robot_enabled else "disconnected",
            "enabled": robot_configured,
            "connected": bool(robot_status.get("connected")) if robot_configured else None,
        },
        {
            "name": "Mill supervisor",
            "state": "connected" if mill_status.get("connected") else "activation required" if mill_configured and not mill_enabled else "disconnected",
            "enabled": mill_configured,
            "connected": bool(mill_status.get("connected")) if mill_configured else None,
        },
        {
            "name": "Robot transport",
            "state": "configured" if settings.robot_connection_mode == "physical" else "simulated",
            "enabled": settings.robot_connection_mode == "physical",
            "connected": None,
        },
        {
            "name": "CNC telemetry",
            "state": "configured" if settings.cnc_telemetry_enabled else "disabled",
            "enabled": bool(settings.cnc_telemetry_enabled),
            "connected": None,
        },
    ]
    return faults, services


def _recovery_snapshot(session: Session, recovery: RecoverySession | None) -> dict[str, object]:
    faults, services = _recovery_observe(session)
    guidance = [item["guidance"] for item in faults if item.get("guidance")]
    if not recovery:
        return {
            "active": False,
            "session": None,
            "faults": faults,
            "services": services,
            "guidance": guidance,
        }
    if recovery.status == "handoff" and not guidance:
        guidance.append(_recovery_instruction(
            "current_recovery_condition",
            "Complete the reported controller or physical check",
            recovery.message or "Correct the displayed condition, then ask recovery to recheck every service.",
        ))
    return {
        "active": recovery.status in _RECOVERY_ACTIVE_STATUSES,
        "session": {
            "id": recovery.id,
            "status": recovery.status,
            "step": recovery.step,
            "answers": _recovery_json(recovery.answers_json, {}),
            "faults": _recovery_json(recovery.faults_json, []),
            "actions": _recovery_json(recovery.actions_json, []),
            "message": recovery.message,
            "created_at": recovery.created_at,
            "updated_at": recovery.updated_at,
        },
        "faults": faults,
        "services": services,
        "guidance": guidance,
    }


def recovery_status(session: Session) -> dict[str, object]:
    recovery = session.scalar(
        select(RecoverySession)
        .where(RecoverySession.status.in_(_RECOVERY_ACTIVE_STATUSES))
        .order_by(RecoverySession.updated_at.desc())
    )
    if not recovery:
        # Preserve the most recent success for the Debug page after a refresh.
        # A new recovery still creates a fresh active session, so this history
        # item can never block or alter the next recovery attempt.
        recovery = session.scalar(
            select(RecoverySession)
            .where(RecoverySession.status == "completed")
            .order_by(RecoverySession.updated_at.desc())
        )
    return _recovery_snapshot(session, recovery)


def start_system_recovery(session: Session) -> dict[str, object]:
    existing = session.scalar(
        select(RecoverySession)
        .where(RecoverySession.status.in_(_RECOVERY_ACTIVE_STATUSES))
        .order_by(RecoverySession.updated_at.desc())
    )
    if existing:
        return _recovery_snapshot(session, existing)
    now = datetime.now(timezone.utc).isoformat()
    faults, _services = _recovery_observe(session)
    recovery = RecoverySession(
        id=str(uuid4()),
        status="awaiting_safety",
        step="safety",
        faults_json=json.dumps(faults, separators=(",", ":")),
        message="Inspect the cell. Confirm that no person is exposed to motion and no robot or mill movement is active.",
        created_at=now,
        updated_at=now,
    )
    session.add(recovery)
    session.commit()
    diagnostics().record(
        "recovery",
        "started",
        "Guided system recovery started.",
        severity="warning",
        details={"session_id": recovery.id, "faults": faults},
    )
    refresh_stack_light(session)
    return _recovery_snapshot(session, recovery)


def _save_recovery(
    session: Session,
    recovery: RecoverySession,
    *,
    status: str | None = None,
    step: str | None = None,
    message: str | None = None,
    answers: dict[str, bool] | None = None,
    actions: list[dict[str, object]] | None = None,
) -> None:
    if status is not None:
        recovery.status = status
    if step is not None:
        recovery.step = step
    if message is not None:
        recovery.message = message[:1000]
    if answers is not None:
        recovery.answers_json = json.dumps(answers, separators=(",", ":"))
    if actions is not None:
        recovery.actions_json = json.dumps(actions, separators=(",", ":"))
    recovery.updated_at = datetime.now(timezone.utc).isoformat()
    session.commit()
    # Recovery owns the white indication only while its persisted state is
    # active. Reapply all outputs after every transition so completion or
    # cancellation cannot leave white latched on the controller.
    refresh_stack_light(session)


def _supervisor_operation_completed(operation: str, resolution: str) -> bool:
    return {
        "pick_pool": resolution in {"robot_held", "machine", "destination_pool"},
        "put_pool": resolution == "destination_pool",
        "load_mill": resolution == "machine",
        "unload_mill": resolution in {"robot_held", "destination_pool"},
    }.get(operation, False)


def _reconcile_recovery_motion(
    session: Session,
    resolution: str,
) -> list[dict[str, object]]:
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status == "faulted"))
    if not motion:
        raise problem(409, "No faulted pallet movement is available for physical reconciliation.")
    supervisor_before = robot_supervisor_status(session)
    reconciliation = supervisor_before.get("reconciliation") or {}
    settings = get_settings(session)
    recover_pallet_motion(
        session,
        motion.id,
        RecoverPalletMotion(expected_revision=settings.revision, resolution=resolution),
    )
    actions: list[dict[str, object]] = [{
        "controller": "Pallet ledger",
        "action": "Reconciled",
        "detail": f"Recorded the observed physical result as {resolution.replace('_', ' ')}.",
    }]
    if reconciliation.get("motion_id") != motion.id or not reconciliation.get("sequence"):
        return actions

    command_resolution = (
        "accept_completed"
        if _supervisor_operation_completed(str(reconciliation.get("operation") or ""), resolution)
        else "mark_faulted"
    )
    settings = get_settings(session)
    reconcile_robot_supervisor(
        session,
        SupervisorReconcile(
            expected_revision=settings.revision,
            sequence=int(reconciliation["sequence"]),
            resolution=command_resolution,
        ),
    )
    actions.append({
        "controller": "Mongo supervisor",
        "action": "Command reconciled",
        "detail": "Recorded the uncertain robot command as physically completed." if command_resolution == "accept_completed" else "Recorded the uncertain robot command as not physically completed.",
    })
    supervisor_after = robot_supervisor_status(session)
    if supervisor_after.get("latched"):
        settings = get_settings(session)
        reconcile_robot_supervisor(
            session,
            SupervisorReconcile(
                expected_revision=settings.revision,
                sequence=int(reconciliation["sequence"]),
                resolution="clear_latch",
            ),
        )
        actions.append({
            "controller": "Mongo supervisor",
            "action": "Latch cleared",
            "detail": "Cleared the reconciled supervisor latch without moving the robot.",
        })
    return actions


def _reconcile_recovery_robot_command(
    session: Session,
    resolution: str,
) -> list[dict[str, object]]:
    if resolution not in {"accept_completed", "mark_faulted"}:
        raise problem(422, "Choose whether the observed robot command completed or did not complete.")
    status = robot_supervisor_status(session)
    reconciliation = status.get("reconciliation") or {}
    sequence = reconciliation.get("sequence")
    if not sequence:
        raise problem(409, "The robot has no uncertain command that can be safely reconciled automatically.")
    settings = get_settings(session)
    reconcile_robot_supervisor(
        session,
        SupervisorReconcile(
            expected_revision=settings.revision,
            sequence=int(sequence),
            resolution=resolution,
        ),
    )
    actions: list[dict[str, object]] = [{
        "controller": "Mongo supervisor",
        "action": "Command reconciled",
        "detail": "Recorded the operator-observed result without resending the robot command.",
    }]
    refreshed = robot_supervisor_status(session)
    if refreshed.get("latched"):
        settings = get_settings(session)
        reconcile_robot_supervisor(
            session,
            SupervisorReconcile(
                expected_revision=settings.revision,
                sequence=int(sequence),
                resolution="clear_latch",
            ),
        )
        actions.append({
            "controller": "Mongo supervisor",
            "action": "Latch cleared",
            "detail": "Cleared the reconciled application latch without moving the robot.",
        })
    return actions


def _reconcile_recovery_mill_command(
    session: Session,
    resolution: str,
) -> list[dict[str, object]]:
    if resolution not in {"accept_completed", "mark_faulted"}:
        raise problem(422, "Choose whether the observed mill command completed or did not complete.")
    status = mill_supervisor_status(session)
    reconciliation = status.get("reconciliation") or {}
    sequence = reconciliation.get("sequence")
    if not sequence:
        raise problem(409, "The mill has no uncertain command that can be safely reconciled automatically.")
    settings = get_settings(session)
    reconcile_mill_supervisor_command(
        session,
        MillSupervisorReconcile(
            expected_revision=settings.revision,
            sequence=int(sequence),
            resolution=resolution,
        ),
    )
    return [{
        "controller": "Mill supervisor",
        "action": "Command reconciled",
        "detail": "Recorded the operator-observed PathPilot result without rerunning the command.",
    }]


def _rebuild_generated_programs_for_recovery(
    session: Session,
    actions: list[dict[str, object]],
    progress,
) -> None:
    """Deploy only MPS-generated controller programs after recovery is safely idle."""
    settings = get_settings(session)
    if settings.robot_connection_mode == "physical" and settings.robot_host.strip():
        progress("Rebuilding generated Mongo pallet programs.", [])
        rebuilt = rebuild_pallet_motion_scripts(session)
        actions.append({
            "controller": "Mongo",
            "action": "Programs rebuilt",
            "detail": f"Deployed {len(rebuilt['files'])} generated pallet and supervisor programs.",
        })
        progress("Restarting Mongo with the rebuilt supervisor program.", [])
        supervisor = bootstrap_robot_supervisor(session)
        if not supervisor.get("connected"):
            raise problem(504, "Mongo did not reconnect after its rebuilt supervisor program was started.")
        actions.append({
            "controller": "Mongo",
            "action": "Supervisor restarted",
            "detail": "Restarted the no-motion supervisor so it uses the rebuilt program.",
        })
    else:
        actions.append({
            "controller": "Mongo",
            "action": "Programs skipped",
            "detail": "Mongo is not configured for physical control; no robot program was deployed.",
        })

    settings = get_settings(session)
    if settings.cnc_host.strip():
        progress("Rebuilding the generated PathPilot loading program.", [])
        rebuilt_mill = rebuild_mill_load_position_program(session, settings.revision)
        actions.append({
            "controller": "Mill",
            "action": "Program rebuilt",
            "detail": f"Uploaded {rebuilt_mill['filename']} to PathPilot.",
        })
    else:
        actions.append({
            "controller": "Mill",
            "action": "Program skipped",
            "detail": "PathPilot is not configured; no mill program was deployed.",
        })


def answer_system_recovery(session: Session, payload: RecoveryAnswer) -> dict[str, object]:
    recovery = session.get(RecoverySession, payload.session_id)
    if not recovery or recovery.status not in _RECOVERY_ACTIVE_STATUSES:
        raise problem(409, "That recovery session is no longer active. Refresh the Dashboard.")
    answers = _recovery_json(recovery.answers_json, {})
    answers.update({key: bool(value) for key, value in payload.answers.items()})

    if recovery.status == "awaiting_safety":
        if not answers.get("cell_clear"):
            raise problem(422, "Confirm that the cell is clear before recovery can touch controller connections.")
        faults, _services = _recovery_observe(session)
        if any(item["severity"] == "handoff" for item in faults):
            _save_recovery(
                session,
                recovery,
                status="handoff",
                step="physical_intervention",
                message="Physical inspection or pallet-location reconciliation is required before software recovery can continue.",
                answers=answers,
            )
            return _recovery_snapshot(session, recovery)
        _save_recovery(
            session,
            recovery,
            status="running",
            step="services",
            message="Resetting local connections and restarting enabled supervisors.",
            answers=answers,
        )

    elif recovery.status == "handoff":
        faults, _services = _recovery_observe(session)
        choices = dict(payload.choices)
        if payload.resolution and "robot_motion" not in choices:
            choices["robot_motion"] = payload.resolution
        recovery_actions = _recovery_json(recovery.actions_json, [])
        handled = False

        run_active = next((item for item in faults if item.get("code") == "run_mode_active"), None)
        if run_active:
            if choices.get("run_mode") != "request_stop":
                _save_recovery(session, recovery, message="Choose the guided safe-stop option before recovery continues.", answers=answers)
                return _recovery_snapshot(session, recovery)
            settings = get_settings(session)
            stop_run_mode(session, settings.revision)
            recovery_actions.append({
                "controller": "Run Mode",
                "action": "Safe stop requested",
                "detail": "No subsequent automated step may start; an operation already in progress was not aborted.",
            })
            handled = True
            if not session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running")))) and not session.scalar(select(Pallet).where(Pallet.location == "machine")):
                settings = get_settings(session)
                cancel_run_mode_recovery(session, settings.revision)
            faults, _services = _recovery_observe(session)

        run_fault = next((item for item in faults if item.get("code") == "run_mode_fault" and item.get("severity") == "handoff"), None)
        if run_fault and choices.get("run_mode_fault") == "stop_keep_machine":
            settings = get_settings(session)
            cancel_run_mode_recovery(session, settings.revision)
            recovery_actions.append({
                "controller": "Run Mode",
                "action": "Scheduler stopped",
                "detail": "Stopped automated scheduling while preserving the pallet's recorded mill location.",
            })
            handled = True
            faults, _services = _recovery_observe(session)

        motion_fault = next((item for item in faults if item.get("code") == "active_or_faulted_motion"), None)
        if motion_fault and motion_fault.get("options"):
            motion_resolution = choices.get("robot_motion")
            if not motion_resolution:
                _save_recovery(
                    session,
                    recovery,
                    message="Select the pallet's observed physical location before recovery can clear the uncertain movement record.",
                    answers=answers,
                    actions=recovery_actions,
                )
                return _recovery_snapshot(session, recovery)
            recovery_actions.extend(_reconcile_recovery_motion(session, motion_resolution))
            handled = True
            faults, _services = _recovery_observe(session)

        robot_fault = next((item for item in faults if item.get("code") == "robot_reconciliation_required"), None)
        if robot_fault and (robot_fault.get("guidance") or {}).get("type") == "choice":
            robot_resolution = choices.get("robot_command")
            if not robot_resolution:
                _save_recovery(session, recovery, message="Check the last robot action and record whether it finished.", answers=answers, actions=recovery_actions)
                return _recovery_snapshot(session, recovery)
            recovery_actions.extend(_reconcile_recovery_robot_command(session, robot_resolution))
            handled = True
            faults, _services = _recovery_observe(session)

        mill_fault = next((item for item in faults if item.get("code") == "mill_reconciliation_required"), None)
        if mill_fault and (mill_fault.get("guidance") or {}).get("type") == "choice":
            mill_resolution = choices.get("mill_command")
            if not mill_resolution:
                _save_recovery(session, recovery, message="Check the last mill action and record whether it finished.", answers=answers, actions=recovery_actions)
                return _recovery_snapshot(session, recovery)
            recovery_actions.extend(_reconcile_recovery_mill_command(session, mill_resolution))
            handled = True
            faults, _services = _recovery_observe(session)

        if handled:
            _save_recovery(
                session,
                recovery,
                message="Guided recovery choice recorded. Rechecking every controller and service.",
                answers=answers,
                actions=recovery_actions,
            )
        elif not answers.get("retry"):
            raise problem(422, "Complete the displayed physical checks and acknowledge retry before recovery continues.")
        if any(item["severity"] == "handoff" for item in faults):
            recovery.answers_json = json.dumps(answers, separators=(",", ":"))
            recovery.actions_json = json.dumps(recovery_actions, separators=(",", ":"))
            recovery.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return _recovery_snapshot(session, recovery)
        _save_recovery(session, recovery, status="running", step="services", message="Retrying software recovery.", answers=answers)

    elif recovery.status == "ready":
        if not answers.get("final_approval"):
            raise problem(422, "Final approval is required before marking the system ready.")
        _save_recovery(
            session,
            recovery,
            status="completed",
            step="complete",
            message="Recovery completed. The system is ready for a separate operator-controlled production start.",
            answers=answers,
        )
        diagnostics().record("recovery", "completed", recovery.message, details={"session_id": recovery.id})
        return _recovery_snapshot(session, recovery)

    actions = _recovery_json(recovery.actions_json, [])
    try:
        faults, _services = _recovery_observe(session)
        if any(item["severity"] == "handoff" for item in faults):
            _save_recovery(session, recovery, status="handoff", step="physical_intervention", message="A physical operation became active while recovery was running.", answers=answers, actions=actions)
            return _recovery_snapshot(session, recovery)

        settings = get_settings(session)
        if settings.robot_connection_mode == "physical":
            reset_robot_connections()
            actions.append({"controller": "Robot", "action": "Transport reset", "detail": "Cleared local robot connection caches and reconnect cooldowns."})

        def recovery_progress(message: str, service_results: list[dict[str, object]]) -> None:
            _save_recovery(
                session,
                recovery,
                status="running",
                step="services",
                message=message,
                answers=answers,
                actions=actions + list(service_results),
            )

        controller_result = auto_recover_controller_connections(session, progress=recovery_progress)
        current_results = controller_result.get("results", [])
        actions.extend(current_results)
        if controller_result.get("reconciliation"):
            _save_recovery(
                session,
                recovery,
                status="handoff",
                step="physical_intervention",
                message="The robot reported an uncertain command result. Reconcile the physical pallet location before continuing.",
                answers=answers,
                actions=actions,
            )
            return _recovery_snapshot(session, recovery)

        settings = get_settings(session)
        if settings.robot_connection_mode == "physical" and settings.robot_host.strip():
            result = clear_robot_controller_fault(
                session,
                ClearRobotFault(expected_revision=settings.revision, confirmed=True),
            )
            actions.append({"controller": "Robot", "action": result.get("action", "Fault check"), "detail": result.get("message", "Robot fault check completed.")})

        blocking_actions = [item for item in current_results if item.get("action") in {"Deferred", "Dashboard unavailable", "PathPilot unavailable", "Recovery pending"}]
        faults, _services = _recovery_observe(session)
        blocking_faults = [item for item in faults if item["severity"] == "handoff"]
        if blocking_actions or blocking_faults:
            _save_recovery(
                session,
                recovery,
                status="handoff",
                step="physical_intervention" if blocking_faults else "services",
                message="Recovery could not establish every required service. Review the action results and correct the reported condition before retrying.",
                answers=answers,
                actions=actions,
            )
            return _recovery_snapshot(session, recovery)

        _rebuild_generated_programs_for_recovery(session, actions, recovery_progress)

        _save_recovery(
            session,
            recovery,
            status="ready",
            step="final_approval",
            message="Software recovery completed. Review the service results, then approve the final ready state.",
            answers=answers,
            actions=actions,
        )
        diagnostics().record("recovery", "ready", recovery.message, details={"session_id": recovery.id, "actions": actions})
        return _recovery_snapshot(session, recovery)
    except (HTTPException, RobotTelemetryError, RobotDashboardError, CncTelemetryError, RobotFileAccessError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        actions.append({"controller": "Recovery", "action": "Handoff required", "detail": detail})
        _save_recovery(session, recovery, status="handoff", step="physical_intervention", message=detail, answers=answers, actions=actions)
        return _recovery_snapshot(session, recovery)


def cancel_system_recovery(session: Session, session_id: str) -> dict[str, object]:
    recovery = session.get(RecoverySession, session_id)
    if not recovery or recovery.status not in _RECOVERY_ACTIVE_STATUSES:
        raise problem(409, "That recovery session is no longer active.")
    _save_recovery(session, recovery, status="cancelled", step="cancelled", message="Operator cancelled guided system recovery.")
    diagnostics().record("recovery", "cancelled", recovery.message, severity="warning", details={"session_id": recovery.id})
    return _recovery_snapshot(session, recovery)


def start_robot_supervisor_recovery_watchdog(session_factory) -> None:
    """Heal an idle stale socket without ever touching controller motion."""
    global _SUPERVISOR_RECOVERY_THREAD
    if _SUPERVISOR_RECOVERY_THREAD and _SUPERVISOR_RECOVERY_THREAD.is_alive():
        return
    _SUPERVISOR_RECOVERY_STOP.clear()

    def watch() -> None:
        last_listener_recovery = 0.0
        last_program_recovery = 0.0
        listener_recovery_count = 0
        while not _SUPERVISOR_RECOVERY_STOP.wait(5.0):
            try:
                with session_factory() as session:
                    status = robot_supervisor().status()
                    settings = get_settings(session)
                    listener_age = float(status.get("listener_age_seconds") or 0)
                    if status.get("connected"):
                        if _SUPERVISOR_BACKGROUND_RECOVERY.is_set():
                            _SUPERVISOR_BACKGROUND_RECOVERY.clear()
                            refresh_stack_light(session)
                        listener_recovery_count = 0
                        continue
                    if not _SUPERVISOR_BACKGROUND_RECOVERY.is_set():
                        _SUPERVISOR_BACKGROUND_RECOVERY.set()
                        refresh_stack_light(session)
                    if pause_run_mode_for_manual_robot_control(session):
                        # The mill owns its active cycle. Do not restart Mongo
                        # while an operator is teaching it by hand.
                        listener_recovery_count = 0
                        continue
                    now = time.monotonic()
                    if not status.get("listening") or listener_age < _SUPERVISOR_RECOVERY_GRACE_SECONDS:
                        continue
                    # A listener reset cannot revive a supervisor program that
                    # PolyScope stopped for manual jogging. After one bounded
                    # listener retry, restart only when Dashboard proves that
                    # no operator program is selected (or Mongo's own stopped
                    # supervisor remains selected).
                    if (
                        listener_recovery_count >= 1
                        and now - last_program_recovery >= _SUPERVISOR_PROGRAM_RECOVERY_COOLDOWN_SECONDS
                    ):
                        try:
                            recover_robot_supervisor_program(session, trigger="watchdog")
                            last_program_recovery = now
                            listener_recovery_count = 0
                        except HTTPException as exc:
                            diagnostics().record(
                                "robot_supervisor",
                                "program_recovery_deferred",
                                "Automatic supervisor recovery was deferred to avoid replacing controller work.",
                                severity="warning",
                                details={"status": exc.status_code, "detail": str(exc.detail)},
                            )
                            last_program_recovery = now
                        continue
                    if now - last_listener_recovery >= _SUPERVISOR_RECOVERY_COOLDOWN_SECONDS:
                        if recover_robot_supervisor_connection(session, trigger="watchdog"):
                            last_listener_recovery = now
                            listener_recovery_count += 1
            except Exception as exc:  # A diagnostic helper must never affect production control.
                diagnostics().record(
                    "robot_supervisor", "listener_recovery_error", "Idle supervisor recovery check failed.",
                    severity="warning", details={"error": str(exc)},
                )

    _SUPERVISOR_RECOVERY_THREAD = Thread(target=watch, daemon=True, name="robot-supervisor-recovery")
    _SUPERVISOR_RECOVERY_THREAD.start()


def stop_robot_supervisor_recovery_watchdog() -> None:
    _SUPERVISOR_RECOVERY_STOP.set()
    _SUPERVISOR_BACKGROUND_RECOVERY.clear()


def robot_supervisor_status(session: Session) -> dict[str, object]:
    settings = get_settings(session)
    status = robot_supervisor().status()
    # A backend restart can occur after Mongo physically completed a command.
    # Apply that matching terminal result once, but never cross robot sessions.
    if (
        status.get("connected")
        and status.get("robot_last_sequence") == settings.robot_supervisor_last_sequence
        and status.get("robot_last_event") == "completed"
    ):
        last_command = session.scalar(
            select(RobotSupervisorCommand).where(
                RobotSupervisorCommand.sequence == settings.robot_supervisor_last_sequence
            )
        )
        if (
            last_command
            and last_command.robot_session == status.get("robot_session")
            and last_command.status in {"sent", "accepted", "running", "uncertain"}
        ):
            last_command.status = "completed"
            last_command.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            motion = session.get(RobotMotion, last_command.robot_motion_id) if last_command.robot_motion_id else None
            if motion and motion.status == "faulted" and "Backend restarted" in (motion.failure_detail or ""):
                commands = session.scalars(
                    select(RobotSupervisorCommand)
                    .where(RobotSupervisorCommand.robot_motion_id == motion.id)
                    .order_by(RobotSupervisorCommand.sequence)
                ).all()
                final_operations = {
                    "pick": "pick_pool",
                    "put": "put_pool",
                    "load_mill": "load_mill",
                    "unload_mill": "put_pool",
                }
                if (
                    commands
                    and all(item.status == "completed" for item in commands)
                    and commands[-1].operation == final_operations.get(motion.operation)
                ):
                    _finish_motion(session, motion, True)
                    settings = get_settings(session)
                    status = robot_supervisor().status()
    recent = session.scalars(
        select(RobotSupervisorCommand)
        .order_by(RobotSupervisorCommand.sequence.desc())
        .limit(20)
    ).all()
    robot_sequence = status.get("robot_last_sequence")
    expected_sequence = settings.robot_supervisor_last_sequence
    latest_expected = next((item for item in recent if item.sequence == expected_sequence), None)
    terminal_uncertain = bool(
        latest_expected
        and latest_expected.status in {"uncertain", "latched", "faulted", "dispatching", "sent", "accepted", "running"}
    )
    reconciliation = None
    if terminal_uncertain and latest_expected:
        motion = session.get(RobotMotion, latest_expected.robot_motion_id) if latest_expected.robot_motion_id else None
        pallet = session.get(Pallet, motion.pallet_id) if motion else None
        reconciliation = {
            "sequence": latest_expected.sequence,
            "operation": latest_expected.operation,
            "command_status": latest_expected.status,
            "motion_id": latest_expected.robot_motion_id,
            "pallet_name": pallet.name if pallet else None,
            "board_location": pallet.location if pallet else None,
            "detail": latest_expected.fault_detail,
        }
    session_mismatch = bool(
        status.get("connected")
        and expected_sequence > 0
        and latest_expected
        # A prior controller session is safe to accept once its final command
        # has already been reconciled. Only an unresolved command can make a
        # new robot session physically ambiguous.
        and terminal_uncertain
        and latest_expected.robot_session is not None
        and latest_expected.robot_session != status.get("robot_session")
    )
    active_motion = session.scalar(select(RobotMotion.id).where(RobotMotion.status.in_(("requested", "running"))))
    reconciliation_required = bool(
        status.get("latched")
        or session_mismatch
        or (status.get("connected") and robot_sequence is not None and robot_sequence != expected_sequence)
        or terminal_uncertain
    )
    status.update({
        "enabled": settings.robot_supervisor_enabled,
        "activation_verified": settings.robot_supervisor_activation_verified,
        "maintenance_mode": settings.robot_supervisor_maintenance_mode,
        "expected_sequence": expected_sequence,
        "reconciliation_required": reconciliation_required,
        "manual_reconnect_available": bool(
            not status.get("connected")
            and settings.robot_supervisor_enabled
            and settings.robot_supervisor_activation_verified
            and not reconciliation_required
            and active_motion is None
            and (not settings.run_mode_enabled or _run_mode_allows_robot_reconnect(settings))
        ),
        "session_mismatch": session_mismatch,
        "pre_dispatch_fallback": settings.robot_supervisor_pre_dispatch_fallback,
        "reconciliation": reconciliation,
        "commands": [
            {
                "sequence": item.sequence,
                "robot_session": item.robot_session,
                "robot_motion_id": item.robot_motion_id,
                "operation": item.operation,
                "transport": item.transport,
                "status": item.status,
                "attempted": item.attempted,
                "created_at": item.created_at,
                "sent_at": item.sent_at,
                "accepted_at": item.accepted_at,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
                "result_code": item.result_code,
                "fault_detail": item.fault_detail,
            }
            for item in recent
        ],
    })
    return status


def robot_supervisor_status_document() -> dict[str, object]:
    """Return the durable robot-originated status checkpoint without controller I/O."""
    return robot_supervisor().status_document()


def _repairable_supervisor_maintenance_gap(
    session: Session,
    settings: AppSettings,
    status: dict[str, object],
) -> RobotSupervisorCommand | None:
    """Return the sole no-motion command that may safely close a one-step gap.

    An E-stop can interrupt the supervisor's own maintenance/restart command
    after the backend reserved its sequence but before the robot recorded it.
    Replaying pallet or alignment commands is never allowed here.
    """
    if not status.get("connected") or status.get("latched"):
        return None
    telemetry = status.get("telemetry") or {}
    if telemetry.get("safety_mode") != 1 or telemetry.get("runtime_state") != 1:
        return None
    try:
        robot_sequence = int(status.get("robot_last_sequence"))
    except (TypeError, ValueError):
        return None
    expected_sequence = int(settings.robot_supervisor_last_sequence)
    if expected_sequence != robot_sequence + 1:
        return None
    command = session.scalar(
        select(RobotSupervisorCommand).where(RobotSupervisorCommand.sequence == expected_sequence)
    )
    if not command:
        return None
    if (
        command.robot_motion_id is not None
        or command.operation != "bootstrap_restart"
        or command.opcode != OP_ENTER_MAINTENANCE
        or command.status not in {"completed", "operator_completed", "operator_faulted"}
    ):
        return None
    return command


def bootstrap_robot_supervisor(
    session: Session,
    *,
    allow_run_mode_manual_pause: bool = False,
    allow_run_mode_paused: bool = False,
) -> dict[str, object]:
    settings = get_settings(session)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Configure a physical Mongo controller before bootstrapping its supervisor.")
    active_motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if active_motion or (
        settings.run_mode_enabled
        and not (
            (allow_run_mode_manual_pause and settings.run_mode_manual_robot_pause)
            or (allow_run_mode_paused and _run_mode_allows_robot_reconnect(settings))
        )
    ):
        raise problem(409, "Stop Run Mode and resolve all pallet movements before bootstrapping the supervisor.")
    restore_enabled_after_success = settings.robot_supervisor_enabled
    settings.robot_supervisor_enabled = False
    settings.robot_supervisor_activation_verified = False
    bump(settings)
    commit_or_conflict(session)
    local_script = generated_script_directory(Path(__file__).parents[1]) / "mongo_supervisor.script"
    if not local_script.is_file():
        raise problem(409, "Rebuild generated scripts before bootstrapping the supervisor.")
    start_robot_supervisor_listener(session)
    listener = robot_supervisor().status()
    if not listener.get("listening"):
        raise problem(409, str(listener.get("last_disconnect_detail") or "Supervisor listener did not start."))
    previous_generation = int(listener.get("connection_generation") or 0)
    if listener.get("connected"):
        maintenance = None
        if listener.get("robot_last_sequence") != settings.robot_supervisor_last_sequence:
            maintenance = _repairable_supervisor_maintenance_gap(session, settings, listener)
            if maintenance is None:
                raise problem(409, "The connected supervisor sequence does not match the backend. Reconcile it before restarting the supervisor.")
            # This command cannot move the robot. Bind its retry to the current
            # live supervisor session and preserve the original sequence.
            maintenance.robot_session = listener.get("robot_session")
            maintenance.app_session = listener.get("app_session")
            diagnostics().record(
                "robot_supervisor",
                "maintenance_sequence_repair",
                "Replaying an interrupted no-motion supervisor maintenance command after E-stop recovery.",
                severity="warning",
                details={"sequence": maintenance.sequence, "robot_sequence": listener.get("robot_last_sequence")},
            )
        else:
            maintenance = _new_supervisor_command(
                session,
                motion=None,
                operation="bootstrap_restart",
                opcode=OP_ENTER_MAINTENANCE,
            )
        outcome, detail = _dispatch_supervisor_command(
            session,
            maintenance,
            max(5.0, settings.robot_timeout_seconds * 4),
            allow_pre_dispatch_fallback=False,
        )
        if outcome != "completed":
            raise problem(409, f"The existing supervisor did not stop cleanly: {detail}")
        settings = get_settings(session)
    script_content = with_supervisor_sequence(
        local_script.read_text(encoding="utf-8"),
        settings.robot_supervisor_last_sequence,
    )
    try:
        run_robot_script(
            settings.robot_host.strip(),
            script_content,
            settings.robot_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, f"Supervisor bootstrap could not reach Mongo: {exc}") from exc
    wait_seconds = max(10.0, settings.robot_timeout_seconds * 4)
    connected = robot_supervisor().wait_for_connection_generation(previous_generation, wait_seconds)
    if not connected:
        raise problem(
            504,
            "URControl accepted the no-motion script transfer, but Mongo did not connect to the supervisor listener. "
            "Verify DNS and Windows Firewall, then inspect the controller log for an unsupported URScript command. "
            "PolyScope 3.2.20175 may require its final 3.2 maintenance update before external supervisor programs run reliably.",
        )
    status = robot_supervisor().status()
    if status.get("robot_last_sequence") != settings.robot_supervisor_last_sequence:
        settings.robot_supervisor_activation_verified = False
        bump(settings)
        commit_or_conflict(session)
        raise problem(
            409,
            f"Supervisor connected, but Mongo reports sequence {status.get('robot_last_sequence')} while the backend expects {settings.robot_supervisor_last_sequence}. Reconcile before enabling it.",
        )
    settings.robot_supervisor_activation_verified = True
    settings.robot_supervisor_enabled = restore_enabled_after_success
    settings.robot_supervisor_maintenance_mode = False
    bump(settings)
    commit_or_conflict(session)
    return robot_supervisor_status(session)


def reconcile_robot_supervisor(session: Session, payload) -> dict[str, object]:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled or session.scalar(
        select(RobotMotion).where(RobotMotion.status.in_(("requested", "running")))
    ):
        raise problem(409, "Stop Run Mode and wait for active motion before reconciling the supervisor.")
    command = session.scalar(
        select(RobotSupervisorCommand).where(RobotSupervisorCommand.sequence == payload.sequence)
    )
    if not command:
        raise problem(404, "Supervisor command sequence was not found in the durable ledger.")
    if payload.resolution == "accept_completed":
        if command.status not in {"uncertain", "latched", "faulted", "dispatching", "sent", "accepted", "running"}:
            raise problem(409, f"Supervisor sequence {command.sequence} is already reconciled.")
        command.status = "operator_completed"
        command.completed_at = command.completed_at or datetime.now(timezone.utc).isoformat()
        command.fault_detail = "Operator confirmed the physical atomic operation completed. Reconcile the pallet-motion record separately if its board location is faulted."
    elif payload.resolution == "mark_faulted":
        if command.status not in {"uncertain", "latched", "faulted", "dispatching", "sent", "accepted", "running"}:
            raise problem(409, f"Supervisor sequence {command.sequence} is already reconciled.")
        command.status = "operator_faulted"
        command.completed_at = command.completed_at or datetime.now(timezone.utc).isoformat()
        command.fault_detail = "Operator confirmed the atomic operation did not complete."
    else:
        if command.sequence != settings.robot_supervisor_last_sequence:
            raise problem(409, "Only the latest supervisor command may be cleared.")
        if command.status not in {"operator_completed", "operator_faulted", "completed"}:
            raise problem(
                409,
                "Confirm whether the physical operation completed or failed before clearing the supervisor latch.",
            )
        if not robot_supervisor().status().get("connected"):
            raise problem(409, "Mongo must be connected before its supervisor latch can be cleared.")
        clear = _new_supervisor_command(
            session,
            motion=None,
            operation="clear_latch",
            opcode=OP_CLEAR_LATCH,
        )
        outcome, detail = _dispatch_supervisor_command(
            session,
            clear,
            max(5.0, settings.robot_timeout_seconds * 4),
            allow_pre_dispatch_fallback=False,
        )
        if outcome != "completed":
            raise problem(409, detail)
        settings = get_settings(session)
        settings.robot_supervisor_activation_verified = True
    bump(settings)
    commit_or_conflict(session)
    return robot_supervisor_status(session)


def set_robot_supervisor_maintenance(session: Session, payload) -> dict[str, object]:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled or _locked_motion(session):
        raise problem(409, "Stop Run Mode and resolve all pallet movements before changing Maintenance Mode.")
    if payload.enabled:
        if robot_supervisor().status().get("connected"):
            command = _new_supervisor_command(
                session,
                motion=None,
                operation="enter_maintenance",
                opcode=OP_ENTER_MAINTENANCE,
            )
            outcome, detail = _dispatch_supervisor_command(
                session,
                command,
                max(5.0, settings.robot_timeout_seconds * 4),
                allow_pre_dispatch_fallback=False,
            )
            if outcome != "completed":
                raise problem(409, detail)
        settings = get_settings(session)
        settings.robot_supervisor_maintenance_mode = True
    else:
        local_script = generated_script_directory(Path(__file__).parents[1]) / "mongo_supervisor.script"
        if not local_script.is_file():
            raise problem(409, "Rebuild generated scripts before leaving Maintenance Mode.")
        run_robot_script(
            settings.robot_host.strip(),
            with_supervisor_sequence(
                local_script.read_text(encoding="utf-8"),
                settings.robot_supervisor_last_sequence,
            ),
            settings.robot_timeout_seconds,
        )
        if not robot_supervisor().wait_until_connected(max(10.0, settings.robot_timeout_seconds * 4)):
            raise problem(504, "Mongo did not reconnect after Maintenance Mode.")
        settings = get_settings(session)
        settings.robot_supervisor_maintenance_mode = False
        settings.robot_supervisor_activation_verified = True
    bump(settings)
    commit_or_conflict(session)
    return robot_supervisor_status(session)


def _new_supervisor_command(
    session: Session,
    *,
    motion: RobotMotion | None,
    operation: str,
    opcode: int,
    argument: int = 0,
    value: int = 0,
    payload_g: int = 0,
) -> RobotSupervisorCommand:
    motion_id = motion.id if motion else None
    for attempt in range(5):
        settings = get_settings(session)
        if settings.robot_supervisor_last_sequence >= 2_000_000_000:
            raise problem(409, "Mongo supervisor sequence space is exhausted. Rebuild and re-bootstrap a new supervisor session before sending commands.")
        settings.robot_supervisor_last_sequence += 1
        status = robot_supervisor().status()
        command = RobotSupervisorCommand(
            id=str(uuid4()),
            sequence=settings.robot_supervisor_last_sequence,
            robot_session=status.get("robot_session"),
            app_session=status.get("app_session"),
            robot_motion_id=motion_id,
            operation=operation,
            opcode=opcode,
            argument=argument,
            value=value,
            payload_g=payload_g,
            transport="supervisor",
            status="created",
            attempted=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(command)
        bump(settings)
        try:
            commit_or_conflict(session)
            return command
        except HTTPException as exc:
            if exc.status_code != 409 or attempt == 4:
                raise
            # Queue and pallet edits are allowed while a robot step is running.
            # Refresh the global revision before reserving the next sequence;
            # the command has not been sent, so retrying cannot duplicate motion.
            session.expire_all()
            diagnostics().record(
                "robot_supervisor",
                "command_ledger_commit_retry",
                "Retrying unsent supervisor command reservation after a concurrent schedule update.",
                severity="warning",
                details={"operation": operation, "motion_id": motion_id, "attempt": attempt + 1},
            )
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("Supervisor command reservation retry loop exited unexpectedly.")


def _dispatch_supervisor_command(
    session: Session,
    command: RobotSupervisorCommand,
    timeout_seconds: float,
    *,
    allow_pre_dispatch_fallback: bool,
) -> tuple[str, str]:
    receipt = robot_supervisor().dispatch(
        command.sequence,
        command.opcode,
        command.argument,
        command.value,
        command.payload_g,
        expected_robot_session=command.robot_session,
    )
    command.attempted = receipt.attempted
    if not receipt.attempted:
        # No socket existed and no byte could have reached Mongo. Release this
        # reserved sequence so the robot never observes an artificial gap.
        settings = get_settings(session)
        if settings.robot_supervisor_last_sequence == command.sequence:
            settings.robot_supervisor_last_sequence -= 1
            bump(settings)
        session.delete(command)
        session.commit()
        if allow_pre_dispatch_fallback:
            return "fallback", receipt.detail
        return "faulted", receipt.detail
    if not receipt.sent:
        command.status = "uncertain"
        command.sent_at = datetime.now(timezone.utc).isoformat()
        command.fault_detail = receipt.detail
        session.commit()
        return "faulted", receipt.detail

    command.status = "sent"
    command.sent_at = datetime.now(timezone.utc).isoformat()
    session.commit()
    event = robot_supervisor().wait_for_event(
        command.sequence,
        timeout_seconds,
        expected_robot_session=command.robot_session,
    )
    history = robot_supervisor().events_for(command.sequence)
    command = session.get(RobotSupervisorCommand, command.id) or command
    for item in history:
        command.robot_session = item.robot_session
        if item.event_code == EVENT_ACCEPTED:
            command.accepted_at = command.accepted_at or item.received_at
        elif item.event_code == EVENT_RUNNING:
            command.started_at = command.started_at or item.received_at
    if event is None:
        command.status = "uncertain"
        command.fault_detail = "Timed out without a matching terminal supervisor event; physical outcome is uncertain."
        command.completed_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return "faulted", command.fault_detail
    command.result_code = event.fault_code
    command.completed_at = event.received_at
    if event.event_code == EVENT_COMPLETED:
        command.status = "completed"
        session.commit()
        return "completed", ""
    if event.event_code == EVENT_LATCHED and event.fault_code == FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION:
        recommendations = {
            "pick_pool": "Robot-held",
            "put_pool": "the destination Pool position",
            "load_mill": "Mill",
            "unload_mill": "Robot-held",
        }
        command.status = "completed_link_lost"
        command.fault_detail = (
            f"Mongo lost its supervisor connection while sequence {event.sequence} was running, but the robot reported "
            f"the atomic {command.operation} step finished before latching. No follow-on command was sent. "
            f"Confirm the pallet is in {recommendations.get(command.operation, 'its expected position')} and reconcile before continuing."
        )
        session.commit()
        # Code 104 is emitted only after the robot's atomic dispatcher has
        # returned. Clear the application latch through a new sequenced,
        # no-motion command before allowing any following physical step.
        supervisor_status = robot_supervisor().status()
        if not (
            supervisor_status.get("connected")
            and supervisor_status.get("latched")
            and supervisor_status.get("robot_last_sequence") == command.sequence
        ):
            return "faulted", (
                f"{command.fault_detail} The reconnect state could not be verified, so the supervisor latch was not cleared."
            )
        clear = _new_supervisor_command(
            session,
            motion=None,
            operation="clear_link_loss_latch",
            opcode=OP_CLEAR_LATCH,
        )
        clear_outcome, clear_detail = _dispatch_supervisor_command(
            session,
            clear,
            max(5.0, timeout_seconds),
            allow_pre_dispatch_fallback=False,
        )
        if clear_outcome == "completed":
            diagnostics().record(
                "robot_supervisor",
                "link_loss_completed_recovered",
                "A completed atomic robot command lost its connection, then the matching supervisor latch was cleared automatically.",
                severity="warning",
                details={"sequence": command.sequence, "operation": command.operation, "clear_sequence": clear.sequence},
            )
            return "completed", ""
        return "faulted", f"{command.fault_detail} Automatic latch clear was not confirmed: {clear_detail}"
    else:
        command.status = "latched" if event.event_code == EVENT_LATCHED else "faulted"
        command.fault_detail = (
            f"Mongo reported {event.name} for sequence {event.sequence} with fault code {event.fault_code}. "
            "Inspect the physical pallet location and reconcile before continuing."
        )
    session.commit()
    return "faulted", command.fault_detail


def _execute_motion_via_supervisor(
    session: Session,
    motion: RobotMotion,
    pallet: Pallet,
) -> str:
    settings = get_settings(session)
    payload_g = max(1, round(pallet.weight_kg * 1000))
    if motion.operation == "pick":
        steps = [("pick_pool", OP_PICK_POOL, motion.source_slot or 0)]
    elif motion.operation == "put":
        steps = [("put_pool", OP_PUT_POOL, motion.destination_slot or 0)]
    elif motion.operation == "load_mill":
        steps = []
        if motion.source_slot:
            steps.append(("pick_pool", OP_PICK_POOL, motion.source_slot))
        steps.append(("load_mill", OP_LOAD_MILL, 0))
    elif motion.operation == "unload_mill":
        steps = [
            ("unload_mill", OP_UNLOAD_MILL, 0),
            ("put_pool", OP_PUT_POOL, motion.destination_slot or 0),
        ]
    else:
        _finish_motion(session, motion, False, f"Unsupported supervisor operation {motion.operation}.")
        return "faulted"

    any_attempted = False
    for operation, opcode, argument in steps:
        if not robot_supervisor().status().get("connected"):
            if not any_attempted and settings.robot_supervisor_pre_dispatch_fallback:
                return "fallback"
            _finish_motion(
                session,
                motion,
                False,
                "Mongo supervisor disconnected before the next atomic step. Legacy fallback is blocked because this movement may already be partially complete.",
            )
            return "faulted"
        command = _new_supervisor_command(
            session,
            motion=motion,
            operation=operation,
            opcode=opcode,
            argument=argument,
            payload_g=payload_g,
        )
        outcome, detail = _dispatch_supervisor_command(
            session,
            command,
            settings.pallet_motion_timeout_seconds,
            allow_pre_dispatch_fallback=(
                not any_attempted and settings.robot_supervisor_pre_dispatch_fallback
            ),
        )
        if command.attempted:
            any_attempted = True
        if outcome == "fallback":
            if any_attempted:
                _finish_motion(session, motion, False, "Supervisor disconnected after this movement had already started; legacy fallback was blocked.")
                return "faulted"
            return "fallback"
        if outcome != "completed":
            _finish_motion(session, motion, False, detail)
            return "faulted"
        motion.status = "running"
        motion.started_at = motion.started_at or command.started_at or command.sent_at
        motion.observed_busy = True
        session.commit()
    _finish_motion(session, motion, True)
    return "completed"


def start_pallet_motion(session: Session, payload: StartPalletMotion, automated: bool = False) -> str | None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not automated:
        assert_run_mode_inactive(settings)
    _assert_reliability_inactive(session)
    locked = _locked_motion(session)
    if locked:
        if locked.status == "faulted":
            raise problem(409, "Resolve the existing pallet-motion fault before commanding another move.")
        raise problem(409, "Another pallet movement is already active.")
    if payload.pool_slot_number > settings.pool_slot_count:
        raise problem(422, "Pool position is outside the configured range.")
    _assert_motion_ready(session, settings)
    if settings.robot_connection_mode == "physical":
        _assert_pool_motion_position_configured(settings, payload.pool_slot_number)

    if payload.operation == "pick":
        if not payload.pallet_id:
            raise problem(422, "Select the pool pallet to pick.")
        pallet = session.get(Pallet, payload.pallet_id)
        if not pallet or pallet.location != "pool" or pallet.pool_slot_number != payload.pool_slot_number:
            raise problem(409, "That pallet is no longer in the selected pool position.")
        source_slot, destination_slot = payload.pool_slot_number, None
        pallet.return_pool_slot_number = source_slot
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
        reserved_by = _pool_slot_reservation_owner(
            session, payload.pool_slot_number, exclude_id=pallet.id,
        )
        if reserved_by:
            raise problem(409, f"Pool position {payload.pool_slot_number:02d} is reserved for {reserved_by.name}.")
        source_slot, destination_slot = None, payload.pool_slot_number
        pallet.return_pool_slot_number = destination_slot

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
    """Queue a physical transfer from Pool/Robot-held to Mill or Mill to Pool."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not automated:
        assert_run_mode_inactive(settings)
    _assert_reliability_inactive(session)
    locked = _locked_motion(session)
    if locked:
        raise problem(409, "Resolve or wait for the active robot pallet movement before commanding another move.")
    _assert_motion_ready(session, settings)

    if payload.operation == "load":
        if not payload.pallet_id:
            raise problem(422, "Choose a pallet to load into the mill.")
        pallet = session.get(Pallet, payload.pallet_id)
        if not pallet or pallet.location not in {"pool", "robot_held"}:
            raise problem(409, "That pallet must be in a pool position or Robot-held before Mongo can load it into the mill.")
        if session.scalar(select(Pallet).where(Pallet.location == "machine")):
            raise problem(409, "The mill already contains a pallet.")
        source_slot = pallet.pool_slot_number if pallet.location == "pool" else None
        if source_slot:
            pallet.return_pool_slot_number = source_slot
        destination_slot = None
        operation = "load_mill"
        if settings.robot_connection_mode == "physical":
            if source_slot:
                _assert_pool_motion_position_configured(settings, source_slot)
            robot_steps = (
                f"{_motion_program(settings, source_slot, 'pick')} -> {_mill_motion_program(settings, 'load')}"
                if source_slot else _mill_motion_program(settings, "load")
            )
            program_path = robot_steps if automated else (
                f"{MILL_PROGRAM_DIRECTORY / MILL_LOAD_POSITION_PROGRAM_NAME} -> {robot_steps}"
            )
        else:
            program_path = "simulated://mill/load"
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
        reserved_by = _pool_slot_reservation_owner(
            session, payload.pool_slot_number, exclude_id=pallet.id,
        )
        if reserved_by:
            raise problem(409, f"Pool position {payload.pool_slot_number:02d} is reserved for {reserved_by.name}.")
        source_slot, destination_slot = None, payload.pool_slot_number
        pallet.return_pool_slot_number = destination_slot
        operation = "unload_mill"
        if settings.robot_connection_mode == "physical":
            _assert_pool_motion_position_configured(settings, destination_slot)
            robot_steps = f"{_mill_motion_program(settings, 'unload')} -> {_motion_program(settings, destination_slot, 'put')}"
            program_path = robot_steps if automated else (
                f"{MILL_PROGRAM_DIRECTORY / MILL_LOAD_POSITION_PROGRAM_NAME} -> {robot_steps}"
            )
        else:
            program_path = "simulated://mill/unload"

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


def start_automatic_put_away(
    session: Session,
    pallet_id: str,
    expected_revision: int,
) -> tuple[str | None, int]:
    """Return a held or loaded pallet to its reserved or nearest available pool position."""
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    if pallet.location not in {"robot_held", "machine"}:
        raise problem(409, "Only a Robot-held pallet or the pallet in the mill can be put away automatically.")
    destination_slot = best_pool_return_slot(session, settings, pallet)
    if pallet.location == "robot_held":
        motion_id = start_pallet_motion(
            session,
            StartPalletMotion(
                expected_revision=expected_revision,
                operation="put",
                pool_slot_number=destination_slot,
                pallet_id=pallet.id,
            ),
        )
    else:
        motion_id = start_mill_pallet_transfer(
            session,
            StartMillPalletTransfer(
                expected_revision=expected_revision,
                operation="unload",
                pallet_id=pallet.id,
                pool_slot_number=destination_slot,
            ),
        )
    return motion_id, destination_slot


def align_robot_for_pallet_pick(session: Session, expected_revision: int) -> dict[str, object]:
    """Rotate the fork to the saved pallet orientation without translating the TCP."""
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    _assert_reliability_inactive(session)
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before aligning the robot for pallet teaching.")
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before aligning the robot.")
    _assert_motion_ready(session, settings)

    generation = pallet_motion_generation(settings)
    orientation = [generation.get(axis) for axis in ("rx_rad", "ry_rad", "rz_rad")]
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in orientation):
        raise problem(422, "Set a finite pallet tool orientation before aligning the robot.")
    if settings.robot_connection_mode == "simulated":
        return {
            "status": "simulated",
            "message": "Simulated alignment accepted. No robot motion was commanded.",
        }
    if not settings.robot_supervisor_enabled or settings.robot_supervisor_maintenance_mode:
        raise problem(409, "Enable the active Mongo supervisor before using pallet-pick alignment.")
    if motion_scripts_need_rebuild(settings):
        raise problem(409, "Rebuild generated robot scripts, then restart the Mongo supervisor before using pallet-pick alignment.")

    command = _new_supervisor_command(
        session,
        motion=None,
        operation="align_pallet_pick",
        opcode=OP_ALIGN_PALLET_PICK,
    )
    outcome, detail = _dispatch_supervisor_command(
        session,
        command,
        max(10.0, settings.robot_timeout_seconds * 12),
        allow_pre_dispatch_fallback=False,
    )
    if outcome != "completed":
        raise problem(409, detail or "The robot did not confirm pallet-pick alignment.")
    return {
        "status": "completed",
        "sequence": command.sequence,
        "message": "Fork aligned to the saved pallet tool orientation. The TCP position was not intentionally translated.",
    }


def run_debug_pallet_motion(session: Session, payload: RunDebugPalletMotion) -> dict[str, object]:
    """Dispatch one generated pallet script for cell setup without changing board state."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    _assert_reliability_inactive(session)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active scheduled pallet movement before running a manual test.")
    if payload.pool_slot_number > settings.pool_slot_count:
        raise problem(422, "Pool position is outside the configured range.")
    _assert_motion_ready(session, settings)
    if settings.robot_connection_mode == "physical":
        _assert_pool_motion_position_configured(settings, payload.pool_slot_number)

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
        pool_position = next(
            (item for item in pallet_location_positions(settings)["pool_locations"] if item["slot"] == payload.pool_slot_number),
            None,
        )
        if pool_position is None:
            raise problem(422, "No configured position exists for that pallet-pool slot.")
        expected_script = build_pallet_motion_script(
            function_name=f"mps_{payload.operation}_pool_{payload.pool_slot_number:03d}",
            operation=payload.operation,
            position=pool_position,
            generation=pallet_motion_generation(settings),
        )
        actual_script = local_script.read_text(encoding="utf-8").replace("\r\n", "\n")
        if actual_script != expected_script:
            raise problem(
                409,
                "The local generated pallet script does not match the saved retrieval settings. Rebuild generated scripts before running it.",
            )
        debug_pallet_query = select(Pallet).where(
            Pallet.location == ("pool" if payload.operation == "pick" else "robot_held"),
        )
        if payload.operation == "pick":
            debug_pallet_query = debug_pallet_query.where(
                Pallet.pool_slot_number == payload.pool_slot_number,
            )
        debug_pallet = session.scalar(debug_pallet_query)
        if debug_pallet:
            expected_script = with_pallet_payload(expected_script, debug_pallet.weight_kg)
        run_robot_script(
            settings.robot_host.strip(),
            expected_script,
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
    if _joint_waypoint(generation.get("safe_pre_waypoint"), "shared safe waypoint") is None:
        raise problem(422, "Capture and save the shared joint-space safe waypoint before using a mill pallet transfer.")
    locations = pallet_location_positions(settings)
    mill_pose = locations["robot_mill_load_unload"]
    mill_pre_entry = _robot_waypoint(generation.get("mill_pre_entry_waypoint"), "Mill pre-entry")
    mill_entry_exit = locations["robot_mill_safe_entry_exit"]
    if not isinstance(mill_pose, dict) or mill_pre_entry is None or not isinstance(mill_entry_exit, dict):
        raise problem(422, "Configure the robot mill load/unload, pre-entry, and entry/exit poses before using a mill pallet transfer.")
    return build_mill_pallet_motion_script(
        function_name=f"mps_{operation}_mill",
        operation=operation,
        mill_pose=mill_pose,
        pre_entry_pose=mill_pre_entry,
        entry_exit_pose=mill_entry_exit,
        generation=generation,
    )


def run_debug_mill_pallet_motion(session: Session, payload: RunDebugMillPalletMotion) -> dict[str, object]:
    """Dispatch a generated mill transfer script without changing scheduled pallet state."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    _assert_reliability_inactive(session)
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
    debug_pallet = session.scalar(select(Pallet).where(
        Pallet.location == ("robot_held" if payload.operation == "load" else "machine"),
    ))
    if debug_pallet:
        script_content = with_pallet_payload(script_content, debug_pallet.weight_kg)
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


def _reliability_script_content(settings: AppSettings, slot: int) -> str:
    generation = pallet_motion_generation(settings)
    staging_pose = _robot_waypoint(generation.get("mill_pre_entry_waypoint"), "Mill pre-entry")
    if staging_pose is None:
        raise problem(422, "Capture and save the outer robot mill pre-entry/staging waypoint before running the reliability test.")
    position = next(
        (item for item in pallet_location_positions(settings)["pool_locations"] if item["slot"] == slot),
        None,
    )
    if position is None:
        raise problem(422, f"Pool {slot:02d} is outside the configured pool.")
    return build_reliability_motion_script(
        function_name=f"mps_reliability_pool_{slot:03d}",
        position=position,
        staging_pose=staging_pose,
        generation=generation,
    )


def _reliability_run_item(run: RobotReliabilityRun) -> dict[str, object]:
    try:
        queue = json.loads(run.queue_snapshot or "[]")
    except json.JSONDecodeError:
        queue = []
    return {
        "id": run.id,
        "status": run.status,
        "total_pallets": run.total_pallets,
        "completed_pallets": run.completed_pallets,
        "current_index": run.current_index,
        "current_pallet_id": run.current_pallet_id,
        "current_pallet_name": run.current_pallet_name,
        "current_pool_slot": run.current_pool_slot,
        "cancel_requested": run.cancel_requested,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "failure_detail": run.failure_detail,
        "queue_snapshot": queue if isinstance(queue, list) else [],
    }


def robot_reliability_status(session: Session) -> dict[str, object]:
    runs = session.scalars(
        select(RobotReliabilityRun)
        .order_by(RobotReliabilityRun.created_at.desc())
        .limit(10)
    ).all()
    active = next((run for run in runs if run.status in {"requested", "running"}), None)
    return {
        "active": _reliability_run_item(active) if active else None,
        "latest": _reliability_run_item(runs[0]) if runs else None,
        "history": [_reliability_run_item(run) for run in runs],
    }


def start_robot_reliability_test(session: Session, expected_revision: int) -> str:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before starting the queue reliability test.")
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before starting the reliability test.")
    _assert_reliability_inactive(session)
    queue = session.scalars(
        select(Pallet)
        .where(Pallet.queue_position.is_not(None))
        .order_by(Pallet.queue_position)
    ).all()
    if not queue:
        raise problem(409, "Add at least one pallet to the production queue before starting the reliability test.")

    snapshot: list[dict[str, object]] = []
    for pallet in queue:
        if pallet.location != "pool" or pallet.pool_slot_number is None:
            raise problem(409, f"{pallet.name} must be in a Pool position before the reliability test can start.")
        snapshot.append({
            "pallet_id": pallet.id,
            "pallet_name": pallet.name,
            "queue_position": pallet.queue_position,
            "pool_slot": pallet.pool_slot_number,
            "weight_kg": pallet.weight_kg,
        })

    if settings.robot_connection_mode == "physical":
        _assert_motion_ready(session, settings)
        if motion_scripts_need_rebuild(settings):
            raise problem(409, "Generated robot scripts do not match Settings. Rebuild them before starting the reliability test.")
        local_root = generated_script_directory(Path(__file__).parents[1])
        for item in snapshot:
            slot = int(item["pool_slot"])
            _assert_pool_motion_position_configured(settings, slot)
            program_path = _reliability_program(settings, slot)
            local_script = local_root / PurePosixPath(program_path).name
            if not local_script.is_file():
                raise problem(409, f"Generated local script is missing: {local_script.name}. Rebuild generated scripts first.")
            if local_script.read_text(encoding="utf-8") != _reliability_script_content(settings, slot):
                raise problem(409, f"{local_script.name} does not match saved poses. Rebuild generated scripts first.")

    run = RobotReliabilityRun(
        id=str(uuid4()),
        status="requested",
        queue_snapshot=json.dumps(snapshot, separators=(",", ":")),
        total_pallets=len(snapshot),
        completed_pallets=0,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise problem(409, "Another queue reliability test started first.") from exc
    diagnostics().record(
        "robot_reliability",
        "started",
        f"Queue reliability test captured {len(snapshot)} pallets.",
        details={"run_id": run.id, "queue": snapshot},
    )
    return run.id


def cancel_robot_reliability_test(session: Session) -> dict[str, object]:
    run = _active_reliability_run(session)
    if not run:
        raise problem(409, "No queue reliability test is active.")
    run.cancel_requested = True
    session.commit()
    diagnostics().record(
        "robot_reliability",
        "cancel_requested",
        "Reliability test will stop after the current pallet is returned.",
        severity="warning",
        details={"run_id": run.id, "current_pallet": run.current_pallet_name},
    )
    return robot_reliability_status(session)


def _finish_reliability_run(
    session: Session,
    run: RobotReliabilityRun,
    status: str,
    detail: str | None = None,
) -> None:
    run.status = status
    run.failure_detail = detail
    run.current_index = None
    run.current_pallet_id = None
    run.current_pallet_name = None
    run.current_pool_slot = None
    run.completed_at = datetime.now(timezone.utc).isoformat()
    session.commit()
    diagnostics().record(
        "robot_reliability",
        status,
        detail or f"Queue reliability test {status}.",
        severity="error" if status in {"faulted", "interrupted"} else "warning" if status == "cancelled" else "info",
        details={
            "run_id": run.id,
            "completed_pallets": run.completed_pallets,
            "total_pallets": run.total_pallets,
        },
    )


def _wait_for_reliability_motion(session: Session, settings: AppSettings) -> None:
    deadline = time.monotonic() + settings.pallet_motion_timeout_seconds
    observed_motion = False
    settled_polls = 0
    telemetry_outage_started: float | None = None
    while time.monotonic() < deadline:
        try:
            moving, state = _robot_motion_activity(session)
        except HTTPException as exc:
            now = time.monotonic()
            telemetry_outage_started = telemetry_outage_started or now
            trigger_network_diagnostic_on_robot_loss()
            if now - telemetry_outage_started < _MOTION_TELEMETRY_OUTAGE_GRACE_SECONDS:
                time.sleep(0.5)
                continue
            raise problem(503, f"Robot telemetry remained unavailable during the reliability cycle: {exc.detail}") from exc
        telemetry_outage_started = None
        safety_error = _motion_safety_error(state)
        if safety_error:
            raise problem(409, safety_error)
        if moving:
            observed_motion = True
            settled_polls = 0
        elif observed_motion:
            settled_polls += 1
            if settled_polls >= 4 and _motion_runtime_is_idle(state):
                return
        time.sleep(0.25)
    if not observed_motion:
        raise problem(504, "The reliability script was dispatched, but robot motion was never observed.")
    raise problem(504, "The reliability cycle did not return to a stationary idle state before timeout.")


def _execute_reliability_cycle(session: Session, run: RobotReliabilityRun, item: dict[str, object]) -> None:
    settings = get_settings(session)
    pallet = session.get(Pallet, str(item["pallet_id"]))
    slot = int(item["pool_slot"])
    if not pallet or pallet.location != "pool" or pallet.pool_slot_number != slot:
        raise problem(409, f"{item['pallet_name']} is no longer in Pool {slot:02d}; the frozen test sequence was stopped.")
    if settings.robot_connection_mode == "simulated":
        time.sleep(0.05)
        return
    _assert_motion_ready(session, settings)

    if settings.robot_supervisor_enabled:
        command = _new_supervisor_command(
            session,
            motion=None,
            operation=f"reliability_pool_{slot:03d}",
            opcode=OP_RELIABILITY_POOL,
            argument=slot,
            payload_g=max(1, round(float(item["weight_kg"]) * 1000)),
        )
        outcome, detail = _dispatch_supervisor_command(
            session,
            command,
            settings.pallet_motion_timeout_seconds,
            allow_pre_dispatch_fallback=settings.robot_supervisor_pre_dispatch_fallback,
        )
        if outcome == "completed":
            return
        if outcome != "fallback":
            raise problem(409, detail)

    program_path = _reliability_program(settings, slot)
    local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(program_path).name
    script = with_pallet_payload(local_script.read_text(encoding="utf-8"), float(item["weight_kg"]))
    run_robot_script(settings.robot_host.strip(), script, settings.robot_timeout_seconds)
    _wait_for_reliability_motion(session, settings)


def execute_robot_reliability_test(session_factory, run_id: str) -> None:
    with session_factory() as session:
        run = session.get(RobotReliabilityRun, run_id)
        if not run or run.status != "requested":
            return
        try:
            queue = json.loads(run.queue_snapshot)
            run.status = "running"
            run.started_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            for index, item in enumerate(queue):
                session.refresh(run)
                if run.cancel_requested:
                    _finish_reliability_run(session, run, "cancelled", "Stopped between pallets at the operator's request.")
                    return
                run.current_index = index
                run.current_pallet_id = str(item["pallet_id"])
                run.current_pallet_name = str(item["pallet_name"])
                run.current_pool_slot = int(item["pool_slot"])
                session.commit()
                _execute_reliability_cycle(session, run, item)
                run.completed_pallets = index + 1
                run.current_index = None
                run.current_pallet_id = None
                run.current_pallet_name = None
                run.current_pool_slot = None
                session.commit()
            _finish_reliability_run(session, run, "completed", "Every pallet in the captured queue completed its pick, staging, and same-slot put-away cycle.")
        except HTTPException as exc:
            session.rollback()
            run = session.get(RobotReliabilityRun, run_id)
            if run and run.status in {"requested", "running"}:
                _finish_reliability_run(
                    session,
                    run,
                    "faulted",
                    f"Reliability test stopped with an uncertain physical pallet state: {exc.detail}",
                )
        except (RobotDashboardError, RobotFileAccessError) as exc:
            session.rollback()
            run = session.get(RobotReliabilityRun, run_id)
            if run and run.status in {"requested", "running"}:
                _finish_reliability_run(session, run, "faulted", f"Reliability test transport failed; inspect the current pallet: {exc}")
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            session.rollback()
            run = session.get(RobotReliabilityRun, run_id)
            if run and run.status in {"requested", "running"}:
                _finish_reliability_run(session, run, "faulted", f"Unexpected reliability-test failure; inspect the current pallet: {exc}")


def interrupt_robot_reliability_test(session: Session) -> None:
    run = _active_reliability_run(session)
    if not run:
        return
    _finish_reliability_run(
        session,
        run,
        "interrupted",
        "Backend restarted during the reliability test. Physical pallet state is uncertain and must be inspected before another test.",
    )


def execute_pallet_motion(session_factory, motion_id: str) -> None:
    """Run one persisted physical motion. Every terminal outcome is committed for recovery."""
    with session_factory() as session:
        motion = session.get(RobotMotion, motion_id)
        if not motion or motion.status != "requested":
            return
        try:
            settings = get_settings(session)

            pallet = session.get(Pallet, motion.pallet_id)
            if not pallet:
                _finish_motion(session, motion, False, "The pallet no longer exists.")
                return

            mill_position_already_run = False
            robot_warning_emitted = False
            if settings.robot_supervisor_enabled:
                if motion.operation in {"load_mill", "unload_mill"} and MILL_LOAD_POSITION_PROGRAM_NAME in motion.program_path:
                    try:
                        _run_manual_mill_load_position_cycle(session, settings)
                        mill_position_already_run = True
                    except (HTTPException, CncTelemetryError, RobotFileAccessError) as exc:
                        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                        _finish_motion(session, motion, False, f"Mill loading-position move failed: {detail}")
                        return
                robot_warning_emitted = emit_pre_motion_alarm(session, f"robot:{motion.operation}")
                supervisor_outcome = _execute_motion_via_supervisor(session, motion, pallet)
                if supervisor_outcome != "fallback":
                    return

            def generated_script_content(local_script: Path) -> str:
                return with_pallet_payload(
                    local_script.read_text(encoding="utf-8"), pallet.weight_kg,
                )

            def run_script(program_path: str) -> None:
                if PurePosixPath(program_path).suffix.lower() == ".script":
                    local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(program_path).name
                    if not local_script.is_file():
                        raise RobotFileAccessError(f"Generated local script is missing: {local_script.name}")
                    run_robot_script(settings.robot_host.strip(), generated_script_content(local_script), settings.robot_timeout_seconds)
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
                    except RobotScriptTransferUncertain as exc:
                        motion.retry_count += 1
                        _finish_motion(session, motion, False, f"Robot program transfer became uncertain and was not retried: {exc}")
                        return False
                    except (RobotDashboardError, RobotFileAccessError) as exc:
                        motion.retry_count += 1
                        if attempt or not allow_retry:
                            _finish_motion(session, motion, False, f"Robot program start failed: {exc}")
                            return False
                        session.commit()
                deadline = time.monotonic() + settings.pallet_motion_timeout_seconds
                observed_stage_motion = False
                settled_polls = 0
                telemetry_outage_started: float | None = None
                while time.monotonic() < deadline:
                    try:
                        moving, state = _robot_motion_activity(session)
                    except HTTPException as exc:
                        now = time.monotonic()
                        telemetry_outage_started = telemetry_outage_started or now
                        trigger_network_diagnostic_on_robot_loss()
                        if now - telemetry_outage_started < _MOTION_TELEMETRY_OUTAGE_GRACE_SECONDS:
                            time.sleep(0.5)
                            continue
                        _finish_motion(
                            session,
                            motion,
                            False,
                            f"Robot telemetry was unavailable throughout the movement-monitoring grace period: {exc.detail}",
                        )
                        return False
                    telemetry_outage_started = None
                    safety_error = _motion_safety_error(state)
                    if safety_error:
                        _finish_motion(session, motion, False, safety_error)
                        return False
                    if moving:
                        motion.observed_busy = True
                        observed_stage_motion = True
                        settled_polls = 0
                        session.commit()
                    elif observed_stage_motion:
                        settled_polls += 1
                        if settled_polls >= 4:
                            if not _motion_runtime_is_idle(state):
                                continue
                            pose_error = _motion_final_pose_error(state, settings, program_path)
                            if pose_error:
                                _finish_motion(session, motion, False, pose_error)
                                return False
                            return True
                    time.sleep(0.25)
                _finish_motion(session, motion, False, "Timed out waiting for the generated script to move the TCP and settle.")
                return False

            if motion.operation == "load_mill":
                if MILL_LOAD_POSITION_PROGRAM_NAME in motion.program_path and not mill_position_already_run:
                    try:
                        _run_manual_mill_load_position_cycle(session, settings)
                    except (HTTPException, CncTelemetryError, RobotFileAccessError) as exc:
                        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                        _finish_motion(session, motion, False, f"Mill loading-position move failed: {detail}")
                        return
                if not robot_warning_emitted:
                    emit_pre_motion_alarm(session, f"robot:{motion.operation}")
                if motion.source_slot and not run_and_wait(_motion_program(settings, motion.source_slot, "pick"), True):
                    return
                if not run_and_wait(_mill_motion_program(settings, "load"), False):
                    return
                _finish_motion(session, motion, True)
                return
            if motion.operation == "unload_mill":
                if MILL_LOAD_POSITION_PROGRAM_NAME in motion.program_path and not mill_position_already_run:
                    try:
                        _run_manual_mill_load_position_cycle(session, settings)
                    except (HTTPException, CncTelemetryError, RobotFileAccessError) as exc:
                        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                        _finish_motion(session, motion, False, f"Mill loading-position move failed: {detail}")
                        return
                if not robot_warning_emitted:
                    emit_pre_motion_alarm(session, f"robot:{motion.operation}")
                if not run_and_wait(_mill_motion_program(settings, "unload"), True):
                    return
                if not run_and_wait(_motion_program(settings, motion.destination_slot or 0, "put"), False):
                    return
                _finish_motion(session, motion, True)
                return

            if not robot_warning_emitted:
                emit_pre_motion_alarm(session, f"robot:{motion.operation}")
            started = False
            for attempt in range(2):
                try:
                    if PurePosixPath(motion.program_path).suffix.lower() == ".script":
                        local_script = generated_script_directory(Path(__file__).parents[1]) / PurePosixPath(motion.program_path).name
                        if not local_script.is_file():
                            raise RobotDashboardError(f"Generated local script is missing: {local_script.name}")
                        run_robot_script(
                            settings.robot_host.strip(),
                            generated_script_content(local_script),
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
                except RobotScriptTransferUncertain as exc:
                    motion.retry_count = attempt + 1
                    _finish_motion(session, motion, False, f"Generated script transfer became uncertain and was not retried: {exc}")
                    return
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
            telemetry_outage_started: float | None = None
            while time.monotonic() < deadline:
                try:
                    moving, state = _robot_motion_activity(session)
                except HTTPException as exc:
                    now = time.monotonic()
                    telemetry_outage_started = telemetry_outage_started or now
                    trigger_network_diagnostic_on_robot_loss()
                    if now - telemetry_outage_started < _MOTION_TELEMETRY_OUTAGE_GRACE_SECONDS:
                        time.sleep(0.5)
                        continue
                    _finish_motion(
                        session,
                        motion,
                        False,
                        f"Robot telemetry was unavailable throughout the movement-monitoring grace period: {exc.detail}",
                    )
                    return
                telemetry_outage_started = None
                safety_error = _motion_safety_error(state)
                if safety_error:
                    _finish_motion(session, motion, False, safety_error)
                    return
                _mark_pick_as_held_after_lift(session, motion, state, settings)
                if moving:
                    motion.observed_busy = True
                    settled_polls = 0
                    session.commit()
                elif motion.observed_busy:
                    settled_polls += 1
                    if settled_polls >= 4:
                        if not _motion_runtime_is_idle(state):
                            continue
                        pose_error = _motion_final_pose_error(state, settings, motion.program_path)
                        if pose_error:
                            _finish_motion(session, motion, False, pose_error)
                            return
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
    refresh_stack_light(session)


def _finish_run_mode(session_factory, state: str, detail: str, run_token: str | None = None) -> None:
    with session_factory() as session:
        settings = get_settings(session)
        if run_token is not None and settings.run_mode_start_request_id != run_token:
            return
        settings.run_mode_enabled = False
        settings.run_mode_state = state
        settings.run_mode_detail = detail
        settings.run_mode_pending_action = ""
        settings.run_mode_confirmation_token = ""
        settings.run_mode_confirmation_granted = False
        settings.run_mode_manual_robot_pause = False
        settings.run_mode_program_started_at = None
        settings.run_mode_start_request_id = ""
        settings.run_mode_loaded_machine_action = ""
        settings.run_mode_current_pallet_id = None
        settings.run_mode_return_slot = None
        bump(settings)
        commit_or_conflict(session)
        refresh_stack_light(session)
    diagnostics().record(
        "run_mode",
        state,
        detail,
        severity="error" if state == "faulted" else "warning" if state == "interrupted" else "info",
    )
    if state == "faulted":
        _send_push_notification(settings, "error", f"MPS error: {detail}")


def interrupt_run_mode(session: Session) -> None:
    """Never resume production commands implicitly after a backend restart."""
    settings = get_settings(session)
    if not settings.run_mode_enabled and settings.run_mode_state != "stopping":
        return
    settings.run_mode_enabled = False
    settings.run_mode_state = "interrupted"
    settings.run_mode_detail = "Run mode was interrupted by a backend restart. Inspect the cell before starting again."
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    settings.run_mode_manual_robot_pause = False
    settings.run_mode_program_started_at = None
    settings.run_mode_start_request_id = ""
    settings.run_mode_loaded_machine_action = ""
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


def _mill_load_position_local_path() -> Path:
    return Path(__file__).parents[1] / "runtime" / "generated-mill-programs" / MILL_LOAD_POSITION_PROGRAM_NAME


def _assert_mill_load_position_program_current(settings: AppSettings) -> str:
    """Verify PathPilot's generated program and repair a stale local cache copy."""
    expected = build_mill_load_position_program(pallet_location_positions(settings)["mill_load_unload_g53"])
    local_path = _mill_load_position_local_path()
    remote_path = str(_mill_program_root(settings) / MILL_LOAD_POSITION_PROGRAM_NAME)
    try:
        remote = read_robot_file(
            host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
            username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
            directory=settings.mill_file_directory, path=remote_path,
            timeout_seconds=settings.cnc_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(409, f"The PathPilot loading-position program could not be verified: {exc}") from exc
    if remote.get("binary") or remote.get("too_large") or str(remote.get("text", "")).replace("\r\n", "\n") != expected:
        raise problem(409, "The PathPilot mill loading-position program does not match Settings. Rebuild it before starting run mode.")
    try:
        local_content = local_path.read_text(encoding="ascii")
    except OSError:
        local_content = ""
    if local_content.replace("\r\n", "\n") != expected:
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(expected, encoding="ascii", newline="\n")
        except OSError as exc:
            raise problem(409, f"PathPilot is current, but MPS could not repair its local loading-position cache: {exc}") from exc
    return remote_path


def _assert_run_mode_files_ready(settings: AppSettings, queue: list[Pallet]) -> None:
    if motion_scripts_need_rebuild(settings):
        raise problem(409, "Generated robot scripts do not match Settings. Rebuild them before starting run mode.")
    _assert_mill_load_position_program_current(settings)
    _mill_status_file_path(settings)
    if settings.robot_connection_mode == "physical":
        try:
            test_mill_status_file_access(settings)
        except RobotFileAccessError as exc:
            raise problem(409, f"The dedicated mill completion status file is not accessible: {exc}") from exc

    required_local_scripts = {"load_mill.script", "unload_mill.script"}
    for pallet in queue:
        if not pallet.pool_slot_number:
            continue
        for operation in ("pick", "put"):
            program = _motion_program(settings, pallet.pool_slot_number or 0, operation)
            if PurePosixPath(program).suffix.lower() == ".script":
                required_local_scripts.add(PurePosixPath(program).name)
    local_script_root = generated_script_directory(Path(__file__).parents[1])
    missing_scripts = sorted(name for name in required_local_scripts if not (local_script_root / name).is_file())
    if missing_scripts:
        raise problem(409, "Generated robot scripts are missing locally: " + ", ".join(missing_scripts) + ". Rebuild them before starting run mode.")

    try:
        remote_files = set(list_robot_program_files(
            host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
            username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
            directory=str(_mill_program_root(settings)), extensions=None,
            timeout_seconds=settings.cnc_timeout_seconds,
        ))
    except RobotFileAccessError as exc:
        raise problem(409, f"Queued PathPilot programs could not be verified: {exc}") from exc
    extensions = set(json.loads(settings.mill_program_extensions))
    missing_programs = []
    for pallet in queue:
        remote_program = _run_mode_program_path(settings, pallet.program_path or "", extensions)
        if remote_program not in remote_files:
            missing_programs.append(f"{pallet.name}: {remote_program}")
    if missing_programs:
        raise problem(409, "Queued programs are missing from PathPilot: " + "; ".join(missing_programs))


def _run_mode_token_is_active(settings: AppSettings, run_token: str | None) -> bool:
    return settings.run_mode_enabled and (run_token is None or settings.run_mode_start_request_id == run_token)


def pause_run_mode_for_manual_robot_control(session: Session) -> bool:
    """Hold the next robot action after Mongo's supervisor is interrupted by manual control."""
    settings = get_settings(session)
    if (
        not settings.run_mode_enabled
        or settings.run_mode_manual_robot_pause
        or settings.run_mode_state not in {"machining", "manual_robot_pause"}
        or _locked_motion(session)
    ):
        return False
    settings.run_mode_manual_robot_pause = True
    settings.run_mode_state = "manual_robot_pause"
    settings.run_mode_detail = (
        "Manual robot control detected. The active mill program continues; MPS will not start another robot action. "
        "Finish teaching, then choose Resume queue."
    )
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record("run_mode", "manual_robot_pause", settings.run_mode_detail, severity="warning")
    return True


def _wait_for_manual_robot_pause(session_factory, run_token: str | None) -> bool:
    """Never let a completed mill cycle advance to a robot action while teaching is active."""
    while True:
        with session_factory() as session:
            settings = get_settings(session)
            if not _run_mode_token_is_active(settings, run_token):
                return False
            if not settings.run_mode_manual_robot_pause:
                return True
        time.sleep(0.25)


def resume_run_mode_after_manual_robot_control(session: Session, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if not settings.run_mode_enabled or not settings.run_mode_manual_robot_pause:
        raise problem(409, "Run Mode is not waiting for manual robot control.")
    if _locked_motion(session):
        raise problem(409, "Wait for the active robot movement to finish before resuming the queue.")
    if settings.robot_connection_mode == "physical":
        recover_robot_supervisor_program(
            session,
            trigger="manual-control-resume",
            allow_run_mode_manual_pause=True,
        )
        settings = get_settings(session)
        _assert_motion_ready(session, settings)
    settings.run_mode_manual_robot_pause = False
    settings.run_mode_state = "resuming"
    settings.run_mode_detail = "Manual robot control ended. Run Mode is continuing safely."
    bump(settings)
    commit_or_conflict(session)


def update_pool_locations_during_manual_robot_pause(session: Session, payload) -> None:
    """Persist taught pool positions without changing scripts or dispatching motion."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.run_mode_enabled or not settings.run_mode_manual_robot_pause:
        raise problem(409, "Pool locations can only be saved here while Run Mode is waiting for manual robot control.")
    if _locked_motion(session):
        raise problem(409, "Wait for the active robot movement to finish before saving pool locations.")
    store_pallet_location_positions(
        settings,
        [item.model_dump() for item in payload.pool_locations],
        None, None, None, None, None, None,
    )
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record(
        "settings", "pool_locations_taught_during_run",
        "Saved pallet-pool locations while Run Mode was paused for manual robot control.",
    )


def start_run_mode(session: Session, payload: StartRunMode) -> str | None:
    settings = get_settings(session)
    request_id = payload.request_id or str(uuid4())
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled:
        if settings.run_mode_start_request_id == request_id:
            return None
        raise problem(409, "Run mode is already active.")
    if settings.run_mode_state == "stopping":
        raise problem(409, "Run Mode is still stopping. Wait for the active worker to acknowledge the stop request.")
    _assert_reliability_inactive(session)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before starting run mode.")
    machine_pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
    if machine_pallet:
        if payload.loaded_machine_action is None:
            raise problem(409, "A pallet is recorded in the mill. Choose whether to unload it before the queue or run its assigned program.")
        if not machine_pallet.return_pool_slot_number:
            raise problem(409, f"{machine_pallet.name} has no reserved return pool position. Reconcile its pallet location before starting Run Mode.")
        if payload.loaded_machine_action == "run_machine_program" and not machine_pallet.program_path:
            raise problem(409, f"{machine_pallet.name} has no assigned mill program to run.")
    queue = session.scalars(
        select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
    ).all()
    for pallet in queue:
        if pallet.location != "pool" or not pallet.pool_slot_number:
            raise problem(409, f"{pallet.name} must be in a pallet-pool position before run mode can start.")
        if not pallet.program_path:
            raise problem(409, f"Assign a mill program to {pallet.name} before starting run mode.")
        if pallet.content_status in {"complete_parts", "defective_parts"}:
            raise problem(409, f"{pallet.name} is already marked {pallet.content_status.replace('_', ' ')}.")
    if machine_pallet and payload.loaded_machine_action == "unload_then_queue" and not queue:
        raise problem(409, "Add a ready pallet to the queue before choosing unload and start next.")
    if settings.robot_connection_mode == "physical" and not settings.pallet_motion_enabled:
        raise problem(403, "Enable physical pallet movements before starting run mode.")
    if settings.robot_connection_mode == "physical" and (not settings.cnc_telemetry_enabled or not settings.cnc_host.strip()):
        raise problem(409, "Enable and configure CNC telemetry before starting physical run mode.")
    if settings.robot_connection_mode == "physical" and (not settings.cnc_ssh_username or not settings.cnc_ssh_password):
        raise problem(409, "Configure the PathPilot SSH username and password before starting physical run mode.")

    # Persist the request before slow network checks. A lost browser response can
    # therefore never leave a hidden start that the Stop control cannot cancel.
    settings.run_mode_enabled = True
    if payload.safety_confirm is not None:
        settings.run_mode_safety_confirm = payload.safety_confirm
    settings.run_mode_start_request_id = request_id
    settings.run_mode_loaded_machine_action = payload.loaded_machine_action or ""
    initial_work = bool(queue) or machine_pallet is not None
    settings.run_mode_state = "start_requested" if initial_work else "idle_waiting_queue"
    settings.run_mode_detail = (
        f"Start requested. Checking the pallet already in the mill and {len(queue)} queued pallet{'s' if len(queue) != 1 else ''}."
        if machine_pallet
        else f"Start requested. Checking {len(queue)} queued pallet{'s' if len(queue) != 1 else ''}."
        if queue
        else "Run Mode is armed and waiting for a pallet to be added to the production queue."
    )
    settings.run_mode_current_pallet_id = None
    settings.run_mode_return_slot = None
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    settings.run_mode_program_started_at = None
    settings.run_mode_manual_robot_pause = False
    settings.run_mode_manual_robot_pause = False
    bump(settings)
    commit_or_conflict(session)
    refresh_stack_light(session)
    return request_id if initial_work else None


def resume_armed_run_mode(session: Session) -> str | None:
    """Reserve one scheduler worker when an armed, idle queue becomes runnable."""
    settings = get_settings(session)
    if not settings.run_mode_enabled or settings.run_mode_state != "idle_waiting_queue":
        return None
    queue = session.scalars(
        select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
    ).all()
    if not queue:
        return None
    not_ready = next((item for item in queue if (
        item.location != "pool"
        or not item.pool_slot_number
        or not item.program_path
        or item.content_status in {"complete_parts", "defective_parts"}
    )), None)
    if not_ready:
        settings.run_mode_detail = (
            f"Run Mode is armed, but {not_ready.name} still needs a pool position, an assigned program, and runnable stock status."
        )
        bump(settings)
        commit_or_conflict(session)
        refresh_stack_light(session)
        return None
    settings.run_mode_state = "resuming"
    settings.run_mode_detail = f"Queue updated. Checking {len(queue)} queued pallet{'s' if len(queue) != 1 else ''}."
    token = settings.run_mode_start_request_id
    bump(settings)
    commit_or_conflict(session)
    return token or None


def stop_run_mode(session: Session, expected_revision: int) -> None:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if not settings.run_mode_enabled and settings.run_mode_state != "stopping":
        return
    settings.run_mode_enabled = False
    # Stopping is logical and immediate: the worker observes the disabled token
    # before it can dispatch another action. Do not leave the operator-facing
    # state stuck on an acknowledgement from a slow controller I/O call.
    settings.run_mode_state = "stopped"
    settings.run_mode_detail = (
        "Stop acknowledged. No pending or subsequent automated step may start; "
        "an already-running mill program is not aborted."
    )
    settings.run_mode_pending_action = ""
    settings.run_mode_loaded_machine_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    settings.run_mode_manual_robot_pause = False
    bump(settings)
    commit_or_conflict(session)
    refresh_stack_light(session)
    diagnostics().record(
        "run_mode",
        "stop_requested",
        "Run Mode stop requested; the scheduler will not dispatch another action.",
        details={"state": settings.run_mode_state},
    )


def cancel_run_mode_recovery(session: Session, expected_revision: int) -> None:
    """Stop a paused or faulted scheduler without moving a pallet or controller."""
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    settings.run_mode_enabled = False
    settings.run_mode_state = "stopped"
    settings.run_mode_detail = "Operator cancelled recovery. No further automated action will start; the pallet remains where it is."
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    settings.run_mode_manual_robot_pause = False
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record("run_mode", "recovery_cancelled", settings.run_mode_detail, severity="warning")


def confirm_run_mode_action(session: Session, payload: ConfirmRunModeAction) -> None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.run_mode_enabled or settings.run_mode_confirmation_token != payload.token:
        raise problem(409, "That run-mode confirmation is no longer active.")
    if not payload.approved:
        cnc_fault = settings.run_mode_pending_action == "retry_cnc_program"
        cnc_preflight_retry = settings.run_mode_pending_action == "retry_cnc_preflight"
        robot_retry = settings.run_mode_pending_action == "retry_robot_transfer"
        previous_detail = settings.run_mode_detail
        settings.run_mode_enabled = False
        settings.run_mode_state = "stopped"
        settings.run_mode_detail = (
            f"Operator stopped Run Mode after a CNC fault. The pallet remains in place. {previous_detail}"
            if cnc_fault
            else f"Operator stopped Run Mode after PathPilot connection recovery was exhausted. The pallet remains in place. {previous_detail}"
            if cnc_preflight_retry
            else f"Operator stopped Run Mode before retrying the robot transfer. The pallet remains in place. {previous_detail}"
            if robot_retry
            else "Operator declined the pending action. Run mode stopped."
        )
        settings.run_mode_pending_action = ""
        settings.run_mode_confirmation_token = ""
        settings.run_mode_confirmation_granted = False
        settings.run_mode_manual_robot_pause = False
        settings.run_mode_manual_robot_pause = False
    else:
        if settings.run_mode_pending_action in {"retry_cnc_program", "retry_cnc_preflight", "retry_robot_transfer"}:
            settings.machine_state = "idle"
            settings.run_mode_alert = ""
        settings.run_mode_confirmation_granted = True
        settings.run_mode_state = "approved"
        settings.run_mode_detail = "Operator approved the pending action."
    bump(settings)
    commit_or_conflict(session)


def _await_run_mode_action(
    session_factory,
    action: str,
    detail: str,
    *,
    force_confirmation: bool = False,
    run_token: str | None = None,
) -> bool:
    with session_factory() as session:
        settings = get_settings(session)
        if not _run_mode_token_is_active(settings, run_token):
            return False
        if not settings.run_mode_safety_confirm and not force_confirmation:
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
            if not _run_mode_token_is_active(settings, run_token):
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


def _run_mode_program_path(settings: AppSettings, program_path: str, extensions: set[str]) -> str:
    relative = PurePosixPath(program_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise problem(422, "Queued mill program paths must remain inside the PathPilot Gcode folder.")
    if relative.suffix.lower() not in extensions:
        raise problem(422, f"Queued program {program_path} is not an allowed mill program type.")
    return str(_mill_program_root(settings).joinpath(*relative.parts))


def _run_mode_motion_succeeded(session_factory, motion_id: str | None) -> bool:
    if motion_id is None:
        return True
    execute_pallet_motion(session_factory, motion_id)
    with session_factory() as session:
        motion = session.get(RobotMotion, motion_id)
        return bool(motion and motion.status == "succeeded")


def _run_cnc_cycle_via_supervisor(
    session: Session,
    settings: AppSettings,
    remote_program: str,
    *,
    cycle_label: str,
    timeout_seconds: float,
    continue_check=None,
    status_report=None,
    completion_status: dict[str, object] | None = None,
) -> bool:
    """Run one program through the durable mill-originated supervisor."""
    supervisor = mill_supervisor()
    status = supervisor.status()
    heartbeat_age = status.get("heartbeat_age_seconds")
    freshness_limit = max(10.0, settings.mill_supervisor_heartbeat_seconds * 3)
    if not (
        settings.mill_supervisor_activation_verified
        and status.get("connected")
        and heartbeat_age is not None
        and float(heartbeat_age) <= freshness_limit
    ):
        raise CncPreDispatchTelemetryError(
            f"The mill supervisor was not fresh and connected before {cycle_label} dispatch."
        )
    if status.get("mill_last_sequence") != settings.mill_supervisor_last_sequence:
        raise CncPreDispatchTelemetryError(
            "The mill supervisor sequence does not match the durable command ledger. Reconcile before dispatch."
        )
    telemetry = status.get("telemetry") or {}
    if telemetry.get("interp_state") != 1:
        raise CncPreDispatchTelemetryError("PathPilot is not Idle; no supervisor command was sent.")
    if telemetry.get("estop") is True or telemetry.get("enabled") is False:
        raise CncPreDispatchTelemetryError("PathPilot is disabled or in E-stop; no supervisor command was sent.")

    emit_pre_motion_alarm(session, f"mill:{cycle_label}")
    command = _new_mill_supervisor_command(
        session,
        "run_program",
        {
            "program": remote_program,
            "require_a_axis_homed": settings.cnc_require_a_axis_homed,
            "require_completion_status": bool(completion_status),
        },
    )
    sent, detail = _dispatch_mill_supervisor_command(session, command)
    if not sent:
        raise CncProgramFault(
            f"{cycle_label} has an uncertain supervisor dispatch result: {detail}. The program was not retried."
        )
    diagnostics().record(
        "run_mode",
        "cnc_supervisor_dispatch",
        f"{cycle_label} was dispatched through the persistent mill supervisor.",
        details={"program": remote_program, "sequence": command.sequence},
    )

    started = time.monotonic()
    outage_started: float | None = None
    last_report = 0.0
    while time.monotonic() - started < timeout_seconds:
        if continue_check and not continue_check():
            command.status = "monitoring_released"
            command.fault_detail = "Run Mode stopped while PathPilot retained ownership of the active program."
            session.commit()
            return False
        event = supervisor.wait_for_event(
            command.sequence,
            {"completed", "faulted", "latched"},
            min(2.0, max(0.1, timeout_seconds - (time.monotonic() - started))),
        )
        history = supervisor.events_for(command.sequence)
        for item in history:
            if item.name == "accepted":
                command.accepted_at = command.accepted_at or item.received_at
                if command.status in {"sent", "dispatching"}:
                    command.status = "accepted"
            elif item.name == "running":
                command.started_at = command.started_at or item.received_at
                command.status = "running"
        if event:
            command.completed_at = event.received_at
            command.result_json = json.dumps(event.result or {}, sort_keys=True, separators=(",", ":"))
            if event.name == "completed":
                command.status = "completed"
                command.fault_detail = None
                session.commit()
                if completion_status:
                    complete, completion_detail = _mill_status_record(
                        settings,
                        str(completion_status["program"]),
                        completion_status.get("previous_signature"),
                        completion_status.get("previous_legacy_signature"),
                    )
                    if not complete:
                        raise CncProgramFault(
                            f"{cycle_label} returned to Idle without a valid completion record: {completion_detail}"
                        )
                diagnostics().record(
                    "run_mode",
                    "cnc_supervisor_completed",
                    f"{cycle_label} completed through the persistent mill supervisor.",
                    details={"program": remote_program, "sequence": command.sequence},
                )
                return True
            command.status = event.name
            command.fault_detail = event.detail or f"PathPilot reported {event.name}."
            session.commit()
            raise CncProgramFault(f"{cycle_label} stopped: {command.fault_detail}")
        session.commit()
        connection = supervisor.status()
        if not connection.get("connected"):
            now = time.monotonic()
            outage_started = outage_started or now
            if status_report and now - last_report >= _CNC_TELEMETRY_STATUS_INTERVAL_SECONDS:
                status_report(
                    "telemetry_unavailable",
                    f"{cycle_label} remains under PathPilot control while its supervisor reconnects. No command will be retried.",
                )
                last_report = now
        elif outage_started is not None:
            if status_report:
                status_report(
                    "telemetry_restored",
                    f"The mill supervisor reconnected after {int(time.monotonic() - outage_started)} seconds.",
                )
            outage_started = None
    command.status = "uncertain"
    command.completed_at = datetime.now(timezone.utc).isoformat()
    command.fault_detail = (
        f"No terminal supervisor result arrived within {round(timeout_seconds / 3600, 1)} hours. "
        "The program was not retried and the pallet was not moved."
    )
    session.commit()
    raise problem(504, command.fault_detail)


def _run_cnc_cycle(
    settings: AppSettings,
    remote_program: str,
    *,
    session: Session | None = None,
    cycle_label: str,
    timeout_seconds: float = _CNC_LONG_CYCLE_MAXIMUM_SECONDS,
    continue_check=None,
    status_report=None,
    completion_status: dict[str, object] | None = None,
) -> bool:
    """Run one PathPilot program and wait for LinuxCNC to return to Idle."""
    if settings.robot_connection_mode == "simulated":
        time.sleep(0.25)
        return continue_check() if continue_check else True
    if settings.mill_supervisor_enabled:
        if session is None:
            raise CncPreDispatchTelemetryError("A durable database session is required for mill-supervisor dispatch.")
        return _run_cnc_cycle_via_supervisor(
            session,
            settings,
            remote_program,
            cycle_label=cycle_label,
            timeout_seconds=timeout_seconds,
            continue_check=continue_check,
            status_report=status_report,
            completion_status=completion_status,
        )
    connection = (
        settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
        settings.cnc_ssh_password, settings.cnc_timeout_seconds,
    )
    require_a = settings.cnc_require_a_axis_homed
    # PathPilot can retain interpreter_errcode after a prior operation even when
    # the interpreter is idle. Capture it before this program so it cannot be
    # mistaken for a new failure after a successful cycle.
    try:
        baseline = read_linuxcnc_cycle_state(*connection)
    except CncTelemetryError as exc:
        raise CncPreDispatchTelemetryError(
            f"PathPilot telemetry was unavailable before {cycle_label} was dispatched: {exc}"
        ) from exc
    baseline_interpreter_error = baseline.get("interpreter_error")

    if session is not None:
        emit_pre_motion_alarm(session, f"mill:{cycle_label}")
    start_result = run_linuxcnc_program(*connection, remote_program, require_a)
    if not isinstance(start_result, dict) or start_result.get("started") is not True:
        raise CncProgramFault(
            f"{cycle_label} was not started: PathPilot did not confirm that its interpreter left Idle."
        )
    diagnostics().record(
        "run_mode",
        "cnc_cycle_started",
        f"{cycle_label} started.",
        details={
            "program": remote_program,
            "loaded_program": start_result.get("loaded_program"),
            "interp_state": start_result.get("interp_state"),
        },
    )
    started = time.monotonic()
    # The launch command has already observed a non-Idle state. This also covers
    # a very short program that finishes before the first follow-up SSH poll.
    saw_running = True
    telemetry_outage_started: float | None = None
    last_outage_report = 0.0
    retry_delay = 1.0
    while time.monotonic() - started < timeout_seconds:
        if continue_check and not continue_check():
            return False
        try:
            telemetry = read_linuxcnc_cycle_state(*connection)
        except CncTelemetryError as exc:
            now = time.monotonic()
            if telemetry_outage_started is None:
                telemetry_outage_started = now
                diagnostics().record(
                    "run_mode",
                    "cnc_telemetry_outage",
                    f"{cycle_label} is still running, but PathPilot telemetry is temporarily unavailable.",
                    severity="warning",
                    details={"program": remote_program, "error": str(exc)},
                )
            outage_seconds = now - telemetry_outage_started
            if status_report and now - last_outage_report >= _CNC_TELEMETRY_STATUS_INTERVAL_SECONDS:
                status_report(
                    "telemetry_unavailable",
                    f"{cycle_label} remains unconfirmed while PathPilot telemetry reconnects "
                    f"({int(outage_seconds)} seconds). No new robot or mill action will be dispatched.",
                )
                last_outage_report = now
            # Do not retry or restart the CNC program after an observation loss.
            # The controller may still be cutting. Keep a bounded, gentle
            # read-only reconnect loop until the cycle time limit is reached.
            time.sleep(retry_delay)
            retry_delay = min(_CNC_TELEMETRY_RETRY_MAX_SECONDS, retry_delay * 2)
            continue
        if telemetry_outage_started is not None:
            outage_seconds = time.monotonic() - telemetry_outage_started
            diagnostics().record(
                "run_mode",
                "cnc_telemetry_restored",
                f"PathPilot telemetry resumed while monitoring {cycle_label}.",
                details={"program": remote_program, "outage_seconds": round(outage_seconds, 3)},
            )
            if status_report:
                status_report(
                    "telemetry_restored",
                    f"PathPilot telemetry resumed after {int(outage_seconds)} seconds. Continuing to monitor {cycle_label}.",
                )
            telemetry_outage_started = None
            retry_delay = 1.0
        fault_detail = _cnc_cycle_fault_detail(telemetry)
        interpreter_state = telemetry.get("interp_state")
        if (
            fault_detail == f"LinuxCNC interpreter error {baseline_interpreter_error}."
            and baseline_interpreter_error not in (None, 0, "0")
        ):
            fault_detail = None
        if fault_detail:
            raise CncProgramFault(f"{cycle_label} stopped: {fault_detail}")
        if interpreter_state != 1:
            saw_running = True
        elif saw_running:
            if completion_status:
                complete, detail = _mill_status_record(
                    settings,
                    str(completion_status["program"]),
                    completion_status.get("previous_signature"),
                    completion_status.get("previous_legacy_signature"),
                )
                if not complete:
                    raise CncProgramFault(f"{cycle_label} returned to Idle without a valid completion record: {detail}")
            diagnostics().record(
                "run_mode",
                "cnc_cycle_completed",
                f"{cycle_label} returned to Idle after a confirmed start.",
                details={
                    "program": remote_program,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            )
            return True
        time.sleep(_CNC_RUNNING_POLL_SECONDS)
    raise problem(
        504,
        f"{cycle_label} did not return PathPilot to Idle within {round(timeout_seconds / 3600, 1)} hours. "
        "The scheduler did not retry or move the pallet.",
    )


def _cnc_cycle_fault_detail(telemetry: dict[str, object]) -> str | None:
    if telemetry.get("estop") is True:
        return "PathPilot entered E-stop."
    if telemetry.get("enabled") is False:
        return "PathPilot became disabled."
    if (
        telemetry.get("jog_lockout_configured") is True
        and telemetry.get("jog_locked_out") is True
    ) or (
        telemetry.get("motion_stop_lockout_configured") is True
        and telemetry.get("motion_stop_locked_out") is True
    ):
        return (
            "PathPilot motion is locked out after a probe or unexpected-stop event. "
            "Restore the probe and press Reset on PathPilot to re-enable operation."
        )
    if telemetry.get("rcs_error") is True or telemetry.get("exec_error") is True:
        return "LinuxCNC reported an execution error."
    interpreter_error = telemetry.get("interpreter_error")
    if interpreter_error not in (None, 0, "0"):
        return f"LinuxCNC interpreter error {interpreter_error}."

    alarm_terms = re.compile(r"\b(alarm|error|fail(?:ed|ure)?|broken|breakage|out[- ]of[- ]tolerance)\b", re.IGNORECASE)
    messages = telemetry.get("error_messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            text = str(message.get("text") or "").strip()
            if text and (message.get("is_error") is True or alarm_terms.search(text)):
                return text[:500]
    return None


def _run_manual_mill_load_position_cycle(session: Session, settings: AppSettings) -> bool:
    remote_program = _assert_mill_load_position_program_current(settings)
    return _run_cnc_cycle(
        settings,
        remote_program,
        session=session,
        cycle_label="The mill loading-position program",
        timeout_seconds=5 * 60,
    )


def _recover_idle_mill_supervisor_for_pre_dispatch(settings: AppSettings) -> str:
    """Restart only the telemetry helper after an independent Idle check.

    This never sends a LinuxCNC command.  It is used only before a Run Mode
    program is dispatched, so an unavailable helper cannot turn a short
    network interruption into a production fault.
    """
    connection = (
        settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
        settings.cnc_ssh_password, settings.cnc_timeout_seconds,
    )
    try:
        cycle = read_linuxcnc_cycle_state(*connection)
    except CncTelemetryError as exc:
        return f"PathPilot could not be checked yet ({exc}); helper restart was not attempted."
    if cycle.get("interp_state") != 1:
        return "PathPilot is not Idle; helper restart was not attempted."
    try:
        mill_supervisor().start(settings.mill_supervisor_listen_host, settings.mill_supervisor_port)
        stop_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds, _MILL_SUPERVISOR_PID_PATH,
        )
        start_mill_supervisor_agent(
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            settings.cnc_ssh_password, settings.cnc_timeout_seconds,
            _MILL_SUPERVISOR_AGENT_PATH, _MILL_SUPERVISOR_CONFIG_PATH, _MILL_SUPERVISOR_PID_PATH,
        )
    except (CncTelemetryError, RobotFileAccessError) as exc:
        return f"The Idle helper restart could not be sent ({exc})."
    diagnostics().record(
        "mill_supervisor",
        "pre_dispatch_helper_restart",
        "Restarted the idle PathPilot telemetry helper after a prolonged pre-dispatch outage.",
        severity="warning",
    )
    return "PathPilot was confirmed Idle; its telemetry helper was restarted and is reconnecting."


def _run_mode_cnc_cycle(
    session_factory,
    remote_program: str,
    *,
    cycle_label: str,
    timeout_seconds: float = _CNC_LONG_CYCLE_MAXIMUM_SECONDS,
    run_token: str | None = None,
    completion_status: dict[str, object] | None = None,
) -> bool:

    def run_mode_is_enabled() -> bool:
        with session_factory() as check_session:
            return _run_mode_token_is_active(get_settings(check_session), run_token)

    def report_observation_state(state: str, detail: str) -> None:
        with session_factory() as report_session:
            settings = get_settings(report_session)
            if not _run_mode_token_is_active(settings, run_token):
                return
            settings.run_mode_state = state
            settings.run_mode_detail = detail
            settings.run_mode_alert = detail if state == "telemetry_unavailable" else ""
            bump(settings)
            commit_or_conflict(report_session)

    pre_dispatch_attempt = 0
    while True:
        try:
            with session_factory() as session:
                settings = get_settings(session)
                if not _run_mode_token_is_active(settings, run_token):
                    return False
                return _run_cnc_cycle(
                    settings,
                    remote_program,
                    session=session,
                    cycle_label=cycle_label,
                    timeout_seconds=timeout_seconds,
                    continue_check=run_mode_is_enabled,
                    status_report=report_observation_state,
                    completion_status=completion_status,
                )
        except CncPreDispatchTelemetryError as exc:
            pre_dispatch_attempt += 1
            if pre_dispatch_attempt <= _RUN_MODE_CNC_PRE_DISPATCH_RECOVERY_ATTEMPTS:
                delay = min(
                    _RUN_MODE_PRE_DISPATCH_RECOVERY_MAX_SECONDS,
                    2 ** (pre_dispatch_attempt - 1),
                )
                recovery_note = ""
                with session_factory() as session:
                    settings = get_settings(session)
                    if not _run_mode_token_is_active(settings, run_token):
                        return False
                    if (
                        pre_dispatch_attempt >= _RUN_MODE_CNC_PRE_DISPATCH_HELPER_RESTART_ATTEMPT
                        and (pre_dispatch_attempt - _RUN_MODE_CNC_PRE_DISPATCH_HELPER_RESTART_ATTEMPT)
                        % _RUN_MODE_CNC_PRE_DISPATCH_HELPER_RESTART_INTERVAL == 0
                    ):
                        recovery_note = " " + _recover_idle_mill_supervisor_for_pre_dispatch(settings)
                    _set_run_mode_status(
                        session,
                        "recovering_cnc_telemetry",
                        f"PathPilot telemetry is unavailable before {cycle_label}. "
                        f"No program was sent. Retrying automatically in {delay} seconds "
                        f"({pre_dispatch_attempt}/{_RUN_MODE_CNC_PRE_DISPATCH_RECOVERY_ATTEMPTS}).{recovery_note}",
                    )
                diagnostics().record(
                    "run_mode",
                    "cnc_pre_dispatch_retry",
                    "Retrying PathPilot telemetry before a CNC program dispatch.",
                    severity="warning",
                    details={
                        "program": remote_program,
                        "attempt": pre_dispatch_attempt,
                        "delay_seconds": delay,
                        "error": str(exc),
                        "recovery_note": recovery_note.strip() or None,
                    },
                )
                time.sleep(delay)
                continue
            detail = (
                f"{exc} Automatic PathPilot reconnect attempts were exhausted before any program was sent."
            )
            retry_action = "retry_cnc_preflight"
        except CncProgramFault as exc:
            detail = str(exc)
            retry_action = "retry_cnc_program"
        except CncTelemetryError as exc:
            # A start request may have reached PathPilot before the SSH response
            # was lost. Retrying here could run the same machining program twice.
            _finish_run_mode(
                session_factory,
                "faulted",
                f"{cycle_label} has an uncertain PathPilot command result: {exc}. "
                "The queue stopped without retrying the program. Inspect PathPilot and reconcile before continuing.",
                run_token,
            )
            return False
        with session_factory() as session:
            settings = get_settings(session)
            if not _run_mode_token_is_active(settings, run_token):
                return False
            settings.machine_state = "error"
            settings.run_mode_alert = detail
            bump(settings)
            commit_or_conflict(session)
        diagnostics().record(
            "run_mode",
            "cnc_fault",
            detail,
            severity="error",
            details={"program": remote_program, "cycle_label": cycle_label},
        )
        if not _await_run_mode_action(
            session_factory,
            retry_action,
            (
                f"{detail} The queue is paused and the pallet has not been moved. "
                + (
                    "Inspect the mill and clear its alarm, then retry this same program, or stop Run Mode and leave the pallet in place."
                    if retry_action == "retry_cnc_program"
                    else "Check the PathPilot connection, then retry the connection check or stop Run Mode and leave the pallet in place."
                )
            ),
            force_confirmation=True,
            run_token=run_token,
        ):
            return False


def _run_mode_load_position_cycle(session_factory, run_token: str | None = None) -> bool:
    with session_factory() as session:
        settings = get_settings(session)
        if not _run_mode_token_is_active(settings, run_token):
            return False
        if settings.robot_connection_mode == "simulated":
            remote_program = str(_mill_program_root(settings) / MILL_LOAD_POSITION_PROGRAM_NAME)
        else:
            remote_program = _assert_mill_load_position_program_current(settings)
    return _run_mode_cnc_cycle(
        session_factory,
        remote_program,
        cycle_label="The mill loading-position program",
        timeout_seconds=5 * 60,
        run_token=run_token,
    )


def _run_mode_start_robot_transfer(
    session_factory,
    *,
    operation: str,
    pallet_id: str,
    pallet_name: str,
    return_slot: int,
    run_token: str | None,
) -> tuple[bool, str | None]:
    """Dispatch one robot transfer, pausing safely on pre-dispatch failures."""
    action_label = "load into" if operation == "load" else "unload from"
    automatic_attempt = 0
    while True:
        try:
            with session_factory() as session:
                settings = get_settings(session)
                if not _run_mode_token_is_active(settings, run_token):
                    return False, None
                _set_run_mode_status(
                    session,
                    "loading" if operation == "load" else "unloading",
                    f"Mongo is preparing to {action_label} the mill for {pallet_name}.",
                    pallet_id=pallet_id,
                    return_slot=return_slot,
                )
                settings = get_settings(session)
                motion_id = start_mill_pallet_transfer(
                    session,
                    StartMillPalletTransfer(
                        expected_revision=settings.revision,
                        operation=operation,
                        pallet_id=pallet_id,
                        pool_slot_number=return_slot if operation == "unload" else None,
                    ),
                    automated=True,
                )
                return True, motion_id
        except HTTPException as exc:
            detail = str(exc.detail)
            transient_transport_failure = _is_transient_robot_pre_dispatch_detail(detail)
            automatic_attempt += 1
            if (
                transient_transport_failure
                and automatic_attempt <= _RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS
            ):
                delay = min(
                    _RUN_MODE_PRE_DISPATCH_RECOVERY_MAX_SECONDS,
                    2 ** (automatic_attempt - 1),
                )
                diagnostics().record(
                    "run_mode",
                    "robot_pre_dispatch_retry",
                    "Retrying robot telemetry before a pallet transfer dispatch.",
                    severity="warning",
                    details={
                        "operation": operation,
                        "pallet_id": pallet_id,
                        "attempt": automatic_attempt,
                        "delay_seconds": delay,
                        "error": detail,
                    },
                )
                with session_factory() as session:
                    settings = get_settings(session)
                    if not _run_mode_token_is_active(settings, run_token):
                        return False, None
                    _set_run_mode_status(
                        session,
                        "recovering_robot_telemetry",
                        f"Mongo telemetry is unavailable before {operation} for {pallet_name}. "
                        f"No robot motion was dispatched. Retrying automatically in {delay} seconds "
                        f"({automatic_attempt}/{_RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS}).",
                        pallet_id=pallet_id,
                        return_slot=return_slot,
                    )
                try:
                    reset_robot_connections()
                except RobotTelemetryError:
                    pass
                time.sleep(delay)
                continue
            diagnostics().record(
                "run_mode",
                "robot_transfer_pre_dispatch_blocked",
                detail,
                severity="warning",
                details={
                    "operation": operation,
                    "pallet_id": pallet_id,
                    "pallet_name": pallet_name,
                    "return_slot": return_slot,
                },
            )
            with session_factory() as session:
                settings = get_settings(session)
                if not _run_mode_token_is_active(settings, run_token):
                    return False, None
                settings.run_mode_alert = detail
                bump(settings)
                commit_or_conflict(session)
            if not _await_run_mode_action(
                session_factory,
                "retry_robot_transfer",
                f"{detail} Automatic reconnect attempts were exhausted. No robot movement was dispatched and the mill positioning step is already complete. "
                f"Retry only the robot {operation} transfer, or stop Run Mode and leave the pallet in its current location.",
                force_confirmation=True,
                run_token=run_token,
            ):
                return False, None
            try:
                reset_robot_connections()
            except RobotTelemetryError:
                # The next preflight reports the current connection detail and
                # returns to this same operator-controlled recovery point.
                pass
            time.sleep(0.5)


def _run_mode_machine_cycle(session_factory, pallet_id: str, run_token: str | None = None) -> bool:
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    program_path = ""
    with session_factory() as session:
        settings = get_settings(session)
        if not _run_mode_token_is_active(settings, run_token):
            return False
        pallet = session.get(Pallet, pallet_id)
        if not pallet or pallet.location != "machine" or not pallet.program_path:
            raise problem(409, "The run-mode pallet is no longer ready in the mill.")
        remote_program = _run_mode_program_path(
            settings,
            pallet.program_path,
            set(json.loads(settings.mill_program_extensions)),
        )
        completion_status = None
        if settings.robot_connection_mode == "physical":
            metadata = read_assigned_program_metadata(settings, pallet.program_path)
            expected_status_path = _mill_status_file_path(settings)
            if metadata.get("mill_status_file") != expected_status_path:
                raise problem(
                    409,
                    "The assigned program does not declare the configured mill completion status file. Repost it with the updated Fusion post.",
                )
            status_before = remote_file_signature(
                path=expected_status_path,
                **_mill_status_file_connection(settings),
            )
            legacy_status_before = remote_file_signature(
                path=_legacy_mill_status_file_path(settings, expected_status_path),
                **_mill_status_file_connection(settings),
            )
            completion_status = {
                "program": remote_program,
                "previous_signature": status_before,
                "previous_legacy_signature": legacy_status_before,
            }
        pallet_name = pallet.name
        program_path = pallet.program_path
        # This timestamp drives the live dashboard countdown. It is set only
        # after the pallet is in the mill and Run Mode has entered machining.
        settings.run_mode_program_started_at = started_at.isoformat()
        bump(settings)
        commit_or_conflict(session)
    completed = False
    try:
        completed = _run_mode_cnc_cycle(
            session_factory,
            remote_program,
            cycle_label=f"The assigned program for {pallet_name}",
            run_token=run_token,
            completion_status=completion_status,
        )
        if completed:
            duration_seconds = max(1, round(time.monotonic() - started_monotonic))
            with session_factory() as session:
                session.add(ProgramRuntime(
                    id=str(uuid4()),
                    program_path=program_path,
                    pallet_id=pallet_id,
                    started_at=started_at.isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=duration_seconds,
                ))
                diagnostics().record(
                    "run_mode",
                    "program_runtime_recorded",
                    f"Recorded {duration_seconds} seconds for {program_path}.",
                    details={"program": program_path, "pallet_id": pallet_id, "duration_seconds": duration_seconds},
                )
                commit_or_conflict(session)
        return completed
    finally:
        with session_factory() as session:
            settings = get_settings(session)
            if settings.run_mode_program_started_at == started_at.isoformat():
                settings.run_mode_program_started_at = None
                bump(settings)
                commit_or_conflict(session)


def dismiss_run_mode_alert(session: Session) -> None:
    settings = get_settings(session)
    if not settings.run_mode_alert:
        return
    settings.run_mode_alert = ""
    settings.run_mode_program_started_at = None
    bump(settings)
    commit_or_conflict(session)


def clear_stale_run_mode_status(session: Session, expected_revision: int) -> None:
    """Clear a terminal Run Mode banner without changing production or motion state."""
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before clearing its status.")
    if settings.run_mode_state not in {"faulted", "interrupted"}:
        raise problem(409, "There is no stale Run Mode fault or interruption to clear.")
    previous_state = settings.run_mode_state
    previous_detail = settings.run_mode_detail
    settings.run_mode_state = "idle"
    settings.run_mode_detail = ""
    settings.run_mode_current_pallet_id = None
    settings.run_mode_return_slot = None
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    bump(settings)
    commit_or_conflict(session)
    diagnostics().record(
        "run_mode",
        "status_cleared",
        "Operator cleared a stale Run Mode status banner.",
        details={"previous_state": previous_state, "previous_detail": previous_detail},
    )
    refresh_stack_light(session)


def start_run_mode_recovery(session: Session, payload) -> str:
    """Resume an interrupted unload without guessing or repeating completed work."""
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.run_mode_enabled:
        raise problem(409, "Run Mode is already active.")
    if settings.run_mode_state not in {"faulted", "interrupted", "stopped"}:
        raise problem(409, "Run Mode is not awaiting recovery.")
    if _locked_motion(session):
        raise problem(409, "Resolve the active robot-motion record before recovering Run Mode.")
    pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
    if not pallet:
        raise problem(409, "Run recovery requires a pallet currently marked in the mill.")
    return_slot = pallet.return_pool_slot_number
    if not return_slot or return_slot > settings.pool_slot_count:
        raise problem(409, "The pallet does not have a valid reserved return pool position.")
    occupied = session.scalar(select(Pallet).where(
        Pallet.location == "pool",
        Pallet.pool_slot_number == return_slot,
        Pallet.id != pallet.id,
    ))
    if occupied:
        raise problem(409, f"Pool position {return_slot:02d} is occupied by {occupied.name}.")

    run_token = str(uuid4())
    settings.run_mode_enabled = True
    settings.run_mode_start_request_id = run_token
    settings.run_mode_state = "recovery_requested"
    settings.run_mode_detail = (
        f"Recovery requested for {pallet.name}: "
        + (
            "retrying only the robot unload because the mill is already at its loading position."
            if payload.strategy == "retry_robot_only"
            else "running the assigned mill program again before unloading."
            if payload.strategy == "rerun_assigned_program"
            else "marking the pallet complete after operator confirmation, then moving the mill to its loading position and unloading."
            if payload.strategy == "manual_complete_and_unload"
            else "repositioning the mill, then retrying the robot unload."
        )
    )
    settings.run_mode_current_pallet_id = pallet.id
    settings.run_mode_return_slot = return_slot
    settings.run_mode_pending_action = ""
    settings.run_mode_confirmation_token = ""
    settings.run_mode_confirmation_granted = False
    settings.run_mode_alert = ""
    bump(settings)
    commit_or_conflict(session)
    return run_token


def execute_run_mode_recovery(session_factory, run_token: str, strategy: str) -> None:
    """Recover a pallet left in the mill, then continue the remaining queue."""
    try:
        with session_factory() as session:
            settings = get_settings(session)
            if not _run_mode_token_is_active(settings, run_token):
                return
            pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
            if not pallet or not pallet.return_pool_slot_number:
                raise problem(409, "The pallet recovery state changed before recovery began.")
            pallet_id = pallet.id
            pallet_name = pallet.name
            return_slot = pallet.return_pool_slot_number

        if strategy == "rerun_assigned_program":
            if not _run_mode_machine_cycle(session_factory, pallet_id, run_token):
                return

        if strategy in {"reposition_and_retry", "rerun_assigned_program", "manual_complete_and_unload"}:
            with session_factory() as session:
                _set_run_mode_status(
                    session,
                    "positioning_mill",
                    f"Recovery is moving the mill to its loading position before unloading {pallet_name}.",
                    pallet_id=pallet_id,
                    return_slot=return_slot,
                )
            if not _run_mode_load_position_cycle(session_factory, run_token):
                return

        transfer_ready, motion_id = _run_mode_start_robot_transfer(
            session_factory,
            operation="unload",
            pallet_id=pallet_id,
            pallet_name=pallet_name,
            return_slot=return_slot,
            run_token=run_token,
        )
        if not transfer_ready:
            return
        if not _run_mode_motion_succeeded(session_factory, motion_id):
            raise problem(409, f"Mongo could not recover {pallet_name}. Reconcile the robot-motion fault before continuing.")

        with session_factory() as session:
            settings = get_settings(session)
            pallet = session.get(Pallet, pallet_id)
            if pallet:
                mark_pallet_program_completed(session, pallet)
            settings.run_mode_current_pallet_id = None
            settings.run_mode_return_slot = None
            remaining = session.scalar(select(Pallet.id).where(Pallet.queue_position.is_not(None)))
            if remaining:
                settings.run_mode_state = "advancing"
                settings.run_mode_detail = f"Recovered {pallet_name}. Continuing the remaining production queue."
                bump(settings)
                commit_or_conflict(session)
            else:
                commit_or_conflict(session)
                _finish_run_mode(session_factory, "complete", f"Recovered {pallet_name}; all queued work is complete.", run_token)
                return
        execute_run_mode(session_factory, run_token)
    except HTTPException as exc:
        _finish_run_mode(session_factory, "faulted", str(exc.detail), run_token)
    except (CncTelemetryError, RobotDashboardError, RobotFileAccessError) as exc:
        _finish_run_mode(session_factory, "faulted", str(exc), run_token)
    except Exception as exc:  # pragma: no cover - defensive coordinator boundary
        _finish_run_mode(session_factory, "faulted", f"Unexpected run-recovery failure: {exc}", run_token)


def _prepare_run_mode(session_factory, run_token: str | None) -> bool:
    """Perform slow controller/file checks after the cancellable start request is committed."""
    recovery_attempt = 0
    while True:
        with session_factory() as session:
            settings = get_settings(session)
            if not _run_mode_token_is_active(settings, run_token):
                return False
            queue = session.scalars(
                select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
            ).all()
            machine_pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
            machine_action = settings.run_mode_loaded_machine_action
            if not queue and not (machine_pallet and machine_action):
                raise problem(409, "The production queue became empty while Run Mode was starting.")
            if settings.robot_connection_mode != "physical":
                break
            try:
                _assert_motion_ready(session, settings)
                cnc_state = read_linuxcnc_cycle_state(
                    settings.cnc_host.strip(), settings.cnc_ssh_port,
                    settings.cnc_ssh_username, settings.cnc_ssh_password,
                    settings.cnc_timeout_seconds,
                )
            except HTTPException as exc:
                detail = str(exc.detail)
                transient = _is_transient_robot_pre_dispatch_detail(detail)
            except CncTelemetryError as exc:
                detail = f"Live CNC telemetry is unavailable before Run Mode starts: {exc}"
                transient = True
            else:
                if cnc_state.get("estop"):
                    raise problem(409, "PathPilot is in E-stop. Run mode was not started.")
                if not cnc_state.get("enabled"):
                    raise problem(409, "PathPilot is not enabled. Run mode was not started.")
                if cnc_state.get("interp_state") != 1:
                    raise problem(409, "PathPilot is not idle. Stop or finish its active program before starting run mode.")
                for pallet in queue:
                    _assert_pool_motion_position_configured(settings, pallet.pool_slot_number or 0)
                checked_programs = [*queue, *([machine_pallet] if machine_action == "run_machine_program" and machine_pallet else [])]
                _assert_run_mode_files_ready(settings, checked_programs)
                break

            recovery_attempt += 1
            if not transient or recovery_attempt > _RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS:
                raise problem(409, f"{detail} Run Mode was not started.")
            delay = min(
                _RUN_MODE_PRE_DISPATCH_RECOVERY_MAX_SECONDS,
                2 ** (recovery_attempt - 1),
            )
            _set_run_mode_status(
                session,
                "recovering_startup_telemetry",
                f"Controller telemetry is unavailable before Run Mode starts. No program or robot movement was sent. "
                f"Retrying automatically in {delay} seconds ({recovery_attempt}/{_RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS}).",
            )
            diagnostics().record(
                "run_mode",
                "startup_telemetry_retry",
                "Retrying controller telemetry before Run Mode dispatch.",
                severity="warning",
                details={"attempt": recovery_attempt, "delay_seconds": delay, "error": detail},
            )
        try:
            reset_robot_connections()
        except RobotTelemetryError:
            pass
        time.sleep(delay)

    with session_factory() as session:
        settings = get_settings(session)
        if not _run_mode_token_is_active(settings, run_token):
            return False
        _set_run_mode_status(session, "starting", "Controller checks passed. Preparing the first queued pallet.")
    return True


def _finalize_stop_request(session_factory, run_token: str | None) -> None:
    if run_token is None:
        return
    with session_factory() as session:
        settings = get_settings(session)
        if settings.run_mode_start_request_id != run_token or settings.run_mode_state != "stopping":
            return
        settings.run_mode_state = "stopped"
        settings.run_mode_detail = "Stop override acknowledged. No further automated action will run."
        settings.run_mode_start_request_id = ""
        bump(settings)
        commit_or_conflict(session)


def _run_mode_handle_loaded_machine_pallet(session_factory, run_token: str | None) -> bool:
    """Complete the explicit operator-selected disposition of a pallet already in the mill."""
    with session_factory() as session:
        settings = get_settings(session)
        if not _run_mode_token_is_active(settings, run_token):
            return False
        action = settings.run_mode_loaded_machine_action
        pallet = session.scalar(select(Pallet).where(Pallet.location == "machine"))
        if not action or not pallet:
            return True
        if action not in {"unload_then_queue", "run_machine_program"} or not pallet.return_pool_slot_number:
            raise problem(409, "The loaded-pallet start choice is no longer valid. Stop Run Mode and reconcile the pallet state.")
        pallet_id, pallet_name, return_slot = pallet.id, pallet.name, pallet.return_pool_slot_number

    if action == "run_machine_program":
        if not _await_run_mode_action(
            session_factory,
            "machining",
            f"Run {pallet_name}'s assigned mill program before returning it to Pool {return_slot:02d}?",
            run_token=run_token,
        ):
            return False
        if not _run_mode_machine_cycle(session_factory, pallet_id, run_token):
            return False
        if not _wait_for_manual_robot_pause(session_factory, run_token):
            return False

    if not _await_run_mode_action(
        session_factory,
        "unloading",
        f"Move the mill to its loading position and return {pallet_name} to Pool {return_slot:02d}?",
        run_token=run_token,
    ):
        return False
    with session_factory() as session:
        _set_run_mode_status(
            session,
            "positioning_mill",
            f"Moving the mill to its loading position before returning {pallet_name} to Pool {return_slot:02d}.",
            pallet_id=pallet_id,
            return_slot=return_slot,
        )
    if not _run_mode_load_position_cycle(session_factory, run_token):
        return False
    transfer_ready, motion_id = _run_mode_start_robot_transfer(
        session_factory,
        operation="unload",
        pallet_id=pallet_id,
        pallet_name=pallet_name,
        return_slot=return_slot,
        run_token=run_token,
    )
    if not transfer_ready:
        return False
    if not _run_mode_motion_succeeded(session_factory, motion_id):
        raise problem(409, f"Mongo could not return {pallet_name}. Resolve the robot-motion fault before continuing Run Mode.")
    with session_factory() as session:
        settings = get_settings(session)
        pallet = session.get(Pallet, pallet_id)
        if action == "run_machine_program" and pallet:
            mark_pallet_program_completed(session, pallet)
        settings.run_mode_loaded_machine_action = ""
        settings.run_mode_current_pallet_id = None
        settings.run_mode_return_slot = None
        settings.run_mode_state = "advancing"
        settings.run_mode_detail = f"{pallet_name} was returned to the pool. Continuing the production queue."
        bump(settings)
        commit_or_conflict(session)
    return True


def execute_run_mode(session_factory, run_token: str | None = None) -> None:
    """Process queued pallets serially, stopping on the first uncertain state."""
    try:
        if not _prepare_run_mode(session_factory, run_token):
            return
        if not _run_mode_handle_loaded_machine_pallet(session_factory, run_token):
            return
        while True:
            with session_factory() as session:
                settings = get_settings(session)
                if not _run_mode_token_is_active(settings, run_token):
                    return
                pallet = session.scalar(
                    select(Pallet).where(Pallet.queue_position.is_not(None)).order_by(Pallet.queue_position)
                )
                if not pallet:
                    settings.run_mode_state = "idle_waiting_queue"
                    settings.run_mode_detail = "Queue is empty. Run Mode remains armed and will resume when a ready pallet is added."
                    settings.run_mode_current_pallet_id = None
                    settings.run_mode_return_slot = None
                    bump(settings)
                    commit_or_conflict(session)
                    diagnostics().record(
                        "run_mode",
                        "idle_waiting_queue",
                        "Run Mode completed its queue and remains armed for newly queued pallets.",
                    )
                    _send_push_notification(
                        settings,
                        "queue_empty",
                        "MPS: the production queue is complete and waiting for the next pallet.",
                    )
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
                session_factory, "loading",
                f"Move the mill to its G53 loading position, then load {pallet_name} from Pool {return_slot:02d}?",
                run_token=run_token,
            ):
                return
            with session_factory() as session:
                _set_run_mode_status(
                    session, "positioning_mill",
                    f"Moving the mill to its G53 loading position before loading {pallet_name}.",
                    pallet_id=pallet_id, return_slot=return_slot,
                )
            if not _run_mode_load_position_cycle(session_factory, run_token):
                return
            transfer_ready, motion_id = _run_mode_start_robot_transfer(
                session_factory,
                operation="load",
                pallet_id=pallet_id,
                pallet_name=pallet_name,
                return_slot=return_slot,
                run_token=run_token,
            )
            if not transfer_ready:
                return
            if not _run_mode_motion_succeeded(session_factory, motion_id):
                raise problem(409, f"Mongo could not load {pallet_name}. Resolve the robot-motion fault before restarting run mode.")

            if not _await_run_mode_action(
                session_factory, "machining", f"Start {pallet_name}'s assigned mill program?",
                run_token=run_token,
            ):
                return
            if not _run_mode_machine_cycle(session_factory, pallet_id, run_token):
                return

            if not _wait_for_manual_robot_pause(session_factory, run_token):
                return

            if not _await_run_mode_action(
                session_factory, "unloading",
                f"Return the mill to its G53 loading position, then unload {pallet_name} to Pool {return_slot:02d}?",
                run_token=run_token,
            ):
                return
            with session_factory() as session:
                _set_run_mode_status(
                    session, "positioning_mill",
                    f"Moving the mill to its G53 loading position before unloading {pallet_name}.",
                    pallet_id=pallet_id, return_slot=return_slot,
                )
            if not _run_mode_load_position_cycle(session_factory, run_token):
                return
            transfer_ready, motion_id = _run_mode_start_robot_transfer(
                session_factory,
                operation="unload",
                pallet_id=pallet_id,
                pallet_name=pallet_name,
                return_slot=return_slot,
                run_token=run_token,
            )
            if not transfer_ready:
                return
            if not _run_mode_motion_succeeded(session_factory, motion_id):
                raise problem(409, f"Mongo could not return {pallet_name}. Resolve the robot-motion fault before restarting run mode.")

            with session_factory() as session:
                settings = get_settings(session)
                pallet = session.get(Pallet, pallet_id)
                if pallet:
                    mark_pallet_program_completed(session, pallet)
                settings.run_mode_current_pallet_id = None
                settings.run_mode_return_slot = None
                settings.run_mode_state = "advancing"
                settings.run_mode_detail = f"{pallet_name} completed. Advancing to the next queued pallet."
                bump(settings)
                commit_or_conflict(session)
    except HTTPException as exc:
        _finish_run_mode(session_factory, "faulted", str(exc.detail), run_token)
    except (CncTelemetryError, RobotDashboardError, RobotFileAccessError) as exc:
        _finish_run_mode(session_factory, "faulted", str(exc), run_token)
    except Exception as exc:  # pragma: no cover - defensive coordinator boundary
        _finish_run_mode(session_factory, "faulted", f"Unexpected run-mode failure: {exc}", run_token)
    finally:
        _finalize_stop_request(session_factory, run_token)


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
        "load_mill": {"robot_held", "machine"} | ({"source_pool"} if motion.source_slot else set()),
        "unload_mill": {"machine", "robot_held", "destination_pool"},
    }.get(motion.operation, set())
    if payload.resolution not in allowed:
        raise problem(422, "That recovery state is not valid for this movement.")
    if payload.resolution == "machine":
        occupant = session.scalar(select(Pallet).where(Pallet.location == "machine", Pallet.id != pallet.id))
        if occupant:
            raise problem(409, "Another pallet is already marked as being in the mill.")
        pallet.location, pallet.pool_slot_number = "machine", None
        pallet.return_pool_slot_number = (
            pallet.return_pool_slot_number or motion.source_slot or motion.destination_slot
        )
        settings.machine_state = "running"
    elif payload.resolution == "source_pool":
        pallet.location, pallet.pool_slot_number = "pool", motion.source_slot
        pallet.return_pool_slot_number = None
    elif payload.resolution == "destination_pool":
        occupant = session.scalar(select(Pallet).where(Pallet.location == "pool", Pallet.pool_slot_number == motion.destination_slot, Pallet.id != pallet.id))
        if occupant:
            raise problem(409, "The destination pool position is now occupied.")
        pallet.location, pallet.pool_slot_number = "pool", motion.destination_slot
        pallet.return_pool_slot_number = None
    else:
        held = session.scalar(select(Pallet).where(Pallet.location == "robot_held", Pallet.id != pallet.id))
        if held:
            raise problem(409, "Another pallet is already marked Robot-held.")
        pallet.location, pallet.pool_slot_number = "robot_held", None
        pallet.return_pool_slot_number = (
            pallet.return_pool_slot_number or motion.source_slot or motion.destination_slot
        )
    motion.status = "reconciled"
    motion.completed_at = datetime.now(timezone.utc).isoformat()
    motion.failure_detail = f"{motion.failure_detail or 'Robot movement fault.'} Reconciled as {payload.resolution}."
    bump(settings)
    commit_or_conflict(session)

    # A 104 latch is emitted only after the robot's atomic dispatcher returned.
    # The operator just confirmed the physical result, so clear only this exact
    # matching application latch. It never moves the robot or restarts Run Mode.
    link_loss_command = session.scalar(
        select(RobotSupervisorCommand)
        .where(
            RobotSupervisorCommand.robot_motion_id == motion_id,
            RobotSupervisorCommand.result_code == FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION,
        )
        .order_by(RobotSupervisorCommand.sequence.desc())
    )
    supervisor_status = robot_supervisor().status()
    if not link_loss_command or not (
        supervisor_status.get("connected")
        and supervisor_status.get("latched")
        and supervisor_status.get("robot_last_sequence") == link_loss_command.sequence
    ):
        return
    try:
        clear = _new_supervisor_command(
            session,
            motion=None,
            operation="clear_latch",
            opcode=OP_CLEAR_LATCH,
        )
        outcome, detail = _dispatch_supervisor_command(
            session,
            clear,
            max(5.0, settings.robot_timeout_seconds * 4),
            allow_pre_dispatch_fallback=False,
        )
        if outcome != "completed":
            diagnostics().record(
                "robot_supervisor",
                "recovery_latch_clear_pending",
                "Pallet location was reconciled, but the matching supervisor latch could not be cleared.",
                severity="warning",
                details={"motion_id": motion_id, "sequence": link_loss_command.sequence, "detail": detail},
            )
    except (HTTPException, RobotDashboardError, RobotFileAccessError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        diagnostics().record(
            "robot_supervisor",
            "recovery_latch_clear_error",
            "Pallet location was reconciled, but automatic supervisor latch clearing failed.",
            severity="warning",
            details={"motion_id": motion_id, "sequence": link_loss_command.sequence, "detail": detail},
        )


def interrupt_active_pallet_motion(session: Session) -> None:
    motion = session.scalar(select(RobotMotion).where(RobotMotion.status.in_(("requested", "running"))))
    if not motion:
        return
    motion.status = "faulted"
    motion.completed_at = datetime.now(timezone.utc).isoformat()
    motion.failure_detail = "Backend restarted while this motion was active. Inspect the cell and reconcile the pallet location."
    bump(get_settings(session))
    commit_or_conflict(session)


def assert_system_relaunch_ready(session: Session) -> None:
    if _active_reliability_run(session):
        raise problem(409, "Cancel or wait for the queue reliability test before relaunching the backend.")
    motion = session.scalar(
        select(RobotMotion).where(RobotMotion.status.in_(("requested", "running")))
    )
    if motion:
        raise problem(409, "Wait for the active robot movement to finish before relaunching the backend.")
    if get_settings(session).run_mode_enabled:
        raise problem(409, "Stop production run mode before relaunching the backend.")


def assert_robot_file_mutation_ready(session: Session) -> None:
    """Prevent controller program changes while an automated workflow owns the robot."""
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before changing controller files.")
    _assert_reliability_inactive(session)
    if get_settings(session).run_mode_enabled:
        raise problem(409, "Stop Run Mode before changing controller files.")


def rebuild_pallet_motion_scripts(session: Session) -> dict:
    settings = get_settings(session)
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before rebuilding scripts.")
    _assert_reliability_inactive(session)
    if settings.run_mode_enabled and not settings.run_mode_manual_robot_pause:
        raise problem(409, "Stop Run Mode before rebuilding robot scripts.")
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
    if _joint_waypoint(generation.get("safe_pre_waypoint"), "shared safe waypoint") is None:
        raise problem(422, "Capture and save the shared joint-space safe waypoint before rebuilding scripts.")
    if _robot_waypoint(generation.get("mill_pre_entry_waypoint"), "Mill pre-entry") is None:
        raise problem(422, "Capture and save the robot mill pre-entry waypoint before rebuilding scripts.")
    intermediate_poses = generation.get("intermediate_safe_poses", [])
    if not isinstance(intermediate_poses, list) or any(_joint_waypoint(item, "intermediate safe pose") is None for item in intermediate_poses):
        raise problem(422, "Every intermediate safe pose must contain a name and six finite joint positions.")
    intermediate_names = [str(item.get("name", "")).casefold() for item in intermediate_poses if isinstance(item, dict)]
    if len(intermediate_names) != len(intermediate_poses) or not all(intermediate_names) or len(intermediate_names) != len(set(intermediate_names)):
        raise problem(422, "Intermediate safe-pose names must be unique.")
    if any(
        not isinstance(item.get("pool_slots"), list)
        or any(not isinstance(slot, int) or slot < 1 or slot > settings.pool_slot_count for slot in item["pool_slots"])
        for item in intermediate_poses
    ):
        raise problem(422, "Intermediate safe-pose assignments must refer to configured pool positions.")

    locations = pallet_location_positions(settings)
    files: dict[str, str] = {}
    mappings: list[dict] = []
    for pool in locations["pool_locations"]:
        slot = pool["slot"]
        pick_name = f"pick_pool_{slot:03d}.script"
        put_name = f"put_pool_{slot:03d}.script"
        reliability_name = f"reliability_pool_{slot:03d}.script"
        files[pick_name] = build_pallet_motion_script(
            function_name=f"mps_pick_pool_{slot:03d}", operation="pick", position=pool, generation=generation,
        )
        files[put_name] = build_pallet_motion_script(
            function_name=f"mps_put_pool_{slot:03d}", operation="put", position=pool, generation=generation,
        )
        files[reliability_name] = build_reliability_motion_script(
            function_name=f"mps_reliability_pool_{slot:03d}",
            position=pool,
            staging_pose=_robot_waypoint(generation.get("mill_pre_entry_waypoint"), "Mill pre-entry"),
            generation=generation,
        )
        mappings.append({
            "slot": slot,
            "pick_program": pick_name,
            "put_program": put_name,
            "reliability_program": reliability_name,
        })
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

    mill_pose = locations.get("robot_mill_load_unload")
    mill_pre_entry = _robot_waypoint(generation.get("mill_pre_entry_waypoint"), "Mill pre-entry")
    mill_entry_exit = locations.get("robot_mill_safe_entry_exit")
    if not isinstance(mill_pose, dict) or mill_pre_entry is None or not isinstance(mill_entry_exit, dict):
        raise problem(422, "Configure all robot mill poses before rebuilding the supervisor.")
    files["mongo_supervisor.script"] = build_robot_supervisor_script(
        backend_hostname=settings.robot_supervisor_hostname,
        backend_port=settings.robot_supervisor_port,
        heartbeat_seconds=settings.robot_supervisor_heartbeat_seconds,
        telemetry_hz=settings.robot_supervisor_telemetry_hz,
        reconnect_limit_seconds=settings.robot_supervisor_reconnect_limit_seconds,
        pool_locations=locations["pool_locations"],
        mill_pose=mill_pose,
        mill_pre_entry_pose=mill_pre_entry,
        mill_entry_exit_pose=mill_entry_exit,
        generation=generation,
    )

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
                "reliability_program": remote_paths[item["reliability_program"]],
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
) -> str | None:
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    _assert_queue_edit_allowed(session, {pallet_id})
    assert_pallet_manageable_during_run(settings, pallet)
    if pallet.location == "machine":
        pool_slot = best_pool_return_slot(session, settings, pallet)
        pallet.location = "pool"
        pallet.pool_slot_number = pool_slot
        pallet.return_pool_slot_number = None
    elif pallet.location != "pool":
        raise problem(
            409,
            "A stored pallet must be returned to the Pool before it can be queued.",
        )
    if payload.convert_completed_to_raw and pallet.content_status == "complete_parts":
        pallet.content_status = "raw_stock"

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
    return resume_armed_run_mode(session)


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
    _assert_queue_edit_allowed(session, {pallet_id})
    assert_pallet_manageable_during_run(settings, pallet)
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
    _assert_reliability_inactive(session)
    queue = session.scalars(
        select(Pallet).where(Pallet.queue_position.is_not(None))
    ).all()
    if len(payload.pallet_ids) != len(set(payload.pallet_ids)):
        raise problem(422, "Queue contains duplicate pallet IDs.")
    if set(payload.pallet_ids) != {item.id for item in queue}:
        raise problem(422, "Queue reorder must contain every queued pallet exactly once.")
    motion = _locked_motion(session)
    if motion:
        motion_pallet = next((item for item in queue if item.id == motion.pallet_id), None)
        if (
            motion_pallet is not None
            and motion_pallet.queue_position is not None
            and payload.pallet_ids[motion_pallet.queue_position] != motion.pallet_id
        ):
            raise problem(
                409,
                "The pallet assigned to the active robot movement must keep its current Queue position until that movement finishes.",
            )
    if settings.run_mode_enabled:
        for item in queue:
            if item.location == "machine" and payload.pallet_ids[item.queue_position] != item.id:
                raise problem(409, "The pallet in the mill must keep its current queue position during Run Mode.")
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
    _assert_no_locked_motion(session)
    pallet = session.get(Pallet, pallet_id)
    if not pallet:
        raise problem(404, "Pallet not found.")
    assert_pallet_manageable_during_run(settings, pallet)
    was_queued = pallet.queue_position is not None
    session.delete(pallet)
    session.flush()
    if was_queued:
        compact_queue(session)
    bump(settings)
    commit_or_conflict(session)


def reconcile_programs(session: Session, settings: AppSettings, *, force_scan: bool = False) -> list[str]:
    programs, _ = available_programs(settings, force=force_scan)
    available = set(programs)
    cleared: list[str] = []
    assigned = session.scalars(
        select(Pallet).where(Pallet.program_path.is_not(None))
    ).all()
    for pallet in assigned:
        if pallet.program_path not in available:
            pallet.program_path = None
            _clear_pallet_program_metadata(pallet)
            cleared.append(pallet.name)
    return cleared


def update_settings(session: Session, payload: SettingsUpdate) -> list[str]:
    _assert_reliability_inactive(session)
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    assert_settings_safe_during_run(settings, payload)
    program_catalog_changed = (
        payload.source_folder is not None or payload.program_extensions is not None
    )
    # Generated motion script freshness is enforced separately by
    # ``motion_scripts_need_rebuild``. Only a real transport change makes the
    # live supervisor connection itself untrusted.
    supervisor_transport_before = (
        settings.robot_supervisor_hostname,
        settings.robot_supervisor_port,
        settings.robot_supervisor_heartbeat_seconds,
        settings.robot_supervisor_telemetry_hz,
        settings.robot_supervisor_reconnect_limit_seconds,
    )
    highest_occupied = session.scalar(
        select(Pallet.pool_slot_number)
        .where(Pallet.location == "pool")
        .order_by(Pallet.pool_slot_number.desc())
        .limit(1)
    )
    highest_reserved = session.scalar(
        select(Pallet.return_pool_slot_number)
        .where(Pallet.return_pool_slot_number.is_not(None))
        .order_by(Pallet.return_pool_slot_number.desc())
        .limit(1)
    )
    highest_in_use = max(highest_occupied or 0, highest_reserved or 0)
    if highest_in_use and payload.pool_slot_count is not None and payload.pool_slot_count < highest_in_use:
        raise problem(
            409,
            f"Pool position {highest_in_use} is occupied or reserved. Return or move that pallet before reducing capacity.",
        )
    if payload.on_deck_enabled is False and session.scalar(select(Pallet).where(Pallet.location == "on_deck")):
        raise problem(409, "Move the pallet out of On deck before disabling that station.")
    if payload.dripping_enabled is False and session.scalar(select(Pallet).where(Pallet.location == "dripping")):
        raise problem(409, "Move the pallet out of Dripping before disabling that station.")
    if payload.on_deck_enabled is not None:
        settings.on_deck_enabled = payload.on_deck_enabled
    if payload.dripping_enabled is not None:
        settings.dripping_enabled = payload.dripping_enabled

    if payload.source_folder is not None:
        settings.source_folder = payload.source_folder.strip()
    if payload.program_extensions is not None:
        settings.program_extensions = json.dumps(
            normalize_extensions(payload.program_extensions),
            separators=(",", ":"),
        )
    if payload.weight_unit is not None:
        settings.weight_unit = payload.weight_unit
    if payload.pool_slot_count is not None:
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
    if payload.debug_menu_enabled is not None:
        settings.debug_menu_enabled = payload.debug_menu_enabled
    if payload.manual_io_control_enabled is not None:
        settings.manual_io_control_enabled = payload.manual_io_control_enabled
    if payload.run_mode_safety_confirm is not None:
        settings.run_mode_safety_confirm = payload.run_mode_safety_confirm
    if payload.robot_connection_mode is not None:
        settings.robot_connection_mode = payload.robot_connection_mode
    if payload.robot_host is not None:
        settings.robot_host = payload.robot_host
    if payload.robot_port is not None:
        settings.robot_port = payload.robot_port
    if payload.robot_poll_hz is not None:
        settings.robot_poll_hz = payload.robot_poll_hz
    if payload.robot_timeout_seconds is not None:
        settings.robot_timeout_seconds = payload.robot_timeout_seconds
    supervisor_listener_changed = False
    if payload.robot_supervisor_enabled is not None:
        if payload.robot_supervisor_enabled and not settings.robot_supervisor_activation_verified:
            raise problem(409, "Run a successful no-motion supervisor bootstrap test before enabling supervisor commands.")
        settings.robot_supervisor_enabled = payload.robot_supervisor_enabled
    if payload.robot_supervisor_hostname is not None:
        settings.robot_supervisor_hostname = payload.robot_supervisor_hostname
    if payload.robot_supervisor_listen_host is not None:
        listen_host = payload.robot_supervisor_listen_host or "0.0.0.0"
        if settings.robot_supervisor_listen_host != listen_host:
            settings.robot_supervisor_listen_host = listen_host
            supervisor_listener_changed = True
    if payload.robot_supervisor_port is not None:
        if settings.robot_supervisor_port != payload.robot_supervisor_port:
            settings.robot_supervisor_port = payload.robot_supervisor_port
            supervisor_listener_changed = True
    if payload.robot_supervisor_heartbeat_seconds is not None:
        if settings.robot_supervisor_heartbeat_seconds != payload.robot_supervisor_heartbeat_seconds:
            settings.robot_supervisor_heartbeat_seconds = payload.robot_supervisor_heartbeat_seconds
            supervisor_listener_changed = True
    if payload.robot_supervisor_telemetry_hz is not None:
        if settings.robot_supervisor_telemetry_hz != payload.robot_supervisor_telemetry_hz:
            settings.robot_supervisor_telemetry_hz = payload.robot_supervisor_telemetry_hz
            supervisor_listener_changed = True
    if payload.robot_supervisor_reconnect_limit_seconds is not None:
        settings.robot_supervisor_reconnect_limit_seconds = payload.robot_supervisor_reconnect_limit_seconds
    if payload.robot_supervisor_pre_dispatch_fallback is not None:
        settings.robot_supervisor_pre_dispatch_fallback = payload.robot_supervisor_pre_dispatch_fallback
    if payload.stack_light_enabled is not None:
        settings.stack_light_enabled = payload.stack_light_enabled
    if payload.stack_light_outputs is not None:
        unknown = set(payload.stack_light_outputs) - set(_STACK_LIGHT_CHANNELS)
        if unknown:
            raise problem(422, "Stack-light outputs may only configure red, amber, green, blue, white, and alarm.")
        assignments = [f"{item.bank}:{item.index}" for item in payload.stack_light_outputs.values()]
        if len(assignments) != len(set(assignments)):
            raise problem(422, "Each stack-light function must use a different robot output.")
        settings.stack_light_outputs = json.dumps(
            {name: item.model_dump() for name, item in payload.stack_light_outputs.items()},
            separators=(",", ":"), sort_keys=True,
        )
    if payload.stack_light_state_colors is not None:
        unknown = set(payload.stack_light_state_colors) - set(_STACK_LIGHT_SYSTEM_STATES)
        if unknown:
            raise problem(422, "Stack-light colors contain an unknown system state.")
        colors = _DEFAULT_STACK_LIGHT_STATE_COLORS | payload.stack_light_state_colors
        settings.stack_light_state_colors = json.dumps(colors, separators=(",", ":"), sort_keys=True)
    if payload.push_notifications_enabled is not None:
        settings.push_notifications_enabled = payload.push_notifications_enabled
    for field_name in ("push_notification_server", "push_notification_topic"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(settings, field_name, value.strip())
    # An empty token field from the browser means "leave the saved token".
    if payload.push_notification_token:
        settings.push_notification_token = payload.push_notification_token.strip()
    if payload.push_notify_errors is not None:
        settings.push_notify_errors = payload.push_notify_errors
    if payload.push_notify_completed_pallets is not None:
        settings.push_notify_completed_pallets = payload.push_notify_completed_pallets
    if payload.push_notify_queue_empty is not None:
        settings.push_notify_queue_empty = payload.push_notify_queue_empty
    if payload.background_stack_light_intensity is not None:
        settings.background_stack_light_intensity = payload.background_stack_light_intensity
    if settings.push_notifications_enabled and not settings.push_notification_topic.strip():
        raise problem(422, "Enter a private ntfy topic before enabling push notifications.")
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
    if payload.mill_supervisor_enabled is not None:
        if payload.mill_supervisor_enabled != settings.mill_supervisor_enabled:
            raise problem(409, "Use the guarded mill supervisor activation control to change command routing.")
    if payload.mill_supervisor_hostname is not None:
        settings.mill_supervisor_hostname = payload.mill_supervisor_hostname
        settings.mill_supervisor_activation_verified = False
    if payload.mill_supervisor_listen_host is not None:
        settings.mill_supervisor_listen_host = payload.mill_supervisor_listen_host or "0.0.0.0"
        settings.mill_supervisor_activation_verified = False
    if payload.mill_supervisor_port is not None:
        settings.mill_supervisor_port = payload.mill_supervisor_port
        settings.mill_supervisor_activation_verified = False
    if payload.mill_supervisor_heartbeat_seconds is not None:
        settings.mill_supervisor_heartbeat_seconds = payload.mill_supervisor_heartbeat_seconds
    if payload.mill_supervisor_telemetry_hz is not None:
        settings.mill_supervisor_telemetry_hz = payload.mill_supervisor_telemetry_hz
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
    if payload.mill_status_file_path is not None:
        settings.mill_status_file_path = payload.mill_status_file_path
    if payload.camera_devices is not None:
        normalized_devices = parse_camera_devices(payload.camera_devices)
        if len(normalized_devices) != len(payload.camera_devices):
            raise problem(422, "Camera definitions must have unique IDs and valid USB device indexes.")
        settings.camera_devices_json = json.dumps(normalized_devices, separators=(",", ":"))
    for field_name in ("camera_idle_id", "camera_loading_id", "camera_machining_id"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(settings, field_name, value)
    camera_ids = {item["id"] for item in parse_camera_devices(settings.camera_devices_json)}
    for field_name in ("camera_idle_id", "camera_loading_id", "camera_machining_id"):
        selected = getattr(settings, field_name)
        if selected and selected not in camera_ids:
            raise problem(422, f"{field_name} must refer to a configured camera.")
    if payload.camera_recording_enabled is not None:
        settings.camera_recording_enabled = payload.camera_recording_enabled
    if payload.camera_recording_path is not None:
        settings.camera_recording_path = payload.camera_recording_path
    if payload.camera_recording_retention_days is not None:
        settings.camera_recording_retention_days = payload.camera_recording_retention_days
    if payload.camera_width is not None:
        settings.camera_width = payload.camera_width
    if payload.camera_height is not None:
        settings.camera_height = payload.camera_height
    if payload.camera_fps is not None:
        settings.camera_fps = payload.camera_fps
    if payload.camera_segment_seconds is not None:
        settings.camera_segment_seconds = payload.camera_segment_seconds
    _mill_status_file_path(settings)
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
        if any(slot > (payload.pool_slot_count or settings.pool_slot_count) for slot in slots):
            raise problem(422, "A pallet-motion mapping is outside the configured pool capacity.")
        settings.pallet_motion_programs = json.dumps(mappings, separators=(",", ":"))
    if payload.pallet_motion_generation is not None:
        generation = payload.pallet_motion_generation.model_dump()
        intermediate_names = [item["name"].casefold() for item in generation["intermediate_safe_poses"]]
        if len(intermediate_names) != len(set(intermediate_names)):
            raise problem(422, "Intermediate safe-pose names must be unique.")
        pool_capacity = payload.pool_slot_count or settings.pool_slot_count
        if any(slot > pool_capacity for item in generation["intermediate_safe_poses"] for slot in item["pool_slots"]):
            raise problem(422, "An intermediate safe pose is assigned outside the configured pool capacity.")
        settings.pallet_motion_generation = json.dumps(generation, separators=(",", ":"))
    if payload.debug_menu_enabled is False:
        settings.machine_state = "idle"
    supervisor_transport_after = (
        settings.robot_supervisor_hostname,
        settings.robot_supervisor_port,
        settings.robot_supervisor_heartbeat_seconds,
        settings.robot_supervisor_telemetry_hz,
        settings.robot_supervisor_reconnect_limit_seconds,
    )
    if supervisor_transport_before != supervisor_transport_after:
        settings.robot_supervisor_activation_verified = False
        settings.robot_supervisor_enabled = False
    # Settings like robot poses, display units, or I/O labels must never erase
    # pallet program assignments just because a network program folder is slow
    # or temporarily unavailable. Reconcile only when the catalog itself changes;
    # the explicit refresh endpoint remains available for an operator-led scan.
    cleared = reconcile_programs(session, settings) if program_catalog_changed else []
    bump(settings)
    commit_or_conflict(session)
    refresh_stack_light(session)
    camera_manager().apply(settings)
    if supervisor_listener_changed:
        robot_supervisor().start(
            settings.robot_supervisor_listen_host,
            settings.robot_supervisor_port,
            settings.robot_supervisor_heartbeat_seconds,
            settings.robot_supervisor_telemetry_hz,
        )
    _clear_telemetry_caches()
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
    root = _mill_program_root(settings)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root):
        raise problem(422, f"Mill program filename must be inside {root}.")
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


def run_debug_program(session: Session, payload: RunDebugProgram) -> bool:
    _assert_reliability_inactive(session)
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if settings.robot_connection_mode != "physical" or not settings.robot_host.strip():
        raise problem(409, "Running controller programs requires a configured physical robot.")
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before starting a manual controller program.")
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before starting a manual program.")
    buttons = _load_debug_program_buttons(settings)
    if payload.index >= len(buttons):
        raise problem(422, "That program button is not enabled in Settings.")
    filename = buttons[payload.index]["filename"]
    if not filename:
        raise problem(422, "Configure a controller filename for this program button first.")
    restore_supervisor = False
    if settings.robot_supervisor_enabled and not settings.robot_supervisor_maintenance_mode:
        command = _new_supervisor_command(
            session,
            motion=None,
            operation="enter_maintenance",
            opcode=OP_ENTER_MAINTENANCE,
        )
        outcome, detail = _dispatch_supervisor_command(
            session,
            command,
            max(5.0, settings.robot_timeout_seconds * 4),
            allow_pre_dispatch_fallback=False,
        )
        if outcome != "completed":
            raise problem(409, f"Could not enter supervisor Maintenance Mode: {detail}")
        settings = get_settings(session)
        settings.robot_supervisor_maintenance_mode = True
        bump(settings)
        commit_or_conflict(session)
        restore_supervisor = True
    try:
        run_robot_program(settings.robot_host.strip(), filename, settings.robot_timeout_seconds)
    except RobotDashboardError as exc:
        if restore_supervisor:
            try:
                bootstrap_robot_supervisor(session)
            except HTTPException:
                pass
        raise problem(502, str(exc)) from exc
    return restore_supervisor


def restore_supervisor_after_arbitrary_program(session_factory) -> None:
    """Wait for a Maintenance Mode program to finish, then restore the persistent supervisor."""
    observed_running = False
    stopped_polls = 0
    deadline = time.monotonic() + 24 * 60 * 60
    while time.monotonic() < deadline:
        with session_factory() as session:
            settings = get_settings(session)
            if not settings.robot_supervisor_maintenance_mode:
                return
            host = settings.robot_host.strip()
            timeout_seconds = settings.robot_timeout_seconds
        try:
            running = robot_program_running(host, timeout_seconds)
        except RobotDashboardError:
            time.sleep(2)
            continue
        if running:
            observed_running = True
            stopped_polls = 0
        else:
            stopped_polls += 1
            if stopped_polls >= (2 if observed_running else 4):
                break
        time.sleep(0.5)
    with session_factory() as session:
        try:
            bootstrap_robot_supervisor(session)
        except HTTPException:
            # Maintenance Mode remains visible and operator-recoverable when
            # the controller or network does not return after the program.
            session.rollback()


def mill_program_files(session: Session) -> list[str]:
    settings = get_settings(session)
    if not settings.cnc_host.strip() or not settings.cnc_ssh_username:
        raise problem(409, "Configure the PathPilot SSH connection in Settings first.")
    try:
        return list_robot_program_files(
            host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
            username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
            directory=str(_mill_program_root(settings)),
            extensions=set(json.loads(settings.mill_program_extensions)),
            timeout_seconds=settings.cnc_timeout_seconds,
        )
    except RobotFileAccessError as exc:
        raise problem(502, str(exc)) from exc


def pallet_program_files(session: Session) -> list[str]:
    """List assignable mill programs from the same SFTP root as Mill Programs."""
    settings = get_settings(session)
    extensions = set(json.loads(settings.mill_program_extensions))
    remote_configured = bool(
        settings.mill_programs_page_enabled
        and settings.cnc_host.strip()
        and settings.cnc_ssh_username
        and settings.cnc_ssh_password
    )
    if remote_configured:
        root = PurePosixPath(settings.mill_file_directory)
        key = (
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            str(root), tuple(sorted(extensions)),
        )
        now = time.monotonic()
        with _PALLET_PROGRAM_REMOTE_LOCK:
            cached = _PALLET_PROGRAM_REMOTE_CACHE.get(key)
            if cached and now - cached[0] < 10.0:
                return list(cached[1])
        try:
            files = list_robot_program_files(
                host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
                username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
                directory=str(root), extensions=extensions,
                timeout_seconds=settings.cnc_timeout_seconds,
            )
            relative_files = [str(PurePosixPath(path).relative_to(root)) for path in files]
            with _PALLET_PROGRAM_REMOTE_LOCK:
                _PALLET_PROGRAM_REMOTE_CACHE[key] = (time.monotonic(), list(relative_files))
            return relative_files
        except RobotFileAccessError:
            # A brief PathPilot outage should not invalidate a selection the UI
            # loaded moments earlier. Keep a bounded last-known-good file list.
            with _PALLET_PROGRAM_REMOTE_LOCK:
                cached = _PALLET_PROGRAM_REMOTE_CACHE.get(key)
                if cached and now - cached[0] < 300.0:
                    return list(cached[1])

    local_programs, _ = available_programs(settings)
    return local_programs


def run_debug_mill_program(session: Session, payload: RunDebugMillProgram) -> None:
    _assert_reliability_inactive(session)
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
    _assert_reliability_inactive(session)
    settings = get_settings(session)
    check_revision(settings, payload.expected_revision)
    if not settings.manual_io_control_enabled:
        raise problem(403, "Manual I/O control is locked in Settings.")
    if settings.run_mode_enabled:
        raise problem(409, "Stop Run Mode before manually changing robot I/O.")
    if _locked_motion(session):
        raise problem(409, "Resolve or wait for the active robot pallet movement before manually changing I/O.")

    if payload.bank == "tool" and payload.index > 1:
        raise problem(422, "Tool I/O indices only allow 0 or 1.")

    if settings.robot_connection_mode == "physical":
        if payload.direction != "output":
            raise problem(409, "Physical robot inputs are read-only.")
        if not settings.robot_host.strip():
            raise problem(409, "Physical robot mode requires a configured robot host.")
        if settings.robot_supervisor_enabled:
            status = robot_supervisor().status()
            telemetry = status.get("telemetry") or {}
            mask = telemetry.get(f"{payload.bank}_outputs")
            if not isinstance(mask, int):
                raise problem(409, f"Supervisor telemetry does not currently expose {payload.bank} output state.")
            opcodes = {
                "standard": OP_SET_STANDARD_OUTPUT,
                "configurable": OP_SET_CONFIGURABLE_OUTPUT,
                "tool": OP_SET_TOOL_OUTPUT,
            }
            command = _new_supervisor_command(
                session,
                motion=None,
                operation=f"set_{payload.bank}_output",
                opcode=opcodes[payload.bank],
                argument=payload.index,
                value=0 if mask & (1 << payload.index) else 1,
            )
            outcome, detail = _dispatch_supervisor_command(
                session,
                command,
                max(5.0, settings.robot_timeout_seconds * 4),
                allow_pre_dispatch_fallback=settings.robot_supervisor_pre_dispatch_fallback,
            )
            if outcome == "completed":
                return
            if outcome != "fallback":
                raise problem(409, detail)
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


def refresh_programs(session: Session, expected_revision: int) -> dict[str, object]:
    settings = get_settings(session)
    check_revision(settings, expected_revision)
    remote_configured = bool(
        settings.mill_programs_page_enabled
        and settings.cnc_host.strip()
        and settings.cnc_ssh_username
        and settings.cnc_ssh_password
    )
    if remote_configured:
        root = PurePosixPath(settings.mill_file_directory)
        extensions = set(json.loads(settings.mill_program_extensions))
        try:
            remote_files = list_robot_program_files(
                host=settings.cnc_host.strip(), port=settings.cnc_ssh_port,
                username=settings.cnc_ssh_username, password=settings.cnc_ssh_password,
                directory=str(root), extensions=extensions,
                timeout_seconds=settings.cnc_timeout_seconds,
            )
        except RobotFileAccessError as exc:
            # Never clear assignments from an empty fallback catalog when the
            # authoritative PathPilot directory could not be reached.
            raise problem(502, f"Could not refresh PathPilot programs: {exc}") from exc
        programs = [str(PurePosixPath(path).relative_to(root)) for path in remote_files]
        key = (
            settings.cnc_host.strip(), settings.cnc_ssh_port, settings.cnc_ssh_username,
            str(root), tuple(sorted(extensions)),
        )
        with _PALLET_PROGRAM_REMOTE_LOCK:
            _PALLET_PROGRAM_REMOTE_CACHE[key] = (time.monotonic(), list(programs))
        available = set(programs)
        cleared: list[str] = []
        for pallet in session.scalars(select(Pallet).where(Pallet.program_path.is_not(None))).all():
            if pallet.program_path not in available:
                pallet.program_path = None
                _clear_pallet_program_metadata(pallet)
                cleared.append(pallet.name)
    else:
        programs, _ = available_programs(settings, force=True)
        cleared = reconcile_programs(session, settings)

    metadata_changed = False
    metadata_by_program: dict[str, dict[str, object]] = {}
    for pallet in session.scalars(select(Pallet).where(Pallet.program_path.is_not(None))).all():
        program_path = pallet.program_path or ""
        if program_path not in metadata_by_program:
            metadata_by_program[program_path] = read_assigned_program_metadata(settings, program_path)
        metadata = metadata_by_program[program_path]
        previous = (
            pallet.program_tools_json, pallet.program_tool_counts_json, pallet.program_wcs_json, pallet.expected_cycle_seconds,
            pallet.program_metadata_state, pallet.program_metadata_detail, pallet.program_cycle_basis,
        )
        _store_pallet_program_metadata(pallet, metadata)
        current = (
            pallet.program_tools_json, pallet.program_tool_counts_json, pallet.program_wcs_json, pallet.expected_cycle_seconds,
            pallet.program_metadata_state, pallet.program_metadata_detail, pallet.program_cycle_basis,
        )
        metadata_changed = metadata_changed or previous != current
    if cleared or metadata_changed:
        bump(settings)
        commit_or_conflict(session)
    return {
        "cleared_assignments": cleared,
        "programs": programs,
        "metadata_refreshed": len(metadata_by_program),
    }


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
        pallet.pool_slot_number = best_pool_return_slot(session, settings, pallet)
        pallet.location = "pool"
        pallet.return_pool_slot_number = None
        pallet.content_status = (
            "complete_parts" if signal == "complete" else "defective_parts"
        )
        settings.machine_state = "idle"
    else:
        raise problem(422, "Unknown debug signal.")

    bump(settings)
    commit_or_conflict(session)
