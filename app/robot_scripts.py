from __future__ import annotations

import socket
from pathlib import Path, PurePosixPath

from app.robot_files import RobotFileAccessError, robot_sftp_client


GENERATED_REMOTE_DIRECTORY = "mongo-production-system"


def generated_script_directory(project_root: Path) -> Path:
    return project_root / "runtime" / "generated-robot-programs"


def _output_command(channel: dict, value: bool) -> str:
    functions = {
        "standard": "set_standard_digital_out",
        "configurable": "set_configurable_digital_out",
        "tool": "set_tool_digital_out",
    }
    return f"  {functions[channel['bank']]}({channel['index']}, {str(value)})"


def _pose(position: dict, orientation: dict, *, z_offset_mm: float = 0) -> str:
    return "p[{:.6f},{:.6f},{:.6f},{:.6f},{:.6f},{:.6f}]".format(
        float(position["x_mm"]) / 1000,
        float(position["y_mm"]) / 1000,
        (float(position["z_mm"]) + z_offset_mm) / 1000,
        float(orientation["rx_rad"]),
        float(orientation["ry_rad"]),
        float(orientation["rz_rad"]),
    )


def _xyz_comment(label: str, position: dict, *, z_offset_mm: float = 0) -> str:
    return "  # {} | X={:.3f} mm, Y={:.3f} mm, Z={:.3f} mm".format(
        label,
        float(position["x_mm"]),
        float(position["y_mm"]),
        float(position["z_mm"]) + z_offset_mm,
    )


def _append_move(
    lines: list[str],
    *,
    label: str,
    command: str,
    position: dict,
    orientation: dict,
    acceleration: float,
    speed: float,
    z_offset_mm: float = 0,
) -> None:
    lines.append(_xyz_comment(label, position, z_offset_mm=z_offset_mm))
    lines.append(
        f"  {command}({_pose(position, orientation, z_offset_mm=z_offset_mm)}, "
        f"a={acceleration}, v={speed:.3f})"
    )


def _append_output_action(lines: list[str], label: str, action: dict | None, wait_seconds: float) -> None:
    if not action:
        lines.append(f"  # {label} is not configured; no output command was generated")
        return
    output = action["output"]
    active_value = bool(action.get("active_value", True))
    lines.append(
        f"  # {label} using {output['bank']} digital output {output['index']} "
        f"({'ON' if active_value else 'OFF'})"
    )
    lines.append(_output_command(output, active_value))
    if action.get("pulse", True):
        lines.append("  sleep(0.25)")
        lines.append(_output_command(output, not active_value))
    if wait_seconds > 0:
        lines.append(f"  sleep({wait_seconds:.3f})")


