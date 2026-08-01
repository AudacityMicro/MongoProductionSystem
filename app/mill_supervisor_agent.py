#!/usr/bin/env python3
"""PathPilot-side MPS mill supervisor asset.

This file is uploaded and launched only by the explicit future bootstrap action.
It intentionally supports Python 3.4 and has no third-party dependencies.
"""
from __future__ import print_function

import json
import os
import socket
import struct
import subprocess
import sys
import time

PROTOCOL = 1
MAX_FRAME = 262144
AGENT_VERSION = "1.0.0-staged"


def send_message(sock, message):
    body = json.dumps(message, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)) + body)


def receive_messages(sock, buffer):
    chunk = sock.recv(8192)
    if not chunk:
        raise IOError("server closed connection")
    buffer.extend(chunk)
    messages = []
    while len(buffer) >= 4:
        length = struct.unpack("!I", bytes(buffer[:4]))[0]
        if length < 2 or length > MAX_FRAME:
            raise IOError("invalid frame length")
        if len(buffer) < length + 4:
            break
        raw = bytes(buffer[4:length + 4])
        del buffer[:length + 4]
        messages.append(json.loads(raw.decode("utf-8")))
    return messages


def atomic_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)


def read_json(path):
    try:
        with open(path) as stream:
            return json.load(stream)
    except Exception:
        return {}


def snapshot(status):
    status.poll()
    return {
        "task_state": getattr(status, "task_state", None),
        "task_mode": getattr(status, "task_mode", None),
        "interp_state": getattr(status, "interp_state", None),
        "program": getattr(status, "file", "") or "",
        "tool_in_spindle": getattr(status, "tool_in_spindle", None),
        "estop": bool(getattr(status, "estop", False)),
        "enabled": bool(getattr(status, "enabled", False)),
        "paused": bool(getattr(status, "paused", False)),
        "actual_position": list(getattr(status, "actual_position", [])),
    }


def command_error_channel():
    import linuxcnc
    return linuxcnc.error_channel()


def read_errors(channel):
    messages = []
    for _unused in range(50):
        item = channel.poll()
        if not item:
            break
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            item = item[1]
        if isinstance(item, bytes):
            item = item.decode("utf-8", "replace")
        messages.append(str(item))
    return messages


def status_signature(path):
    try:
        info = os.stat(path)
        return [int(info.st_mtime), int(info.st_size)]
    except Exception:
        return None


def completed_status(path, program, previous_signature):
    """Require the dedicated MPS completion proof before reporting success."""
    signature = status_signature(path)
    if signature is None:
        return False, "Mill completion status file was not available."
    if previous_signature is not None and signature == previous_signature:
        return False, "Mill completion status file did not change for this program."
    try:
        fields = {}
        states = []
        for line in open(path):
            key, separator, value = line.partition(":")
            if not separator:
                continue
            key = key.strip().upper()
            value = value.strip()
            if key == "STATE":
                states.append(value.upper())
            else:
                fields[key] = value
    except Exception as exc:
        return False, "Mill completion status file could not be read: " + str(exc)
    if fields.get("VERSION") != "MPS-MILL-STATUS-V1":
        return False, "Mill completion status file has an unsupported format."
    if fields.get("PROGRAM") != os.path.basename(program):
        return False, "Mill completion status file does not identify the dispatched program."
    if not states or states[0] != "STARTED" or states[-1] != "COMPLETED":
        return False, "Mill program did not report normal completion."
    return True, ""


def set_hal_pin(name, value):
    subprocess.check_call(["/home/operator/tmc/bin/halcmd", "setp", name, "TRUE" if value else "FALSE"])


def optional_hal_pin(name):
    try:
        value = subprocess.check_output(["/home/operator/tmc/bin/halcmd", "getp", name]).strip().upper()
        return value == b"TRUE" or value == "TRUE"
    except Exception:
        return None


