from __future__ import annotations

import socket
import time


class RobotDashboardError(Exception):
    pass


class _DashboardSession:
    def __init__(self, host: str, timeout_seconds: float):
        self.host = host
        self.timeout_seconds = timeout_seconds
        self.connection: socket.socket | None = None

    def __enter__(self) -> "_DashboardSession":
        try:
            self.connection = socket.create_connection((self.host, 29999), timeout=self.timeout_seconds)
            self.connection.settimeout(self.timeout_seconds)
            # The server sends a banner immediately after a connection opens.
            self.connection.recv(1024)
        except OSError as exc:
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
            response = self.connection.recv(4096).decode("utf-8", errors="replace").strip()
        except OSError as exc:
            raise RobotDashboardError(f"Dashboard command '{command}' failed: {exc}") from exc
        if not response:
            raise RobotDashboardError(f"Dashboard did not respond to '{command}'.")
        return response


def _dashboard_command(host: str, command: str, timeout_seconds: float) -> str:
    try:
        with _DashboardSession(host, timeout_seconds) as dashboard:
            return dashboard.command(command)
    except RobotDashboardError:
        raise


def loaded_robot_program(host: str, timeout_seconds: float) -> str | None:
    response = _dashboard_command(host, "get loaded program", timeout_seconds)
    prefix = "Loaded program:"
    if response.startswith(prefix):
        program = response.removeprefix(prefix).strip()
        return program or None
    if "No program loaded" in response:
        return None
    raise RobotDashboardError(response)


def run_robot_program(host: str, filename: str, timeout_seconds: float) -> str:
    filename = filename.strip()
    if not filename:
        raise RobotDashboardError("A controller program filename is required.")
    if "\n" in filename or "\r" in filename:
        raise RobotDashboardError("Program filename cannot contain a line break.")

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

        play_response = dashboard.command("play")
        if not play_response.lower().startswith("starting program"):
            raise RobotDashboardError(f"Dashboard could not start '{filename}': {play_response}")
        return play_response
