"""LinuxCNC telemetry and explicitly requested program control over PathPilot SSH."""

from __future__ import annotations

import base64
import json
import shlex
from threading import RLock
from time import monotonic

import paramiko


class CncTelemetryError(RuntimeError):
    """Raised when the controller cannot provide a valid telemetry snapshot."""


class CncProgramFault(RuntimeError):
    """Raised when PathPilot reports a program failure or active machine alarm."""


_SSH_LOCK = RLock()
_SSH_CLIENT: paramiko.SSHClient | None = None
_SSH_KEY: tuple[str, int, str, str, float] | None = None
_SSH_RETRY_AFTER = 0.0
_SSH_FAILURE_COUNT = 0
_SSH_SUSPENDED = False


# This runs on the controller and intentionally creates only a linuxcnc.stat()
# object. Do not add linuxcnc.command() calls to this telemetry adapter.
_REMOTE_STATUS_SCRIPT = r'''import json
import linuxcnc
import os
import re
import subprocess

AXES = ["X", "Y", "Z", "A", "B", "C", "U", "V", "W"]
s = linuxcnc.stat()
s.poll()

def value(name, default=None):
    return getattr(s, name, default)

def number(item):
    return item if isinstance(item, (int, float)) else None

def number_list(name):
    return [number(item) for item in list(value(name, []))]

def bool_list(name):
    return [bool(item) for item in list(value(name, []))]

def code_list(name, prefix, divide_by_ten=True):
    codes = []
    for code in list(value(name, [])):
        if not isinstance(code, (int, float)) or code <= 0:
            continue
        rendered = "{:g}".format(code / 10.0) if divide_by_ten else "{:g}".format(code)
        codes.append(prefix + rendered)
    return codes

def hal_value(pin):
    try:
        raw = subprocess.check_output(["/home/operator/tmc/bin/halcmd", "getp", pin], stderr=subprocess.STDOUT).strip()
        if raw.upper() == "TRUE":
            return True
        if raw.upper() == "FALSE":
            return False
        return float(raw)
    except Exception:
        return None

def tool_table_rows():
    # The tool table contains geometry. Its P value mirrors the tool number on
    # PathPilot and is not the physical ATC position.
    rows = []
    path = "/home/operator/mill_data/tool2.tbl"
    try:
        for line in open(path):
            match = re.search(r"(?:^|\s)T(\d+)\s+P(\d+)", line)
            if not match:
                continue
            number_match = re.search(r"(?:^|\s)D([+-]?[\d.]+)", line)
            length_match = re.search(r"(?:^|\s)Z([+-]?[\d.]+)", line)
            rows.append({
                "tool_number": int(match.group(1)),
                "pocket": int(match.group(2)),
                "diameter": float(number_match.group(1)) if number_match else None,
                "length_offset": float(length_match.group(1)) if length_match else None,
            })
    except Exception:
        pass
    return rows

def redis_hash(name):
    try:
        output = subprocess.check_output(["redis-cli", "--raw", "HGETALL", name], stderr=subprocess.STDOUT)
        values = output.splitlines()
        return dict(zip(values[0::2], values[1::2]))
    except Exception:
        return {}

def atc_position_count():
    path = "/home/operator/tmc/configs/tormach_mill/tormach_1500MX_ethercat.ini"
    try:
        for line in open(path):
            match = re.match(r"\s*ATC_GEN2_TRAY_SLOTS\s*=\s*(\d+)", line)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return 16

def atc_slot_rows(tool_rows):
    # PathPilot's zbot ATC stores its actual carousel assignments separately
    # from LinuxCNC's generic tool table. Internal slots 0-15 are displayed as
    # physical ATC positions 1-16 by the controller UI.
    mapping = redis_hash("zbot_slot_table")
    position_count = atc_position_count()
    if not any(str(slot) in mapping for slot in range(position_count)):
        return [], None
    by_number = dict((row["tool_number"], row) for row in tool_rows)
    try:
        current_slot = int(mapping.get("current_slot", "-1"))
    except (TypeError, ValueError):
        current_slot = -1
    rows = []
    for internal_slot in range(position_count):
        try:
            tool_number = int(mapping.get(str(internal_slot), "0")) or None
        except (TypeError, ValueError):
            tool_number = None
        tool = by_number.get(tool_number, {})
        rows.append({
            "position": internal_slot + 1,
            "tool_number": tool_number,
            "diameter": tool.get("diameter"),
            "length_offset": tool.get("length_offset"),
            "current": internal_slot == current_slot,
        })
    return rows, (current_slot + 1 if 0 <= current_slot < position_count else None)

actual = list(value("actual_position", []))
commanded = list(value("position", []))
axis_data = list(value("axis", []))
joint_data = list(value("joint", []))
homed = bool_list("homed")
limits = number_list("limit")
distance_to_go = number_list("dtg")
rows = []
for index, axis in enumerate(AXES):
    if index >= len(actual) and index >= len(commanded) and index >= len(axis_data):
        continue
    axis_status = axis_data[index] if index < len(axis_data) else {}
    joint_status = joint_data[index] if index < len(joint_data) else {}
    rows.append({
        "axis": axis,
        "position": number(actual[index]) if index < len(actual) else None,
        "commanded": number(commanded[index]) if index < len(commanded) else None,
        "velocity": number(axis_status.get("velocity")) if isinstance(axis_status, dict) else None,
        "following_error": number(joint_status.get("ferror")) if isinstance(joint_status, dict) else None,
        "homed": homed[index] if index < len(homed) else None,
        "limit": limits[index] if index < len(limits) else None,
        "distance_to_go": distance_to_go[index] if index < len(distance_to_go) else None,
    })

spindles = list(value("spindle", []))
spindle = spindles[0] if spindles else {}
if not isinstance(spindle, dict):
    spindle = {}

tool_rows = tool_table_rows()
atc_slots, current_atc_position = atc_slot_rows(tool_rows)

payload = {
    "task_state": value("task_state"),
    "task_mode": value("task_mode"),
    "interp_state": value("interp_state"),
    "program": value("file", "") or "",
    "motion_line": value("motion_line"),
    "current_line": value("current_line"),
    "tool_in_spindle": value("tool_in_spindle"),
    "spindle_speed": spindle.get("speed"),
    "spindle_enabled": spindle.get("enabled"),
    "spindle_direction": spindle.get("direction"),
    "flood": value("flood"),
    "mist": value("mist"),
    "feed_override": value("feedrate"),
    "feed_hold": value("paused"),
    "estop": value("estop"),
    "enabled": value("enabled"),
    "axis_rows": rows,
    "atc": {
        "carousel_slot": hal_value("motion.analog-in-06"),
        "current_position": current_atc_position,
        "change_in_progress": hal_value("dbbutton.tool-change-in-progress"),
        "tray_in": hal_value("motion.digital-in-17"),
        "device_ready": hal_value("motion.digital-in-21"),
        "tray_referenced": hal_value("motion.digital-in-22"),
        "pressure_ok": hal_value("motion.digital-in-20"),
        "tool_number": hal_value("iocontrol.0.tool-number"),
        "prepared_tool": hal_value("iocontrol.0.tool-prep-number"),
        "drawbar_engaged": hal_value("zbotatc.0.dout.2.draw_status"),
        "lock_engaged": hal_value("zbotatc.0.dout.6.lock_status"),
        "vfd_status": hal_value("zbotatc.0.dout.1.vfd_status"),
        "busy": hal_value("zbotatc.0.dout.5.exec_status"),
        "return_code": hal_value("zbotatc.0.aout.0.request_rc"),
        "tray_capacity": hal_value("zbotatc.atc-tools-in-tray"),
        "slots": atc_slots,
    },
    "health": {
        "estop": value("estop"),
        "enabled": value("enabled"),
        "jog_lockout_configured": value("cfg_jog_lockout_after_unexpected"),
        "jog_locked_out": value("jog_locked_out"),
        "motion_stop_lockout_configured": value("cfg_motion_stop_jog_lockout_after_unexpected"),
        "motion_stop_locked_out": value("motion_stop_jog_locked_out"),
        "in_position": value("inpos"),
        "homed": homed,
        "limits": limits,
        "lube_active": value("lube"),
        "lube_level_warning": value("lube_level"),
        "interpreter_error": value("interpreter_errcode"),
    },
    "motion": {
        "distance_to_go": number(value("distance_to_go")),
        "current_velocity": number(value("current_vel")),
        "velocity": number(value("velocity")),
        "acceleration": number(value("acceleration")),
        "axis_distance_to_go": distance_to_go,
        "motion_mode": value("motion_mode"),
    },
    "coordinates": {
        "g5x_index": value("g5x_index"),
        "g5x_offset": number_list("g5x_offset"),
        "g92_offset": number_list("g92_offset"),
        "rotation_xy": number(value("rotation_xy")),
        "program_units": value("program_units"),
        "linear_units": number(value("linear_units")),
        "angular_units": number(value("angular_units")),
    },
    "program_execution": {
        "state": value("state"),
        "exec_state": value("exec_state"),
        "read_line": value("read_line"),
        "readahead_line": value("task_readahead_line"),
        "active_queue": value("active_queue"),
        "queue": value("queue"),
        "queue_full": value("queue_full"),
        "dwell_remaining": number(value("dwell_time_remaining")),
        "optional_stop": value("optional_stop"),
        "block_delete": value("block_delete"),
        "adaptive_feed": value("adaptive_feed_enabled"),
        "feed_hold_enabled": value("feed_hold_enabled"),
        "g_codes": code_list("gcodes", "G"),
        "m_codes": code_list("mcodes", "M", False),
    },
    "spindle_details": {
        "commanded_speed": number(value("spindle_speed")),
        "feedback_speed": number(value("spindle_speed_feedback")),
        "enabled": value("spindle_enabled"),
        "direction": value("spindle_direction"),
        "brake": value("spindle_brake"),
        "spindle_override": number(value("spindlerate")),
        "rapid_override": number(value("rapidrate")),
        "feed_override": number(value("feedrate")),
    },
    "probe": {
        "tripped": value("probe_tripped"),
        "value": value("probe_val"),
        "last_position": number_list("probed_position"),
    },
    "tooling": {
        "tool_in_spindle": value("tool_in_spindle"),
        "prepared_pocket": value("pocket_prepped"),
        "tool_offset_number": value("tool_offset_number"),
        "tool_offset": number_list("tool_offset"),
    },
    "production": {
        "cycle_time": number(value("cycle_time")),
        "m30_a": value("parts_counter_m30a"),
        "m30_b": value("parts_counter_m30b"),
        "m99_a": value("parts_counter_m99a"),
        "m99_b": value("parts_counter_m99b"),
    },
    "io": {
        "digital_inputs": bool_list("din"),
        "digital_outputs": bool_list("dout"),
        "analog_inputs": number_list("ain"),
        "analog_outputs": number_list("aout"),
    },
    "tool_table": tool_rows,
}
print("MONGO_CNC=" + json.dumps(payload, allow_nan=False))
'''