def wait_for_mode(status, command, linuxcnc, mode, label):
    status.poll()
    if getattr(status, "task_mode", None) != mode:
        command.mode(mode)
        command.wait_complete()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        status.poll()
        if getattr(status, "task_mode", None) == mode:
            return
        time.sleep(0.05)
    raise RuntimeError("PathPilot did not enter " + label + " mode.")


def start_program(status, filename, require_a_axis_homed, program_root):
    """Open the selected file through PathPilot, then use Auto and HALUI start."""
    import linuxcnc
    target = os.path.realpath(filename)
    root = os.path.realpath(program_root)
    if target != root and not target.startswith(root + os.sep):
        raise RuntimeError("Requested program is outside the configured G-code directory.")
    if not os.path.isfile(target):
        raise RuntimeError("Requested program does not exist on PathPilot.")
    status.poll()
    if getattr(status, "estop", False) or not getattr(status, "enabled", False):
        raise RuntimeError("PathPilot is not enabled or is in E-stop.")
    if getattr(status, "interp_state", linuxcnc.INTERP_IDLE) != linuxcnc.INTERP_IDLE:
        raise RuntimeError("PathPilot is already running or paused.")
    homed = list(getattr(status, "homed", []))
    required = 4 if require_a_axis_homed else 3
    if homed and (len(homed) < required or not all(bool(value) for value in homed[:required])):
        raise RuntimeError("Required PathPilot axes are not homed.")
    command = linuxcnc.command()
    errors = command_error_channel()
    wait_for_mode(status, command, linuxcnc, linuxcnc.MODE_MDI, "MDI")
    wait_for_mode(status, command, linuxcnc, linuxcnc.MODE_AUTO, "Auto")
    # Load the actual selected file. PathPilot then shows the real filename,
    # builds its normal preview, and keeps relative includes rooted correctly.
    # Keep this to LinuxCNC's documented one-argument API so PathPilot owns
    # the normal filename, source view, and preview refresh.
    command.program_open(target)
    command.wait_complete()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        status.poll()
        if os.path.realpath(getattr(status, "file", "") or "") == target:
            break
        time.sleep(0.05)
    if os.path.realpath(getattr(status, "file", "") or "") != target:
        raise RuntimeError("PathPilot did not load the requested program.")
    wait_for_mode(status, command, linuxcnc, linuxcnc.MODE_AUTO, "Auto")
    read_errors(errors)
    set_hal_pin("halui.program.run", False)
    set_hal_pin("halui.mode.auto", False)
    run_pin_observed = False
    try:
        set_hal_pin("halui.mode.auto", True)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            status.poll()
            if optional_hal_pin("halui.mode.is-auto") and getattr(status, "task_mode", None) == linuxcnc.MODE_AUTO:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("PathPilot did not acknowledge Auto mode.")
        set_hal_pin("halui.program.run", True)
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if optional_hal_pin("halui.program.is-running") is True:
                run_pin_observed = True
                break
            time.sleep(0.05)
    finally:
        set_hal_pin("halui.program.run", False)
        set_hal_pin("halui.mode.auto", False)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        status.poll()
        if getattr(status, "interp_state", None) != linuxcnc.INTERP_IDLE or run_pin_observed:
            return {"program": target, "run_pin_observed": run_pin_observed}
        time.sleep(0.05)
    raise RuntimeError("PathPilot accepted Cycle Start but did not leave Idle: " + " | ".join(read_errors(errors)))


