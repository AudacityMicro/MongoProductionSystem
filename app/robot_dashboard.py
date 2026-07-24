from __future__ import annotations

import socket
import time
from threading import RLock, Thread

from app.robot_transport import robot_command_lock


class RobotDashboardError(Exception):
    pass


_LOADED_PROGRAM_CACHE: dict[str, tuple[float, str | None]] = {}
_LOADED_PROGRAM_REFRESHING: set[str] = set()
_LOADED_PROGRAM_LOCK = RLock()
_DASHBOARD_HEALTH_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_DASHBOARD_HEALTH_LOCK = RLock()
_DASHBOARD_HEALTH_MAX_AGE_SECONDS = 30.0


class _DashboardSession:
    def __init__(self, host: str, timeout_seconds: float):
        self.host = host
        # Dashboard is low-rate and command-oriented. A short user setting is
        # useful for sample freshness, but too aggressive for a fresh TCP/banner
        # exchange on the shop network.
        self.timeout_seconds = max(5.0, timeout_seconds)
        self.connection: socket.socket | None = None
        self.buffer = bytearray()

    def _receive_line(self) -> str:
        if self.connection is None:
            raise RobotDashboardError("Dashboard connection is not open.")
        try:
            while b"\n" not in self.buffer:
                chunk = self.connection.recv(4096)
                if not chunk:
                    raise RobotDashboardError("Dashboard closed the connection before replying.")
                self.buffer.extend(chunk)
        except OSError as exc:
            raise RobotDashboardError(f"Dashboard receive failed: {exc}") from exc
        line, _, remainder = self.buffer.partition(b"\n")
        self.buffer = bytearray(remainder)
        return line.decode("utf-8", errors="replace").strip()

    def __enter__(self) -> "_DashboardSession":
        try:
            self.connection = socket.create_connection((self.host, 29999), timeout=self.timeout_seconds)
            self.connection.settimeout(self.timeout_seconds)
            # The server sends a banner immediately after a connection opens.
            self._receive_line()
        except (OSError, RobotDashboardError) as exc:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            raise RobotDashboardError(f"Dashboard connection failed: {exc}") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            self.connection.close()

    def command(self, command: str) -> str:
        if self.connection is None:
            raise RobotDashboardError("Dashboard connection is not open.")
        try:
            self.connection.sendall(f"{command}\n".encode("utf-8"))
            response = self._receive_line()
        except OSError as exc:
            raise RobotDashboardError(f"Dashboard command '{command}' failed: {exc}") from exc
        if not response:
            raise RobotDashboardError(f"Dashboard did not respond to '{command}'.")
        return response


def _dashboard_command(host: str, command: str, timeout_seconds: float) -> str:
    with robot_command_lock(host):
        with _DashboardSession(host, timeout_seconds) as dashboard:
            return dashboard.command(command)


def _record_dashboard_reachable(host: str, response: str) -> None:
    with _DASHBOARD_HEALTH_LOCK:
        _DASHBOARD_HEALTH_CACHE[host] = (
            time.monotonic(),
            {"reachable": True, "response": response, "error": None},
        )


def robot_dashboard_health(host: str, timeout_seconds: float) -> dict[str, object]:
    """Probe controller reachability without turning UI polling into a connection storm."""
    with _DASHBOARD_HEALTH_LOCK:
        now = time.monotonic()
        cached = _DASHBOARD_HEALTH_CACHE.get(host)
        if cached and now - cached[0] < _DASHBOARD_HEALTH_MAX_AGE_SECONDS:
            return dict(cached[1])

        try:
            response = _dashboard_command(host, "robotmode", timeout_seconds)
            result: dict[str, object] = {"reachable": True, "response": response, "error": None}
        except RobotDashboardError as exc:
            result = {"reachable": False, "response": None, "error": str(exc)}
        _DASHBOARD_HEALTH_CACHE[host] = (time.monotonic(), result)
        return dict(result)


def _refresh_loaded_program(host: str, timeout_seconds: float) -> None:
    try:
        response = _dashboard_command(host, "get loaded program", timeout_seconds)
        prefix = "Loaded program:"
        if response.startswith(prefix):
            program = response.removeprefix(prefix).strip() or None
        elif "No program loaded" in response:
            program = None
        else:
            return
        with _LOADED_PROGRAM_LOCK:
            _LOADED_PROGRAM_CACHE[host] = (time.monotonic(), program)
    except RobotDashboardError:
        pass
    finally:
        with _LOADED_PROGRAM_LOCK:
            _LOADED_PROGRAM_REFRESHING.discard(host)


def loaded_robot_program(host: str, timeout_seconds: float) -> str | None:
    with _LOADED_PROGRAM_LOCK:
        cached = _LOADED_PROGRAM_CACHE.get(host)
        now = time.monotonic()
        if cached and now - cached[0] < 5:
            return cached[1]
        if host not in _LOADED_PROGRAM_REFRESHING:
            _LOADED_PROGRAM_REFRESHING.add(host)
            Thread(
                target=_refresh_loaded_program,
                args=(host, timeout_seconds),
                daemon=True,
                name="robot-dashboard-refresh",
            ).start()
        return cached[1] if cached else None