_REMOTE_IO_LABELS_SCRIPT = r'''import json
import re
import subprocess

labels = {
    "digital_inputs": {},
    "digital_outputs": {},
    "analog_inputs": {},
    "analog_outputs": {},
}
try:
    signal_text = subprocess.check_output(["/home/operator/tmc/bin/halcmd", "show", "sig"], stderr=subprocess.STDOUT)
    current_signal = None
    channel_patterns = (
        ("digital_inputs", r"motion\.digital-in-(\d+)"),
        ("digital_outputs", r"motion\.digital-out-(\d+)"),
        ("analog_inputs", r"motion\.analog-in-(\d+)"),
        ("analog_outputs", r"motion\.analog-out-(\d+)"),
    )
    for line in signal_text.splitlines():
        header = re.match(r"^\s*(?:bit|float|s32|u32|s64|u64)\s+\S+\s+(\S+)\s*$", line)
        if header:
            current_signal = header.group(1)
            continue
        if not current_signal:
            continue
        for group, pattern in channel_patterns:
            match = re.search(pattern, line)
            if match:
                labels[group][str(int(match.group(1)))] = current_signal
except Exception:
    pass

print("MONGO_CNC_LABELS=" + json.dumps(labels, allow_nan=False))
'''


_REMOTE_CYCLE_STATE_SCRIPT = r'''import json
import linuxcnc

s = linuxcnc.stat()
s.poll()
messages = []
try:
    channel = linuxcnc.error_channel()
    error_kinds = set(filter(lambda value: value is not None, (
        getattr(linuxcnc, "NML_ERROR", None),
        getattr(linuxcnc, "OPERATOR_ERROR", None),
    )))
    for _ in range(20):
        message = channel.poll()
        if not message:
            break
        kind, text = message
        if not isinstance(text, str):
            text = text.decode("utf-8", "replace")
        messages.append({"kind": kind, "text": text, "is_error": kind in error_kinds})
except Exception:
    pass

status_state = getattr(s, "state", None)
exec_state = getattr(s, "exec_state", None)
print("MONGO_CNC_CYCLE=" + json.dumps({
    "interp_state": getattr(s, "interp_state", None),
    "estop": getattr(s, "estop", None),
    "enabled": getattr(s, "enabled", None),
    "program": getattr(s, "file", "") or "",
    "motion_line": getattr(s, "motion_line", None),
    "current_line": getattr(s, "current_line", None),
    "read_line": getattr(s, "read_line", None),
    "paused": getattr(s, "paused", None),
    "queue": getattr(s, "queue", None),
    "task_state": getattr(s, "task_state", None),
    "jog_lockout_configured": getattr(s, "cfg_jog_lockout_after_unexpected", None),
    "jog_locked_out": getattr(s, "jog_locked_out", None),
    "motion_stop_lockout_configured": getattr(s, "cfg_motion_stop_jog_lockout_after_unexpected", None),
    "motion_stop_locked_out": getattr(s, "motion_stop_jog_locked_out", None),
    "status_state": status_state,
    "exec_state": exec_state,
    "interpreter_error": getattr(s, "interpreter_errcode", None),
    "rcs_error": status_state == getattr(linuxcnc, "RCS_ERROR", -999999),
    "exec_error": exec_state == getattr(linuxcnc, "EXEC_ERROR", -999999),
    "error_messages": messages,
}, allow_nan=False))
'''


