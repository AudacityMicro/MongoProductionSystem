from __future__ import annotations

import math
import socket
import time
from pathlib import Path, PurePosixPath

from app.robot_files import RobotFileAccessError, robot_sftp_client
from app.robot_transport import robot_command_lock


GENERATED_REMOTE_DIRECTORY = "mongo-production-system"
PALLET_MOTION_SCRIPT_REVISION = 14
UNLOADED_TOOL_PAYLOAD_KG = 5 * 0.028349523125
_PALLET_PAYLOAD_ASSIGNMENT_PREFIX = "global mongo_pallet_payload_kg = "
_SUPERVISOR_SEQUENCE_PREFIX = "global mongo_last_sequence = "


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


def _joints(waypoint: dict) -> str:
    joints = waypoint["joints_rad"]
    if not isinstance(joints, list) or len(joints) != 6:
        raise ValueError("Joint waypoint must contain six joint values.")
    return "[{}]".format(",".join(f"{float(joint):.6f}" for joint in joints))


def _joint_comment(label: str, waypoint: dict) -> str:
    joints = waypoint["joints_rad"]
    return "  # {} | J0..J5 = {}".format(label, ", ".join(f"{float(joint):.3f}" for joint in joints))


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


def _append_joint_move(
    lines: list[str],
    *,
    label: str,
    waypoint: dict,
    acceleration: float,
    speed: float,
) -> None:
    lines.append(_joint_comment(label, waypoint))
    lines.append(f"  movej({_joints(waypoint)}, a={acceleration}, v={speed:.3f})")


def _assigned_intermediate_safe_poses(generation: dict, position: dict) -> list[dict]:
    """Return the ordered joint-safe poses explicitly assigned to this pool position."""
    slot = position.get("slot")
    if not isinstance(slot, int):
        return []
    poses = generation.get("intermediate_safe_poses", [])
    if not isinstance(poses, list):
        return []
    return [
        pose for pose in poses
        if isinstance(pose, dict) and slot in pose.get("pool_slots", [])
    ]


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


def _append_payload(lines: list[str], label: str, payload_expression: str) -> None:
    lines.append(f"  # {label}")
    lines.append(f"  set_payload({payload_expression})")


def with_pallet_payload(script: str, weight_kg: float) -> str:
    """Inject one pallet's stored mass into a generated script immediately before dispatch."""
    if not math.isfinite(weight_kg) or weight_kg <= 0:
        raise ValueError("Pallet payload weight must be a finite positive value in kilograms.")
    marker = _PALLET_PAYLOAD_ASSIGNMENT_PREFIX
    if marker not in script:
        # Hand-authored programs are allowed, but they own their payload behavior.
        return script
    before, remainder = script.split(marker, 1)
    _, newline, after = remainder.partition("\n")
    if not newline:
        raise ValueError("Generated pallet-motion script has an invalid payload assignment.")
    return f"{before}{marker}{weight_kg:.6f}{newline}{after}"


def with_supervisor_sequence(script: str, sequence: int) -> str:
    """Seed a newly bootstrapped supervisor with the durable backend sequence."""
    if sequence < 0:
        raise ValueError("Supervisor sequence cannot be negative.")
    marker = _SUPERVISOR_SEQUENCE_PREFIX
    if marker not in script:
        raise ValueError("Generated supervisor script has no sequence assignment.")
    before, remainder = script.split(marker, 1)
    _, newline, after = remainder.partition("\n")
    if not newline:
        raise ValueError("Generated supervisor sequence assignment is malformed.")
    return f"{before}{marker}{sequence}{newline}{after}"