def run_robot_program(host: str, filename: str, timeout_seconds: float) -> str:
    filename = filename.strip()
    if not filename:
        raise RobotDashboardError("A controller program filename is required.")
    if "\n" in filename or "\r" in filename:
        raise RobotDashboardError("Program filename cannot contain a line break.")

    with robot_command_lock(host):
        with _DashboardSession(host, timeout_seconds) as dashboard:
            load_response = dashboard.command(f"load {filename}")
            if not load_response.lower().startswith("loading program"):
                raise RobotDashboardError(f"Dashboard could not load '{filename}': {load_response}")

            # Do not play until the controller confirms the requested file is loaded.
            # Otherwise a delayed load can start whatever program was loaded previously.
            deadline = time.monotonic() + max(timeout_seconds, 1.0)
            loaded_program = ""
            while time.monotonic() < deadline:
                response = dashboard.command("get loaded program")
                if response.startswith("Loaded program:"):
                    loaded_program = response.removeprefix("Loaded program:").strip()
                    if loaded_program == filename:
                        break
                time.sleep(0.1)
            else:
                raise RobotDashboardError(
                    f"Controller did not confirm '{filename}' as loaded; it reported '{loaded_program or 'no program'}'. Play was not sent."
                )

            with _LOADED_PROGRAM_LOCK:
                _LOADED_PROGRAM_CACHE[host] = (time.monotonic(), filename)

            play_response = dashboard.command("play")
            if not play_response.lower().startswith("starting program"):
                raise RobotDashboardError(f"Dashboard could not start '{filename}': {play_response}")
    _record_dashboard_reachable(host, play_response)
    return play_response


def robot_program_running(host: str, timeout_seconds: float) -> bool:
    response = _dashboard_command(host, "running", timeout_seconds)
    normalized = response.strip().casefold()
    if normalized.endswith("true"):
        return True
    if normalized.endswith("false"):
        return False
    raise RobotDashboardError(f"Dashboard returned an unrecognized running state: {response}")


def robot_program_status(host: str, timeout_seconds: float) -> dict[str, object]:
    """Read run state and loaded program together for conservative idle confirmation."""
    with robot_command_lock(host):
        with _DashboardSession(host, timeout_seconds) as dashboard:
            running_response = dashboard.command("running")
            normalized = running_response.strip().casefold()
            if normalized.endswith("true"):
                running = True
            elif normalized.endswith("false"):
                running = False
            else:
                raise RobotDashboardError(f"Dashboard returned an unrecognized running state: {running_response}")
            loaded_response = dashboard.command("get loaded program")
            if loaded_response.startswith("Loaded program:"):
                loaded_program = loaded_response.removeprefix("Loaded program:").strip() or None
            elif "No program loaded" in loaded_response:
                loaded_program = None
            else:
                raise RobotDashboardError(f"Dashboard returned an unrecognized loaded-program state: {loaded_response}")
    _record_dashboard_reachable(host, running_response)
    return {"running": running, "loaded_program": loaded_program}


def clear_robot_fault(host: str, timeout_seconds: float) -> dict:
    """Clear only the recoverable safety condition reported by a CB-series controller."""
    with robot_command_lock(host):
        with _DashboardSession(host, timeout_seconds) as dashboard:
            safety_response = dashboard.command("safetymode")
            if ":" not in safety_response:
                raise RobotDashboardError(f"Dashboard returned an unrecognized safety mode: {safety_response}")
            safety_mode = safety_response.split(":", 1)[1].strip().upper().replace(" ", "_")
            responses: list[str] = [safety_response]

            if safety_mode == "PROTECTIVE_STOP":
                responses.append(dashboard.command("close safety popup"))
                result = dashboard.command("unlock protective stop")
                responses.append(result)
                if not result.lower().startswith("protective stop releasing"):
                    raise RobotDashboardError(f"Protective stop was not released: {result}")
                action = "protective_stop_unlocked"
                message = "Protective stop release was accepted. The program was not resumed."
            elif safety_mode in {"FAULT", "VIOLATION"}:
                responses.append(dashboard.command("close safety popup"))
                result = dashboard.command("restart safety")
                responses.append(result)
                if not result.lower().startswith("restarting safety"):
                    raise RobotDashboardError(f"Safety restart was not accepted: {result}")
                action = "safety_restarted"
                message = "Safety restart was accepted. The arm remains powered off; no program was resumed."
            elif safety_mode in {"NORMAL", "REDUCED"}:
                result = dashboard.command("close popup")
                responses.append(result)
                if not result.lower().startswith("closing popup"):
                    raise RobotDashboardError(f"Controller popup was not closed: {result}")
                action = "popup_closed"
                message = "No recoverable safety fault was active. The controller popup was closed."
            else:
                readable = safety_mode.replace("_", " ").title()
                raise RobotDashboardError(
                    f"{readable} requires the physical safety condition to be released at the cell; it cannot be cleared from this UI."
                )

    _record_dashboard_reachable(host, safety_response)

    return {
        "safety_mode_before": safety_mode,
        "action": action,
        "message": message,
        "responses": responses,
    }