def _remote_command(remote_script: str, timeout_seconds: float) -> str:
    encoded = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
    # PathPilot's ordinary SSH shell does not include its LinuxCNC module or
    # NML_FILE. Source either spelling and directory used by PathPilot builds.
    script = (
        "export PYTHONPATH=/home/operator/tmc/python:/home/operator/tmc/lib/python:"
        "/home/operator/tmc/python/config_picker:${PYTHONPATH:-}; "
        "export LD_LIBRARY_PATH=/home/operator/tmc/lib:${LD_LIBRARY_PATH:-}; "
        "export NML_FILE=${NML_FILE:-/home/operator/tmc/configs/common/linuxcnc.nml}; "
        "for env_script in "
        "/home/operator/tmc/scripts/rip_environment.sh "
        "/home/operator/tmc/scripts/rip_enviroment.sh "
        "/home/operator/tmc/script/rip_environment.sh "
        "/home/operator/tmc/script/rip_enviroment.sh; do "
        "if [ -r \"$env_script\" ]; then . \"$env_script\"; break; fi; "
        "done; "
        f"exec python -c \"import base64;exec(base64.b64decode('{encoded}'))\""
    )
    # Bound the process on PathPilot itself. Closing an SSH channel does not
    # reliably terminate its remote child, which can otherwise leave a stale
    # telemetry or command process behind after a network timeout.
    remote_limit = max(2, int(float(timeout_seconds) + 0.999))
    return f"timeout --signal=TERM {remote_limit}s bash -lc {shlex.quote(script)}"