def build_pallet_motion_script(
    *,
    function_name: str,
    operation: str,
    position: dict,
    generation: dict,
    invoke: bool = True,
) -> str:
    """Create a self-contained URScript program for one known pallet position."""
    orientation = generation
    approach = dict(position)
    approach["y_mm"] = float(approach["y_mm"]) + generation["approach_y_clearance_mm"]
    lifted_approach = dict(approach)
    intermediate_poses = _assigned_intermediate_safe_poses(generation, position)
    pre_waypoint = generation["safe_pre_waypoint"]
    # The common safe waypoint is used both before entering and after leaving a pallet position.
    post_waypoint = pre_waypoint
    travel_speed = generation["max_travel_speed_rad_s"]
    precision_speed = generation["pickup_setdown_speed_m_s"]
    lines = [
        f"def {function_name}():",
        "  # Generated pallet motion program",
        f"  # Operation: {operation}",
        "  # The dispatcher replaces this mass with the scheduled pallet's stored weight.",
        f"  {_PALLET_PAYLOAD_ASSIGNMENT_PREFIX}{UNLOADED_TOOL_PAYLOAD_KG:.6f}",
    ]
    if operation == "pick":
        _append_payload(lines, "Set unloaded fork payload to 5 oz", f"{UNLOADED_TOOL_PAYLOAD_KG:.6f}")
    else:
        _append_payload(lines, "Set held pallet payload from pallet record", "mongo_pallet_payload_kg")
    _append_joint_move(
        lines, label=f"Move to {pre_waypoint.get('name', 'shared safe waypoint')}",
        waypoint=pre_waypoint, acceleration=1.2, speed=travel_speed,
    )
    for waypoint in intermediate_poses:
        _append_joint_move(
            lines, label=f"Intermediate safe pose: {waypoint.get('name', 'unnamed')}",
            waypoint=waypoint, acceleration=1.2, speed=travel_speed,
        )
    if operation == "pick":
        # Enter at the taught pallet height through the configured Y approach clearance.
        _append_move(
            lines, label="Pallet approach position", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
        _append_move(
            lines, label="Pallet pickup position", command="movel", position=position,
            orientation=orientation, acceleration=0.4, speed=precision_speed,
        )
    else:
        # Enter above the pallet through the outside clearance so no diagonal crosses the rack.
        _append_move(
            lines, label="Pallet outside/high clearance", command="movel", position=lifted_approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
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
        _append_payload(lines, "Set held pallet payload from pallet record", "mongo_pallet_payload_kg")
        _append_move(
            lines, label="Lift pallet clear", command="movel", position=position,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
        _append_move(
            lines, label="Retract lifted pallet by Y approach clearance", command="movel",
            position=lifted_approach, orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=generation["lift_z_clearance_mm"],
        )
    else:
        _append_payload(lines, "Set unloaded fork payload to 5 oz after setdown", f"{UNLOADED_TOOL_PAYLOAD_KG:.6f}")
        _append_move(
            lines, label="Retract fork by Y approach clearance", command="movel", position=approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
        )
    for waypoint in reversed(intermediate_poses):
        _append_joint_move(
            lines, label=f"Return via intermediate safe pose: {waypoint.get('name', 'unnamed')}",
            waypoint=waypoint, acceleration=1.2, speed=travel_speed,
        )
    _append_joint_move(
        lines, label=f"Return to {post_waypoint.get('name', 'shared safe waypoint')}",
        waypoint=post_waypoint, acceleration=1.2, speed=travel_speed,
    )
    lines.append("end")
    return "\n".join(lines) + "\n"


def build_reliability_motion_script(
    *,
    function_name: str,
    position: dict,
    staging_pose: dict,
    generation: dict,
    include_helpers: bool = True,
) -> str:
    """Pick one pool pallet, visit outer mill staging, and return it to the same slot."""
    slot = int(position["slot"])
    pick_function = f"mps_pick_pool_{slot:03d}"
    put_function = f"mps_put_pool_{slot:03d}"
    lines = [
        f"def {function_name}():",
        "  # Generated queue reliability cycle",
        f"  # Pool position: {slot:03d}",
        "  # This program never opens the mill door, unlocks Erowa, or enters the mill.",
        "  # The dispatcher replaces this mass with the queued pallet's stored weight.",
        f"  {_PALLET_PAYLOAD_ASSIGNMENT_PREFIX}{UNLOADED_TOOL_PAYLOAD_KG:.6f}",
    ]
    if include_helpers:
        pick_script = _supervisor_function(build_pallet_motion_script(
            function_name=pick_function,
            operation="pick",
            position=position,
            generation=generation,
            invoke=False,
        ))
        put_script = _supervisor_function(build_pallet_motion_script(
            function_name=put_function,
            operation="put",
            position=position,
            generation=generation,
            invoke=False,
        ))
        lines.extend(f"  {line}" if line else "" for line in pick_script.splitlines())
        lines.extend(f"  {line}" if line else "" for line in put_script.splitlines())
    lines.append(f"  {pick_function}()")
    _append_move(
        lines,
        label=f"Reliability test outer mill staging: {staging_pose.get('name', 'Mill pre-entry')}",
        command="movel",
        position=staging_pose,
        orientation=staging_pose,
        acceleration=0.5,
        speed=generation["pickup_setdown_speed_m_s"],
    )
    lines.append(f"  {put_function}()")
    lines.append("end")
    return "\n".join(lines) + "\n"


def build_mill_pallet_motion_script(
    *,
    function_name: str,
    operation: str,
    mill_pose: dict,
    pre_entry_pose: dict,
    entry_exit_pose: dict,
    generation: dict,
    invoke: bool = True,
) -> str:
    """Create a manual load or unload URScript for the mill pallet station."""
    if operation not in {"load", "unload"}:
        raise ValueError(f"Unsupported mill pallet operation: {operation}")

    orientation = mill_pose
    approach = dict(mill_pose)
    approach["x_mm"] = float(approach["x_mm"]) + generation.get("mill_approach_x_clearance_mm", 100.0)
    lifted_approach = dict(approach)
    pre_waypoint = generation["safe_pre_waypoint"]
    travel_speed = generation["max_travel_speed_rad_s"]
    precision_speed = generation["pickup_setdown_speed_m_s"]
    mill_lift_z_clearance = generation.get("mill_lift_z_clearance_mm", generation["lift_z_clearance_mm"])

    action_wait = float(generation.get("mill_actuation_wait_seconds", 2.0))
    lines = [
        f"def {function_name}():",
        "  # Generated mill pallet-transfer program",
        f"  # Operation: {operation}",
        "  # The dispatcher replaces this mass with the scheduled pallet's stored weight.",
        f"  {_PALLET_PAYLOAD_ASSIGNMENT_PREFIX}{UNLOADED_TOOL_PAYLOAD_KG:.6f}",
    ]
    if operation == "load":
        _append_payload(lines, "Set held pallet payload from pallet record", "mongo_pallet_payload_kg")
    else:
        _append_payload(lines, "Set unloaded fork payload to 5 oz", f"{UNLOADED_TOOL_PAYLOAD_KG:.6f}")
    _append_joint_move(
        lines, label=f"Move to {pre_waypoint.get('name', 'shared safe waypoint')}",
        waypoint=pre_waypoint, acceleration=1.2, speed=travel_speed,
    )
    _append_move(
        lines, label=f"Linear mill pre-entry: {pre_entry_pose.get('name', 'clearance')}", command="movel",
        position=pre_entry_pose, orientation=pre_entry_pose, acceleration=0.5, speed=precision_speed,
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
            z_offset_mm=mill_lift_z_clearance,
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
        _append_payload(lines, "Set held pallet payload from pallet record", "mongo_pallet_payload_kg")
        _append_move(
            lines, label="Lift mill pallet clear", command="movel", position=mill_pose,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=mill_lift_z_clearance,
        )
        _append_move(
            lines, label="Withdraw lifted pallet in positive X", command="movel", position=lifted_approach,
            orientation=orientation, acceleration=0.5, speed=precision_speed,
            z_offset_mm=mill_lift_z_clearance,
        )
    else:
        _append_payload(lines, "Set unloaded fork payload to 5 oz after setdown", f"{UNLOADED_TOOL_PAYLOAD_KG:.6f}")
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
    _append_move(
        lines, label=f"Linear return through mill pre-entry: {pre_entry_pose.get('name', 'clearance')}", command="movel",
        position=pre_entry_pose, orientation=pre_entry_pose, acceleration=0.5, speed=precision_speed,
    )
    _append_joint_move(
        lines, label=f"Return to {pre_waypoint.get('name', 'shared safe waypoint')}",
        waypoint=pre_waypoint, acceleration=1.2, speed=travel_speed,
    )
    lines.append("end")
    return "\n".join(lines) + "\n"


def _supervisor_function(script: str) -> str:
    """Keep only the function definition when embedding a generated atomic move."""
    lines = script.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("def ")), None)
    if start is None:
        raise ValueError("Generated atomic motion did not contain a function definition.")
    return "\n".join(
        line for line in lines[start:]
        if not line.strip().startswith(_PALLET_PAYLOAD_ASSIGNMENT_PREFIX)
    )


def build_robot_supervisor_script(
    *,
    backend_hostname: str,
    backend_port: int,
    heartbeat_seconds: float,
    telemetry_hz: float,
    reconnect_limit_seconds: float,
    pool_locations: list[dict],
    mill_pose: dict,
    mill_pre_entry_pose: dict,
    mill_entry_exit_pose: dict,
    generation: dict,
) -> str:
    """Build the persistent, robot-originated supervisor and all atomic movement functions."""
    if not backend_hostname.strip():
        raise ValueError("Supervisor backend hostname is required.")
    if backend_port < 1 or backend_port > 65535:
        raise ValueError("Supervisor backend port is invalid.")
    if not pool_locations:
        raise ValueError("At least one pool location is required for the supervisor.")

    functions: list[str] = []
    for pool in pool_locations:
        slot = int(pool["slot"])
        for operation in ("pick", "put"):
            functions.append(_supervisor_function(build_pallet_motion_script(
                function_name=f"mps_{operation}_pool_{slot:03d}",
                operation=operation,
                position=pool,
                generation=generation,
                invoke=False,
            )))
        functions.append(_supervisor_function(build_reliability_motion_script(
            function_name=f"mps_reliability_pool_{slot:03d}",
            position=pool,
            staging_pose=mill_pre_entry_pose,
            generation=generation,
            include_helpers=False,
        )))
    for operation in ("load", "unload"):
        functions.append(_supervisor_function(build_mill_pallet_motion_script(
            function_name=f"mps_{operation}_mill",
            operation=operation,
            mill_pose=mill_pose,
            pre_entry_pose=mill_pre_entry_pose,
            entry_exit_pose=mill_entry_exit_pose,
            generation=generation,
            invoke=False,
        )))

    telemetry_period = 1.0 / max(0.25, min(float(telemetry_hz), 10.0))
    heartbeat_timeout = max(3.0, float(heartbeat_seconds) * 4.0)
    reconnect_limit = max(1.0, min(float(reconnect_limit_seconds), 60.0))
    dispatcher: list[str] = []
    for pool in pool_locations:
        slot = int(pool["slot"])
        prefix = "if" if not dispatcher else "elif"
        dispatcher.extend([
            f"      {prefix} (mongo_command_opcode == 1) and (mongo_command_argument == {slot}):",
            f"        mps_pick_pool_{slot:03d}()",
            f"      elif (mongo_command_opcode == 2) and (mongo_command_argument == {slot}):",
            f"        mps_put_pool_{slot:03d}()",
            f"      elif (mongo_command_opcode == 5) and (mongo_command_argument == {slot}):",
            f"        mps_reliability_pool_{slot:03d}()",
        ])
    dispatcher.extend([
        "      elif mongo_command_opcode == 3:",
        "        mps_load_mill()",
        "      elif mongo_command_opcode == 4:",
        "        mps_unload_mill()",
        "      elif mongo_command_opcode == 10:",
        "        set_standard_digital_out(mongo_command_argument, mongo_command_value != 0)",
        "      elif mongo_command_opcode == 11:",
        "        set_configurable_digital_out(mongo_command_argument, mongo_command_value != 0)",
        "      elif mongo_command_opcode == 12:",
        "        set_tool_digital_out(mongo_command_argument, mongo_command_value != 0)",
        "      elif mongo_command_opcode == 20:",
        "        mongo_latched = False",
        "      elif mongo_command_opcode == 21:",
        "        mongo_maintenance_requested = True",
        "      else:",
        "        mongo_fault_code = 102",
        "        mongo_latched = True",
        "        mongo_publish_event(mongo_command_sequence, 5, mongo_fault_code)",
        "        continue",
    ])

    body = [
        "# Mongo Production System persistent robot supervisor",
        "# Protocol v1: checksummed signed 32-bit integers; robot initiates and owns this socket.",
        f"global mongo_pallet_payload_kg = {UNLOADED_TOOL_PAYLOAD_KG:.6f}",
        "global mongo_connected = False",
        "global mongo_link_lost = False",
        "global mongo_latched = False",
        "global mongo_motion_active = False",
        "global mongo_maintenance_requested = False",
        "global mongo_robot_session = floor(get_robot_time() * 1000) + 1",
        "global mongo_last_sequence = 0",
        "global mongo_last_event = 6",
        "global mongo_last_fault = 0",
        "global mongo_command_sequence = 0",
        "global mongo_command_opcode = 0",
        "global mongo_command_argument = 0",
        "global mongo_command_value = 0",
        "global mongo_command_payload_g = 0",
        "global mongo_pending_event_sequence = 0",
        "global mongo_pending_event_code = 0",
        "global mongo_pending_fault_code = 0",
        "global mongo_sent_event_sequence = 0",
        "global mongo_sent_event_code = 0",
        "global mongo_last_backend_heartbeat = get_robot_time()",
        "",
        *functions,
        "",
        "def mongo_checksum(a,b,c,d,e,f,g,h):",
        "  local total = a+b+c+d+e+f+g+h",
        "  return total - floor(total / 65521) * 65521",
        "end",
        "",
        "def mongo_send_values(a,b,c,d,e,f,g,h):",
        "  local checksum = mongo_checksum(a,b,c,d,e,f,g,h)",
        "  socket_send_int(a, \"mongo\")",
        "  socket_send_int(b, \"mongo\")",
        "  socket_send_int(c, \"mongo\")",
        "  socket_send_int(d, \"mongo\")",
        "  socket_send_int(e, \"mongo\")",
        "  socket_send_int(f, \"mongo\")",
        "  socket_send_int(g, \"mongo\")",
        "  socket_send_int(h, \"mongo\")",
        "  socket_send_int(checksum, \"mongo\")",
        "end",
        "",
        "def mongo_publish_event(sequence, event_code, fault_code):",
        "  mongo_pending_event_sequence = sequence",
        "  mongo_pending_event_code = event_code",
        "  mongo_pending_fault_code = fault_code",
        "  mongo_sent_event_code = 0",
        "  while ((mongo_sent_event_sequence != sequence) or (mongo_sent_event_code != event_code)) and (not mongo_maintenance_requested):",
        "    sync()",
        "  end",
        "end",
        "",
        "def mongo_send_field(value):",
        "  socket_send_int(value, \"mongo\")",
        "end",
        "",
        "def mongo_send_telemetry():",
        "  local q = get_actual_joint_positions()",
        "  local qd = get_actual_joint_speeds()",
        "  local tcp = get_actual_tcp_pose()",
        "  local tcps = get_actual_tcp_speed()",
        "  local din = 0",
        "  local dout = 0",
        "  local cin = 0",
        "  local cout = 0",
        "  local tin = read_port_register(21)",
        "  local tout = read_port_register(22)",
        "  local robot_mode = read_port_register(258)",
        "  local safety_mode = read_port_register(266)",
        "  local runtime_state = 2",
        "  if is_steady():",
        "    runtime_state = 1",
        "  end",
        "  local i = 0",
        "  local bit_value = 1",
        "  while i < 8:",
        "    if get_standard_digital_in(i):",
        "      din = din + bit_value",
        "    end",
        "    if get_standard_digital_out(i):",
        "      dout = dout + bit_value",
        "    end",
        "    if get_configurable_digital_in(i):",
        "      cin = cin + bit_value",
        "    end",
        "    if get_configurable_digital_out(i):",
        "      cout = cout + bit_value",
        "    end",
        "    bit_value = bit_value * 2",
        "    i = i + 1",
        "  end",
        "  # Frame header and I/O. Remaining vectors use milli-units.",
        "  socket_send_int(1, \"mongo\")",
        "  socket_send_int(22, \"mongo\")",
        "  mongo_send_field(mongo_robot_session)",
        "  mongo_send_field(mongo_last_sequence)",
        "  # Robot/safety/runtime values are controller-local and do not depend on RTDE.",
        "  mongo_send_field(robot_mode)",
        "  mongo_send_field(safety_mode)",
        "  mongo_send_field(runtime_state)",
        "  mongo_send_field(din)",
        "  mongo_send_field(dout)",
        "  mongo_send_field(cin)",
        "  mongo_send_field(cout)",
        "  mongo_send_field(tin)",
        "  mongo_send_field(tout)",
        "  local checksum = 1+22+mongo_robot_session+mongo_last_sequence+robot_mode+safety_mode+runtime_state+din+dout+cin+cout+tin+tout",
        "  i = 0",
        "  while i < 6:",
        "    local v = floor(q[i]*1000)",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  i = 0",
        "  while i < 6:",
        "    local v = floor(qd[i]*1000)",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  i = 0",
        "  while i < 6:",
        "    local v = read_port_register(290 + i)",
        "    if v > 32768:",
        "      v = v - 65535",
        "    end",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  i = 0",
        "  while i < 6:",
        "    local v = read_port_register(300 + i) * 10",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  i = 0",
        "  while i < 6:",
        "    local v = floor(tcp[i]*1000)",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  i = 0",
        "  while i < 6:",
        "    local v = floor(tcps[i]*1000)",
        "    mongo_send_field(v)",
        "    checksum = checksum + v",
        "    i = i + 1",
        "  end",
        "  checksum = checksum - floor(checksum / 65521) * 65521",
        "  mongo_send_field(checksum)",
        "end",
        "",
        "thread mongo_communication_thread():",
        "  local reconnect_delay = 1.0",
        "  local last_telemetry = 0.0",
        "  while not mongo_maintenance_requested:",
        "    if not mongo_connected:",
        f"      mongo_connected = socket_open(\"{backend_hostname.strip()}\", {int(backend_port)}, \"mongo\")",
        "      if mongo_connected:",
        "        reconnect_delay = 1.0",
        "        mongo_last_backend_heartbeat = get_robot_time()",
        "        local latch_value = 0",
        "        if mongo_latched:",
        "          latch_value = 1",
        "        end",
        "        mongo_send_values(1,20,mongo_robot_session,mongo_last_sequence,mongo_last_event,latch_value,1,0)",
        "      else:",
        "        sleep(reconnect_delay)",
        "        reconnect_delay = reconnect_delay * 2.0",
        f"        if reconnect_delay > {reconnect_limit:.3f}:",
        f"          reconnect_delay = {reconnect_limit:.3f}",
        "        end",
        "      end",
        "    else:",
        "      # PolyScope 3.2 has no read-timeout argument; backend heartbeats bound this read.",
        "      local incoming = socket_read_binary_integer(9, \"mongo\")",
        "      if incoming[0] == 9:",
        "        local expected = mongo_checksum(incoming[1],incoming[2],incoming[3],incoming[4],incoming[5],incoming[6],incoming[7],incoming[8])",
        "        if (incoming[1] == 1) and (incoming[9] == expected):",
        "          mongo_last_backend_heartbeat = get_robot_time()",
        "          if incoming[2] == 10:",
        "            if mongo_motion_active or (mongo_command_sequence > 0):",
        "              if incoming[4] == mongo_last_sequence:",
        "                # Duplicate active sequence: report current state without rerunning it.",
        "                mongo_pending_event_sequence = mongo_last_sequence",
        "                mongo_pending_event_code = mongo_last_event",
        "                mongo_pending_fault_code = mongo_last_fault",
        "                mongo_sent_event_code = 0",
        "              else:",
        "                # Never overwrite an in-flight atomic command.",
        "                mongo_latched = True",
        "                mongo_last_fault = 105",
        "              end",
        "            else:",
        "              mongo_command_sequence = incoming[4]",
        "              mongo_command_opcode = incoming[5]",
        "              mongo_command_argument = incoming[6]",
        "              mongo_command_value = incoming[7]",
        "              mongo_command_payload_g = incoming[8]",
        "            end",
        "          elif incoming[2] != 11:",
        "            mongo_latched = True",
        "            mongo_last_fault = 102",
        "          end",
        "        else:",
        "          mongo_latched = True",
        "          mongo_last_fault = 101",
        "        end",
        "      end",
        "      if (mongo_pending_event_sequence != mongo_sent_event_sequence) or (mongo_pending_event_code != mongo_sent_event_code):",
        "        mongo_send_values(1,21,mongo_robot_session,mongo_pending_event_sequence,mongo_pending_event_code,mongo_pending_fault_code,1,mongo_last_sequence)",
        "        mongo_sent_event_sequence = mongo_pending_event_sequence",
        "        mongo_sent_event_code = mongo_pending_event_code",
        "      end",
        f"      if get_robot_time() - last_telemetry >= {telemetry_period:.3f}:",
        "        mongo_send_telemetry()",
        "        last_telemetry = get_robot_time()",
        "      end",
        f"      if get_robot_time() - mongo_last_backend_heartbeat > {heartbeat_timeout:.3f}:",
        "        mongo_link_lost = True",
        "        mongo_connected = False",
        "        socket_close(\"mongo\")",
        "      end",
        "    end",
        "    sync()",
        "  end",
        "  if mongo_connected:",
        "    socket_close(\"mongo\")",
        "  end",
        "end",
        "",
        "  local communication = run mongo_communication_thread()",
        "  while not mongo_maintenance_requested:",
        "    if mongo_command_sequence > 0:",
        "      if mongo_command_sequence == mongo_last_sequence:",
        "        mongo_publish_event(mongo_last_sequence, mongo_last_event, mongo_last_fault)",
        "        mongo_command_sequence = 0",
        "        continue",
        "      end",
        "      if (mongo_command_opcode == 20) and (mongo_command_sequence == mongo_last_sequence + 1):",
        "        mongo_last_sequence = mongo_command_sequence",
        "        mongo_latched = False",
        "        mongo_link_lost = False",
        "        mongo_last_fault = 0",
        "        mongo_last_event = 3",
        "        mongo_publish_event(mongo_last_sequence, 3, 0)",
        "        mongo_command_sequence = 0",
        "        continue",
        "      end",
        "      if (mongo_command_opcode == 21) and (mongo_command_sequence == mongo_last_sequence + 1):",
        "        mongo_last_sequence = mongo_command_sequence",
        "        mongo_last_event = 3",
        "        mongo_publish_event(mongo_last_sequence, 3, 0)",
        "        mongo_command_sequence = 0",
        "        mongo_maintenance_requested = True",
        "        continue",
        "      end",
        "      if mongo_latched or mongo_command_sequence != mongo_last_sequence + 1:",
        "        mongo_latched = True",
        "        mongo_last_fault = 103",
        "        mongo_publish_event(mongo_command_sequence, 5, mongo_last_fault)",
        "        mongo_command_sequence = 0",
        "        continue",
        "      end",
        "      mongo_last_sequence = mongo_command_sequence",
        "      mongo_last_event = 1",
        "      mongo_last_fault = 0",
        "      mongo_publish_event(mongo_command_sequence, 1, 0)",
        "      mongo_motion_active = True",
        "      mongo_last_event = 2",
        "      mongo_publish_event(mongo_command_sequence, 2, 0)",
        "      mongo_link_lost = False",
        "      mongo_pallet_payload_kg = mongo_command_payload_g / 1000.0",
        "      local command_failed = False",
        *dispatcher,
        "      mongo_motion_active = False",
        "      if command_failed:",
        "        mongo_last_event = 4",
        "        mongo_latched = True",
        "        mongo_publish_event(mongo_last_sequence, 4, mongo_last_fault)",
        "      elif mongo_link_lost:",
        "        # The atomic move finished; latch until the backend reconnects and reconciles it.",
        "        mongo_last_event = 5",
        "        mongo_latched = True",
        "        mongo_last_fault = 104",
        "        mongo_publish_event(mongo_last_sequence, 5, mongo_last_fault)",
        "      elif mongo_latched:",
        "        # A conflicting command arrived while this atomic move was active.",
        "        mongo_last_event = 5",
        "        mongo_publish_event(mongo_last_sequence, 5, mongo_last_fault)",
        "      else:",
        "        mongo_last_event = 3",
        "        mongo_publish_event(mongo_last_sequence, 3, 0)",
        "      end",
        "      mongo_command_sequence = 0",
        "    end",
        "    sync()",
        "  end",
        "  kill communication",
    ]
    body_lines = "\n".join(body).splitlines()
    lines = [
        "def mongo_supervisor():",
        *(f"  {line}" if line else "" for line in body_lines),
        "end",
    ]
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
    if not content.startswith("def ") or content.rstrip().splitlines()[-1] != "end":
        raise RobotFileAccessError(
            "URControl requires a transmitted script to start with a root function and end with its matching 'end'."
        )
    try:
        payload = content.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RobotFileAccessError("URControl script transport only accepts ASCII text.") from exc
    try:
        with robot_command_lock(host):
            with socket.create_connection((host, 30002), timeout=timeout_seconds) as connection:
                connection.settimeout(min(max(timeout_seconds, 0.25), 0.5))
                connection.sendall(payload)
                # URControl documents that at least 79 response bytes must be read before
                # close; otherwise Windows may reset the socket and discard the script.
                received = 0
                deadline = time.monotonic() + max(0.5, min(timeout_seconds, 2.0))
                while received < 79 and time.monotonic() < deadline:
                    try:
                        chunk = connection.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    received += len(chunk)
                if received < 79:
                    raise RobotFileAccessError(
                        "URControl did not return enough response data to confirm an orderly script transfer."
                    )
    except OSError as exc:
        raise RobotFileAccessError(f"Could not send generated URScript to the robot: {exc}") from exc