def build_pallet_motion_script(
    *,
    function_name: str,
    operation: str,
    position: dict,
    generation: dict,
) -> str:
    """Create a self-contained URScript program for one known pallet position."""
    orientation = generation
    approach = dict(position)
    approach["y_mm"] = float(approach["y_mm"]) + generation["approach_y_clearance_mm"]
    waypoints = generation.get("travel_waypoints", [])
    pre_waypoint = generation["safe_pre_waypoint"]
    # The common safe waypoint is used both before entering and after leaving a pallet position.
    post_waypoint = pre_waypoint
    travel_speed = generation["max_travel_speed_rad_s"]
    precision_speed = generation["pickup_setdown_speed_m_s"]
    lines = ["# Generated pallet motion program", f"# Operation: {operation}", f"def {function_name}():"]
    _append_move(
        lines, label=f"Move to {pre_waypoint.get('name', 'shared safe waypoint')}", command="movej",
        position=pre_waypoint, orientation=pre_waypoint, acceleration=1.2, speed=travel_speed,
    )
    for waypoint in waypoints:
        _append_move(
            lines, label=f"Travel waypoint: {waypoint.get('name', 'unnamed')}", command="movej",
            position=waypoint, orientation=waypoint, acceleration=1.2, speed=travel_speed,
        )
    if operation == "pick":
        # Enter from the configured Y clearance, then lift the forked pallet vertically.
        _append_move(
            lines, label="Pallet approach position", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
        _append_move(
            lines, label="Pallet pickup position", command="movel", position=position,
            orientation=orientation, acceleration=0.4, speed=precision_speed,
        )
    else:
        # Set down from above, then withdraw the fork along the configured Y clearance.
        _append_move(
            lines, label="Pallet position from above", command="movel", position=position,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
        _append_move(
            lines, label="Pallet setdown position", command="movel", position=position,
            orientation=orientation, acceleration=0.4, speed=precision_speed,
        )
    grip = generation.get("grip_output")
    if grip:
        grip_value = generation["grip_closed_value"] if operation == "pick" else not generation["grip_closed_value"]
        lines.append(_output_command(grip, grip_value))
        lines.append("  sleep(0.35)")
    if operation == "pick":
        _append_move(
            lines, label="Lift pallet clear", command="movel", position=position,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
    else:
        _append_move(
            lines, label="Withdraw from pallet", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
    for waypoint in reversed(waypoints):
        _append_move(
            lines, label=f"Return travel waypoint: {waypoint.get('name', 'unnamed')}", command="movej",
            position=waypoint, orientation=waypoint, acceleration=1.2, speed=travel_speed,
        )
    _append_move(
        lines, label=f"Return to {post_waypoint.get('name', 'shared safe waypoint')}", command="movej",
        position=post_waypoint, orientation=post_waypoint, acceleration=1.2, speed=travel_speed,
    )
    lines.append("end")
    lines.append(f"{function_name}()")
    return "\n".join(lines) + "\n"


def build_mill_pallet_motion_script(
    *,
    function_name: str,
    operation: str,
    mill_pose: dict,
    entry_exit_pose: dict,
    generation: dict,
) -> str:
    """Create a manual load or unload URScript for the mill pallet station."""
    if operation not in {"load", "unload"}:
        raise ValueError(f"Unsupported mill pallet operation: {operation}")

    orientation = mill_pose
    approach = dict(mill_pose)
    approach["x_mm"] = float(approach["x_mm"]) + generation.get("mill_approach_x_clearance_mm", 100.0)
    lifted_approach = dict(approach)
    pre_waypoint = generation["safe_pre_waypoint"]
    waypoints = generation.get("travel_waypoints", [])
    travel_speed = generation["max_travel_speed_rad_s"]
    precision_speed = generation["pickup_setdown_speed_m_s"]

    action_wait = float(generation.get("mill_actuation_wait_seconds", 2.0))
    lines = ["# Generated mill pallet-transfer program", f"# Operation: {operation}", f"def {function_name}():"]
    _append_move(
        lines, label=f"Move to {pre_waypoint.get('name', 'shared safe waypoint')}", command="movej",
        position=pre_waypoint, orientation=pre_waypoint, acceleration=1.2, speed=travel_speed,
    )
    for waypoint in waypoints:
        _append_move(
            lines, label=f"Travel waypoint: {waypoint.get('name', 'unnamed')}", command="movej",
            position=waypoint, orientation=waypoint, acceleration=1.2, speed=travel_speed,
        )
    _append_output_action(lines, "Open mill door", generation.get("door_open_action"), action_wait)
    # Use Cartesian motion through the mill boundary. Joint interpolation can arc the TCP
    # toward the machine even when the destination has a larger base-frame X value.
    _append_move(
        lines, label=f"Linear mill entry: {entry_exit_pose.get('name', 'clearance')}", command="movel",
        position=entry_exit_pose, orientation=entry_exit_pose, acceleration=0.5, speed=precision_speed,
    )
    _append_output_action(lines, "Unlock Erowa system", generation.get("erowa_unlock_action"), action_wait)
    if operation == "unload":
        _append_move(
            lines, label="Mill pallet approach position", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
        _append_move(
            lines, label="Mill pallet pickup position", command="movel", position=mill_pose,
            orientation=orientation, acceleration=0.4, speed=precision_speed,
        )
    else:
        _append_move(
            lines, label="Mill pallet position from above", command="movel", position=mill_pose,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
        _append_move(
            lines, label="Mill pallet setdown position", command="movel", position=mill_pose,
            orientation=orientation, acceleration=0.4, speed=precision_speed,
        )

    grip = generation.get("grip_output")
    if grip:
        grip_value = generation["grip_closed_value"] if operation == "unload" else not generation["grip_closed_value"]
        lines.append(_output_command(grip, grip_value))
        lines.append("  sleep(0.35)")
    if operation == "unload":
        _append_move(
            lines, label="Lift mill pallet clear", command="movel", position=mill_pose,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
        _append_move(
            lines, label="Withdraw lifted pallet in positive X", command="movel", position=lifted_approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
    else:
        _append_move(
            lines, label="Withdraw fork in positive X", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
    _append_move(
        lines, label=f"Linear retract to mill entry/exit: {entry_exit_pose.get('name', 'clearance')}", command="movel",
        position=entry_exit_pose, orientation=entry_exit_pose, acceleration=0.5, speed=precision_speed,
    )
    _append_output_action(lines, "Lock Erowa system", generation.get("erowa_lock_action"), action_wait)
    _append_output_action(lines, "Close mill door", generation.get("door_close_action"), action_wait)
    for waypoint in reversed(waypoints):
        _append_move(
            lines, label=f"Linear return waypoint: {waypoint.get('name', 'unnamed')}", command="movel",
            position=waypoint, orientation=waypoint, acceleration=0.5, speed=precision_speed,
        )
    _append_move(
        lines, label=f"Linear return to {pre_waypoint.get('name', 'shared safe waypoint')}", command="movel",
        position=pre_waypoint, orientation=pre_waypoint, acceleration=0.5, speed=precision_speed,
    )
    lines.append("end")
    lines.append(f"{function_name}()")
    return "\n".join(lines) + "\n"


def sync_generated_scripts(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    root_directory: str,
    timeout_seconds: float,
    local_directory: Path,
    files: dict[str, str],
) -> dict[str, str]:
    """Stage every artifact first, then replace remote and local copies as one rebuild."""
    root = PurePosixPath(root_directory)
    remote_directory = root / GENERATED_REMOTE_DIRECTORY
    local_directory.mkdir(parents=True, exist_ok=True)
    staged = local_directory / ".staging"
    staged.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (staged / name).write_text(content, encoding="utf-8", newline="\n")

    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            current = PurePosixPath("/")
            for part in remote_directory.parts[1:]:
                current /= part
                try:
                    sftp.stat(str(current))
                except OSError:
                    sftp.mkdir(str(current))
            for name in files:
                target = remote_directory / name
                temporary = remote_directory / f".{name}.mps-new"
                with (staged / name).open("rb") as source, sftp.open(str(temporary), "wb") as destination:
                    while chunk := source.read(65536):
                        destination.write(chunk)
                try:
                    sftp.posix_rename(str(temporary), str(target))
                except (AttributeError, OSError):
                    try:
                        sftp.remove(str(target))
                    except OSError:
                        pass
                    sftp.rename(str(temporary), str(target))
    except RobotFileAccessError:
        raise

    for name in files:
        (staged / name).replace(local_directory / name)
    try:
        staged.rmdir()
    except OSError:
        pass
    return {name: str(remote_directory / name) for name in files}


def run_robot_script(host: str, content: str, timeout_seconds: float) -> None:
    try:
        with socket.create_connection((host, 30002), timeout=timeout_seconds) as connection:
            connection.settimeout(timeout_seconds)
            connection.sendall(content.encode("utf-8"))
    except OSError as exc:
        raise RobotFileAccessError(f"Could not send generated URScript to the robot: {exc}") from exc