def _read_remote_payload(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: float,
    remote_script: str,
    marker: str,
) -> dict:
    with _SSH_LOCK:
        client = _persistent_ssh_client(host, port, username, password, timeout)
        try:
            _, stdout, stderr = client.exec_command(
                _remote_command(remote_script, timeout), timeout=timeout, get_pty=False,
            )
            output = stdout.read().decode("utf-8", errors="replace")
            error_output = stderr.read().decode("utf-8", errors="replace").strip()
        except (paramiko.SSHException, EOFError, OSError) as exc:
            _close_ssh_client()
            _record_ssh_failure()
            raise CncTelemetryError(str(exc)) from exc

    for line in reversed(output.splitlines()):
        if line.startswith(marker):
            try:
                return json.loads(line.removeprefix(marker))
            except json.JSONDecodeError as exc:
                raise CncTelemetryError("Controller returned invalid LinuxCNC telemetry JSON.") from exc
    detail = error_output or output.strip() or "No telemetry response received."
    raise CncTelemetryError(detail)


def _record_ssh_failure() -> None:
    global _SSH_FAILURE_COUNT, _SSH_RETRY_AFTER
    _SSH_FAILURE_COUNT += 1
    delay = min(60.0, 5.0 * (2 ** min(_SSH_FAILURE_COUNT - 1, 4)))
    _SSH_RETRY_AFTER = monotonic() + delay


