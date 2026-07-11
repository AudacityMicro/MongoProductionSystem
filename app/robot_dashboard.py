from __future__ import annotations

import socket


class RobotDashboardError(Exception):
    pass


def _dashboard_command(host: str, command: str, timeout_seconds: float) -> str:
    try:
        with socket.create_connection((host, 29999), timeout=timeout_seconds) as connection:
            connection.settimeout(timeout_seconds)
            # The server sends a banner immediately after a connection opens.
            connection.recv(1024)
            connection.sendall(f"{command}\n".encode("utf-8"))
            response = connection.recv(4096).decode("utf-8", errors="replace").strip()
    except OSError as exc:
        raise RobotDashboardError(f"Dashboard connection failed: {exc}") from exc
    if not response:
        raise RobotDashboardError("Dashboard server did not return a response.")
    return response


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

    load_response = _dashboard_command(host, f"load {filename}", timeout_seconds)
    if not load_response.lower().startswith("loading program"):
        raise RobotDashboardError(f"Dashboard could not load '{filename}': {load_response}")
    play_response = _dashboard_command(host, "play", timeout_seconds)
    if not play_response.lower().startswith("starting program"):
        raise RobotDashboardError(f"Dashboard could not start '{filename}': {play_response}")
    return play_response