def main(config_path):
    config = read_json(config_path)
    state_path = config["state_path"]
    state = read_json(state_path)
    session = int(state.get("session", int(time.time())))
    state["session"] = session
    state.setdefault("last_sequence", 0)
    state.setdefault("last_result", {})
    active = state.get("active") if isinstance(state.get("active"), dict) else None
    import linuxcnc
    status = linuxcnc.stat()
    buffer = bytearray()
    reconnect = [1, 2, 5, 10]
    reconnect_index = 0
    while True:
        sock = None
        try:
            sock = socket.create_connection((config["host"], int(config["port"])), 10)
            sock.settimeout(1.0)
            send_message(sock, {"protocol": PROTOCOL, "kind": "hello", "session": session, "last_sequence": state["last_sequence"], "last_result": state["last_result"]})
            reconnect_index = 0
            last_telemetry = 0.0
            while True:
                now = time.time()
                if now - last_telemetry >= float(config.get("telemetry_seconds", 1.0)):
                    send_message(sock, {"protocol": PROTOCOL, "kind": "telemetry", "snapshot": snapshot(status)})
                    last_telemetry = now
                if active:
                    current = snapshot(status)
                    if current["interp_state"] == 1:
                        if active.get("require_completion_status", False):
                            complete, detail = completed_status(
                                config["status_path"], active["program"], active.get("status_signature")
                            )
                        else:
                            complete, detail = True, ""
                        result = {"program": active["program"], "idle_at": int(time.time())}
                        event = "completed" if complete else "latched"
                        state["last_result"] = {
                            "sequence": active["sequence"], "event": event, "detail": detail, "result": result
                        }
                        state.pop("active", None)
                        atomic_json(state_path, state)
                        send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": active["sequence"], "event": event, "detail": detail, "result": result})
                        active = None
                try:
                    for message in receive_messages(sock, buffer):
                        if message.get("kind") == "command":
                            sequence = int(message["sequence"])
                            if sequence <= state["last_sequence"]:
                                if sequence == state["last_sequence"]:
                                    result = state["last_result"]
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": result.get("event", "latched"), "detail": result.get("detail", "Duplicate command sequence."), "result": result.get("result", {})})
                                else:
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "latched", "detail": "Stale command sequence was rejected."})
                            elif not config.get("execution_enabled", False):
                                state["last_sequence"] = sequence
                                state["last_result"] = {"sequence": sequence, "event": "latched", "detail": "Mill supervisor command execution is not activated."}
                                atomic_json(state_path, state)
                                send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "latched", "detail": "Mill supervisor command execution is not activated."})
                            else:
                                state["last_sequence"] = sequence
                                atomic_json(state_path, state)
                                operation = message.get("operation")
                                if operation == "probe" and not active:
                                    result = {"idle": True, "timestamp": int(time.time())}
                                    state["last_result"] = {
                                        "sequence": sequence, "event": "completed", "result": result
                                    }
                                    atomic_json(state_path, state)
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "accepted"})
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "completed", "result": result})
                                    continue
                                if operation != "run_program" or active:
                                    state["last_result"] = {"sequence": sequence, "event": "latched", "detail": "Unsupported or conflicting mill supervisor command."}
                                    atomic_json(state_path, state)
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "latched", "detail": "Unsupported or conflicting mill supervisor command."})
                                    continue
                                send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "accepted"})
                                try:
                                    previous_status_signature = status_signature(config["status_path"])
                                    details = start_program(status, message.get("arguments", {}).get("program", ""), bool(message.get("arguments", {}).get("require_a_axis_homed", False)), config["program_root"])
                                    active = {
                                        "sequence": sequence,
                                        "program": details["program"],
                                        "status_signature": previous_status_signature,
                                        "require_completion_status": bool(message.get("arguments", {}).get("require_completion_status", False)),
                                    }
                                    state["active"] = active
                                    atomic_json(state_path, state)
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "running", "result": details})
                                except Exception as exc:
                                    state["last_result"] = {"sequence": sequence, "event": "faulted", "detail": str(exc)}
                                    atomic_json(state_path, state)
                                    send_message(sock, {"protocol": PROTOCOL, "kind": "event", "sequence": sequence, "event": "faulted", "detail": str(exc)})
                except socket.timeout:
                    pass
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        time.sleep(reconnect[reconnect_index])
        reconnect_index = min(reconnect_index + 1, len(reconnect) - 1)


if __name__ == "__main__":
    main(sys.argv[1])