def _close_ssh_client() -> None:
    global _SSH_CLIENT, _SSH_KEY
    client = _SSH_CLIENT
    _SSH_CLIENT = None
    _SSH_KEY = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def _persistent_ssh_client(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: float,
) -> paramiko.SSHClient:
    global _SSH_CLIENT, _SSH_KEY, _SSH_FAILURE_COUNT, _SSH_RETRY_AFTER
    if _SSH_SUSPENDED:
        raise CncTelemetryError("CNC communication is suspended while the backend is shutting down.")

    key = (host, port, username, password, timeout)
    if _SSH_CLIENT is not None and _SSH_KEY == key:
        transport = _SSH_CLIENT.get_transport()
        if transport is not None and transport.is_active():
            return _SSH_CLIENT
        _close_ssh_client()

    if monotonic() < _SSH_RETRY_AFTER:
        remaining = max(1, int(_SSH_RETRY_AFTER - monotonic() + 0.999))
        raise CncTelemetryError(f"CNC SSH reconnect is cooling down after a connection failure; retry in {remaining} seconds.")

    _close_ssh_client()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password or None,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        client.close()
        _record_ssh_failure()
        raise CncTelemetryError(str(exc)) from exc

    _SSH_CLIENT = client
    _SSH_KEY = key
    _SSH_FAILURE_COUNT = 0
    _SSH_RETRY_AFTER = 0.0
    return client


def suspend_cnc_connections() -> None:
    """Close controller sockets before backend shutdown or relaunch."""
    global _SSH_SUSPENDED
    with _SSH_LOCK:
        _SSH_SUSPENDED = True
        _close_ssh_client()


def resume_cnc_connections() -> None:
    """Allow controller connections after application startup."""
    global _SSH_SUSPENDED, _SSH_FAILURE_COUNT, _SSH_RETRY_AFTER
    with _SSH_LOCK:
        _close_ssh_client()
        _SSH_SUSPENDED = False
        _SSH_FAILURE_COUNT = 0
        _SSH_RETRY_AFTER = 0.0


def read_linuxcnc_snapshot(host: str, port: int, username: str, password: str, timeout: float) -> dict:
    return _read_remote_payload(host, port, username, password, timeout, _REMOTE_STATUS_SCRIPT, "MONGO_CNC=")


def read_linuxcnc_cycle_state(host: str, port: int, username: str, password: str, timeout: float) -> dict:
    """Read only the fields needed while monitoring a running program."""
    return _read_remote_payload(host, port, username, password, timeout, _REMOTE_CYCLE_STATE_SCRIPT, "MONGO_CNC_CYCLE=")


def read_linuxcnc_io_labels(host: str, port: int, username: str, password: str, timeout: float) -> dict:
    """Read PathPilot's static HAL signal-to-channel labels."""
    return _read_remote_payload(host, port, username, password, timeout, _REMOTE_IO_LABELS_SCRIPT, "MONGO_CNC_LABELS=")


def run_linuxcnc_program(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: float,
    filename: str,
    require_a_axis_homed: bool = False,
) -> dict:
    """Load and start one preconfigured absolute G-code file through LinuxCNC."""
    remote_script = f'''import json
import linuxcnc
import os
import shutil
import subprocess
import time

filename = {filename!r}
require_a_axis_homed = {require_a_axis_homed!r}
pathpilot_program = "/tmp/very-unlikely-pathpilot-gcode.file"
status = linuxcnc.stat()
errors = linuxcnc.error_channel()

def read_errors():
    messages = []
    for _ in range(50):
        item = errors.poll()
        if not item:
            break
        kind, text = item
        if not isinstance(text, str):
            text = text.decode("utf-8", "replace")
        messages.append({{"kind": kind, "text": text}})
    return messages

def wait_for_mode(command, expected_mode, label):
    status.poll()
    if getattr(status, "task_mode", None) != expected_mode:
        result = command.mode(expected_mode)
        if result == getattr(linuxcnc, "RCS_ERROR", -1):
            raise RuntimeError("PathPilot rejected the change to " + label + " mode.")
        command.wait_complete()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        status.poll()
        if getattr(status, "task_mode", None) == expected_mode:
            return
        time.sleep(0.05)
    raise RuntimeError(
        "PathPilot did not enter " + label + " mode. Current task_mode="
        + str(getattr(status, "task_mode", None))
    )

def set_hal_pin(name, enabled):
    subprocess.check_call([
        "/home/operator/tmc/bin/halcmd", "setp", name,
        "TRUE" if enabled else "FALSE",
    ])

def read_hal_pin(name):
    raw = subprocess.check_output([
        "/home/operator/tmc/bin/halcmd", "getp", name,
    ]).strip().upper()
    return raw == "TRUE"

def motion_lockout_active():
    return bool(
        (
            getattr(status, "cfg_jog_lockout_after_unexpected", False)
            and getattr(status, "jog_locked_out", False)
        )
        or (
            getattr(status, "cfg_motion_stop_jog_lockout_after_unexpected", False)
            and getattr(status, "motion_stop_jog_locked_out", False)
        )
    )

def raise_if_motion_locked_out():
    status.poll()
    if motion_lockout_active():
        raise RuntimeError(
            "PathPilot motion is locked out after a probe or unexpected-stop event. "
            "Restore the probe and press Reset on PathPilot to re-enable operation."
        )

status.poll()
if getattr(status, "estop", False):
    raise RuntimeError("PathPilot is in E-stop.")
if not getattr(status, "enabled", False):
    raise RuntimeError("PathPilot is not enabled.")
raise_if_motion_locked_out()
if getattr(status, "interp_state", linuxcnc.INTERP_IDLE) != linuxcnc.INTERP_IDLE:
    raise RuntimeError("PathPilot is already running or paused. Stop the current program before starting another.")
homed = list(getattr(status, "homed", []))
# PathPilot exposes nine display slots in ``homed`` even on a three/four-axis
# mill. X/Y/Z are always required; A is opt-in because this mill normally runs
# three-axis programs even when its optional fourth axis is not homed.
active_axes = int(getattr(status, "axes", 0) or 0)
if active_axes <= 0:
    axis_mask = int(getattr(status, "axis_mask", 0) or 0)
    active_axes = bin(axis_mask).count("1") or len(homed)
required_axes = 4 if require_a_axis_homed and active_axes >= 4 else 3
if homed and (len(homed) < required_axes or not all(bool(value) for value in homed[:required_axes])):
    axis_label = "X, Y, Z, and A" if required_axes == 4 else "X, Y, and Z"
    raise RuntimeError(axis_label + " must be homed before starting a program.")

command = linuxcnc.command()
# Match PathPilot's own loader. Switching through MDI clears the interpreter's
# file pointer without program_close(), which PathPilot avoids because it also
# resets modal interpreter state. LinuxCNC runs a private immutable copy while
# resolving relative paths from the selected program's original directory.
wait_for_mode(command, linuxcnc.MODE_MDI, "MDI")
wait_for_mode(command, linuxcnc.MODE_AUTO, "Auto")
shutil.copy2(filename, pathpilot_program)
command.program_open(pathpilot_program, os.path.dirname(filename))
command.wait_complete()
# PathPilot may acknowledge program_open() before status reports the private
# working copy as selected.
load_deadline = time.time() + 5.0
while time.time() < load_deadline:
    status.poll()
    if (getattr(status, "file", "") or "") == pathpilot_program:
        break
    time.sleep(0.05)
status.poll()
loaded_program = getattr(status, "file", "") or ""
if loaded_program != pathpilot_program:
    raise RuntimeError(
        "PathPilot did not finish loading the requested program. "
        "Requested: " + filename + "; controller selected: " + (loaded_program or "none")
    )
# Loading can cause an asynchronous mode transition. Reconfirm Auto and Idle
# before Cycle Start rather than assuming wait_complete() synchronized status.
wait_for_mode(command, linuxcnc.MODE_AUTO, "Auto")
status.poll()
if getattr(status, "interp_state", linuxcnc.INTERP_IDLE) != linuxcnc.INTERP_IDLE:
    raise RuntimeError("PathPilot left Idle while loading the requested program.")
# Discard stale messages so any diagnostics below belong to this launch.
read_errors()
# Use LinuxCNC's supported HALUI remote-start path. PathPilot's UI owns the
# interpreter state and can force Manual while an external command object is
# loading a file; HALUI requests Auto and Run together without competing with
# that UI state machine. Always release both momentary inputs afterward.
set_hal_pin("halui.program.run", False)
set_hal_pin("halui.mode.auto", False)
try:
    set_hal_pin("halui.mode.auto", True)
    auto_deadline = time.time() + 5.0
    while time.time() < auto_deadline:
        status.poll()
        if (
            read_hal_pin("halui.mode.is-auto")
            and getattr(status, "task_mode", None) == linuxcnc.MODE_AUTO
        ):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("PathPilot did not acknowledge the HALUI Auto-mode request.")
    set_hal_pin("halui.program.run", True)
    time.sleep(0.1)
finally:
    set_hal_pin("halui.program.run", False)
    set_hal_pin("halui.mode.auto", False)
# Accepting Cycle Start does not prove that execution began. Confirm a real
# non-Idle transition before the scheduler is allowed to monitor completion.
started = False
start_deadline = time.time() + 5.0
last_interp_state = None
launch_messages = []
while time.time() < start_deadline:
    status.poll()
    launch_messages.extend(read_errors())
    raise_if_motion_locked_out()
    last_interp_state = getattr(status, "interp_state", None)
    if last_interp_state != linuxcnc.INTERP_IDLE:
        started = True
        break
    if getattr(status, "estop", False):
        raise RuntimeError("PathPilot entered E-stop before the program started.")
    if not getattr(status, "enabled", False):
        raise RuntimeError("PathPilot became disabled before the program started.")
    time.sleep(0.05)
if not started:
    state_detail = (
        "task_mode=" + str(getattr(status, "task_mode", None))
        + ", interp_state=" + str(last_interp_state)
        + ", task_state=" + str(getattr(status, "task_state", None))
        + ", exec_state=" + str(getattr(status, "exec_state", None))
        + ", paused=" + str(getattr(status, "paused", None))
        + ", queue=" + str(getattr(status, "queue", None))
    )
    error_detail = " | ".join(item["text"] for item in launch_messages)
    raise RuntimeError(
        "PathPilot accepted the HALUI Run request, but the interpreter never left Idle ("
        + state_detail + ")."
        + ((" Controller: " + error_detail) if error_detail else "")
    )
print("MONGO_CNC_RUN=" + json.dumps({{
    "accepted": True,
    "started": True,
    "filename": filename,
    "loaded_program": loaded_program,
    "interp_state": last_interp_state,
    "task_mode": getattr(status, "task_mode", None),
    "controller_messages": launch_messages,
}}))
'''
    # Mode switching, copying, loading, and start confirmation can legitimately
    # take longer than a short telemetry timeout on PathPilot.
    command_timeout = max(float(timeout), 20.0)
    return _read_remote_payload(host, port, username, password, command_timeout, remote_script, "MONGO_CNC_RUN=")


def abort_linuxcnc_program(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: float,
) -> dict:
    """Abort queued or active LinuxCNC execution and confirm the interpreter is Idle."""
    remote_script = r'''import json
import linuxcnc
import time

command = linuxcnc.command()
command.abort()
command.wait_complete()
status = linuxcnc.stat()
deadline = time.time() + 5.0
while time.time() < deadline:
    status.poll()
    if getattr(status, "interp_state", None) == linuxcnc.INTERP_IDLE:
        break
    time.sleep(0.05)
status.poll()
interp_state = getattr(status, "interp_state", None)
if interp_state != linuxcnc.INTERP_IDLE:
    raise RuntimeError("LinuxCNC did not return to Idle after the abort command.")
print("MONGO_CNC_ABORT=" + json.dumps({
    "aborted": True,
    "interp_state": interp_state,
    "program": getattr(status, "file", "") or "",
}))
'''
    return _read_remote_payload(
        host, port, username, password, timeout,
        remote_script, "MONGO_CNC_ABORT=",
    )
