from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import socket
import struct
from threading import RLock
from typing import Any

try:
    import rtde.rtde as rtde_client
except ImportError:  # pragma: no cover - handled at runtime
    rtde_client = None


FULL_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "standard_analog_input0",
    "standard_analog_input1",
    "standard_analog_output0",
    "standard_analog_output1",
    "tool_analog_input0",
    "tool_analog_input1",
    "analog_io_types",
    "tool_analog_input_types",
    "robot_mode",
    "safety_mode",
    "runtime_state",
    "robot_status_bits",
    "safety_status_bits",
    "speed_scaling",
    "actual_execution_time",
    "actual_TCP_pose",
    "actual_TCP_speed",
    "actual_TCP_force",
    "actual_q",
    "actual_qd",
    "actual_current",
    "actual_joint_voltage",
    "actual_main_voltage",
    "actual_robot_voltage",
    "actual_robot_current",
    "actual_momentum",
    "actual_tool_accelerometer",
    "elbow_position",
    "elbow_velocity",
]

MID_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "standard_analog_input0",
    "standard_analog_input1",
    "standard_analog_output0",
    "standard_analog_output1",
    "tool_analog_input0",
    "tool_analog_input1",
    "analog_io_types",
    "tool_analog_input_types",
    "robot_mode",
    "safety_mode",
    "runtime_state",
    "speed_scaling",
    "actual_execution_time",
    "actual_TCP_pose",
    "actual_TCP_speed",
    "actual_q",
]

MOTION_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "robot_mode",
    "safety_mode",
    "runtime_state",
    "actual_TCP_pose",
    "actual_TCP_speed",
    "actual_TCP_force",
    "actual_q",
    "actual_qd",
    "actual_current",
]

POSE_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "robot_mode",
    "safety_mode",
    "actual_TCP_pose",
    "actual_TCP_speed",
]

JOINT_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "robot_mode",
    "safety_mode",
    "actual_q",
    "actual_qd",
    "actual_current",
]

CORE_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "standard_analog_input0",
    "standard_analog_input1",
    "standard_analog_output0",
    "standard_analog_output1",
    "tool_analog_input0",
    "tool_analog_input1",
    "analog_io_types",
    "tool_analog_input_types",
    "robot_mode",
    "safety_mode",
    "runtime_state",
]

LEGACY_OUTPUT_RECIPE = [
    "timestamp",
    "actual_digital_input_bits",
    "actual_digital_output_bits",
    "robot_mode",
    "safety_mode",
]


_LIVE_CONNECTION_LOCK = RLock()
_LIVE_CONNECTION: Any | None = None
_LIVE_CONNECTION_KEY: tuple[str, int, int] | None = None
_LIVE_CONTROLLER_VERSION: tuple[int | None, int | None, int | None, int | None] = (None, None, None, None)
_LIVE_RECIPE: list[str] = []


@dataclass
class RobotTelemetryError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _bit_rows(
    raw_value: int | None,
    prefix: str,
    count: int,
    offset: int = 0,
    *,
    writable: bool = False,
    direction: str | None = None,
    bank: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(count):
        bit = index + offset
        value = None if raw_value is None else bool((raw_value >> bit) & 1)
        rows.append(
            {
                "channel": f"{prefix}{index}",
                "index": index,
                "bit": bit,
                "value": value,
                "writable": writable,
                "direction": direction,
                "bank": bank,
            }
        )
    return rows


def _analog_row(
    channel: str,
    label: str,
    value: Any,
    mode_mask: int | None = None,
    mode_bit: int | None = None,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "label": label,
        "value": value,
        "mode_mask": mode_mask,
        "mode_bit": mode_bit,
    }


def _vector_rows(value: Any, prefix: str, labels: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) != len(labels):
        return []
    return [
        {
            "channel": f"{prefix}{index}",
            "label": label,
            "value": component,
        }
        for index, (label, component) in enumerate(zip(labels, value, strict=True))
    ]


def _flatten_named_vector(name: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for index, component in enumerate(value):
        rows.append(
            {
                "channel": f"{name}[{index}]",
                "label": name,
                "value": component,
            }
        )
    return rows


def _vector_component(value: Any, index: int) -> Any:
    if not isinstance(value, (list, tuple)) or index >= len(value):
        return None
    return value[index]


def _joint_detail_rows(sample: Any) -> list[dict[str, Any]]:
    joint_names = ["Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3"]
    fields = {
        "actual_position": _read_attr(sample, "actual_q"),
        "actual_velocity": _read_attr(sample, "actual_qd"),
        "actual_current": _read_attr(sample, "actual_current"),
        "target_position": _read_attr(sample, "target_q"),
        "target_velocity": _read_attr(sample, "target_qd"),
        "target_current": _read_attr(sample, "target_current"),
    }
    if not any(isinstance(value, (list, tuple)) for value in fields.values()):
        return []
    return [
        {
            "joint": name,
            **{key: _vector_component(value, index) for key, value in fields.items()},
        }
        for index, name in enumerate(joint_names)
    ]


def _tcp_detail_rows(sample: Any) -> list[dict[str, Any]]:
    labels = ["X", "Y", "Z", "Rx", "Ry", "Rz"]
    fields = {
        "actual_pose": _read_attr(sample, "actual_TCP_pose"),
        "actual_speed": _read_attr(sample, "actual_TCP_speed"),
        "actual_force": _read_attr(sample, "actual_TCP_force"),
        "target_pose": _read_attr(sample, "target_TCP_pose"),
        "target_speed": _read_attr(sample, "target_TCP_speed"),
    }
    if not any(isinstance(value, (list, tuple)) for value in fields.values()):
        return []
    return [
        {
            "axis": label,
            **{key: _vector_component(value, index) for key, value in fields.items()},
        }
        for index, label in enumerate(labels)
    ]


def _read_attr(sample: Any, name: str) -> Any:
    return getattr(sample, name, None)


def _read_modbus_registers(
    host: str,
    address: int,
    count: int,
    timeout_seconds: float,
) -> list[int]:
    """Read controller-owned Modbus holding registers without writing."""
    transaction_id = address + 1
    pdu = struct.pack(">BHH", 3, address, count)  # Read Holding Registers.
    request = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, 0) + pdu
    with socket.create_connection((host, 502), timeout=timeout_seconds) as connection:
        connection.settimeout(timeout_seconds)
        connection.sendall(request)
        response = b""
        expected_length = 9 + (count * 2)
        while len(response) < expected_length:
            chunk = connection.recv(expected_length - len(response))
            if not chunk:
                break
            response += chunk
    if (
        len(response) != expected_length
        or response[7] != 3
        or response[8] != count * 2
    ):
        raise RobotTelemetryError("The robot returned an invalid Modbus I/O response.")
    return list(struct.unpack(f">{count}H", response[9:]))


def _read_modbus_register(host: str, address: int, timeout_seconds: float) -> int:
    return _read_modbus_registers(host, address, 1, timeout_seconds)[0]


def _write_modbus_register(host: str, address: int, value: int, timeout_seconds: float) -> None:
    transaction_id = address + 101
    pdu = struct.pack(">BHH", 6, address, value)  # Write Single Register.
    request = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, 0) + pdu
    with socket.create_connection((host, 502), timeout=timeout_seconds) as connection:
        connection.settimeout(timeout_seconds)
        connection.sendall(request)
        response = connection.recv(260)
    if len(response) != 12 or response[7] != 6 or response[8:] != pdu[1:]:
        raise RobotTelemetryError("The robot rejected the Modbus output write.")


def toggle_robot_digital_output(
    host: str,
    port: int,
    timeout_seconds: float,
    bank: str,
    index: int,
) -> None:
    """Toggle one physical digital output through the controller Modbus server."""
    del port  # RTDE port is configured separately; the Modbus server is always TCP 502.
    if bank == "standard":
        register, bit = 1, index
    elif bank == "configurable":
        register, bit = 31, index
    elif bank == "tool":
        register, bit = 1, index + 8
    else:
        raise RobotTelemetryError(f"Unknown digital output bank: {bank}")

    if not 0 <= index <= (1 if bank == "tool" else 7):
        raise RobotTelemetryError(f"Invalid {bank} output index: {index}")

    current_value = _read_modbus_register(host, register, timeout_seconds)
    _write_modbus_register(host, register, current_value ^ (1 << bit), timeout_seconds)


def _read_legacy_controller_io(host: str, timeout_seconds: float) -> dict[int, int]:
    # UR CB-series Modbus registers 0/1 are standard input/output and 30/31
    # are configurable input/output. This avoids RTDE v1's unreliable masks.
    addresses = (0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 19, 30, 31)
    return {
        address: _read_modbus_register(host, address, timeout_seconds)
        for address in addresses
    }


def _install_protocol_fallback() -> None:
    if rtde_client is None:
        return

    if getattr(rtde_client.RTDE, "_mongo_rtde_compat_installed", False):
        return

    def negotiate_protocol_version(self: Any) -> bool:
        for version in (
            rtde_client.RTDE_PROTOCOL_VERSION_2,
            rtde_client.RTDE_PROTOCOL_VERSION_1,
        ):
            payload = struct.pack(">H", version)
            success = self._RTDE__sendAndReceive(  # noqa: SLF001 - UR client has no public hook.
                rtde_client.Command.RTDE_REQUEST_PROTOCOL_VERSION,
                payload,
            )
            if success:
                self._RTDE__protocolVersion = version  # noqa: SLF001
                return True
        return False

    rtde_client.RTDE.negotiate_protocol_version = negotiate_protocol_version

    original_output_setup = rtde_client.RTDE.send_output_setup
    original_unpack_output_setup = rtde_client.RTDE._RTDE__unpack_setup_outputs_package
    original_unpack_data = rtde_client.RTDE._RTDE__unpack_data_package

    def unpack_output_setup(self: Any, payload: bytes) -> Any:
        if self._RTDE__protocolVersion != rtde_client.RTDE_PROTOCOL_VERSION_1:  # noqa: SLF001
            return original_unpack_output_setup(self, payload)
        if not payload:
            return None
        # v1 replies with just the comma-separated types, while the client's
        # generic decoder expects an initial recipe-id byte (a v2 addition).
        return rtde_client.serialize.DataConfig.unpack_recipe(b"\x00" + payload)

    rtde_client.RTDE._RTDE__unpack_setup_outputs_package = unpack_output_setup

    def unpack_data(self: Any, payload: bytes, output_config: Any) -> Any:
        if self._RTDE__protocolVersion != rtde_client.RTDE_PROTOCOL_VERSION_1:  # noqa: SLF001
            return original_unpack_data(self, payload, output_config)
        if output_config is None:
            return None
        # v1 data packets have no recipe-id byte. The client helper expects a
        # decoded value sequence, not raw bytes, so unpack the configured
        # scalar/vector types before building the sample.
        values = struct.unpack_from(
            ">" + output_config.fmt[2:],  # Drop the v2-only recipe-id byte.
            payload,
        )
        sample = rtde_client.serialize.DataObject()
        sample.recipe_id = 0
        offset = 0
        for name, field_type in zip(output_config.names, output_config.types, strict=True):
            sample.__dict__[name] = rtde_client.serialize.unpack_field(values, offset, field_type)
            offset += rtde_client.serialize.get_item_size(field_type)
        return sample

    rtde_client.RTDE._RTDE__unpack_data_package = unpack_data

    def send_output_setup(
        self: Any,
        variables: list[str],
        types: list[str] | None = None,
        frequency: int = 125,
    ) -> bool:
        if self._RTDE__protocolVersion != rtde_client.RTDE_PROTOCOL_VERSION_1:  # noqa: SLF001
            return original_output_setup(self, variables, types or [], frequency)

        # Protocol v1 (used by the installed PolyScope 3.2 controller) accepts
        # only comma-separated field names. Frequency was added in v2.
        result = self._RTDE__sendAndReceive(  # noqa: SLF001
            rtde_client.Command.RTDE_CONTROL_PACKAGE_SETUP_OUTPUTS,
            ",".join(variables).encode("utf-8"),
        )
        if result is None:
            return False
        requested_types = types or []
        if requested_types and result.types != requested_types:
            return False
        result.names = variables
        self._RTDE__output_config = result  # noqa: SLF001
        return True

    rtde_client.RTDE.send_output_setup = send_output_setup
    rtde_client.RTDE._mongo_rtde_compat_installed = True


def _disconnect_live_connection() -> None:
    global _LIVE_CONNECTION, _LIVE_CONNECTION_KEY, _LIVE_RECIPE

    if _LIVE_CONNECTION is not None:
        try:
            _LIVE_CONNECTION.send_pause()
        except Exception:
            pass
        try:
            _LIVE_CONNECTION.disconnect()
        except Exception:
            pass
    _LIVE_CONNECTION = None
    _LIVE_CONNECTION_KEY = None
    _LIVE_RECIPE = []


def _open_live_connection(
    host: str,
    port: int,
    poll_hz: int,
    timeout_seconds: float,
) -> tuple[Any, tuple[int | None, int | None, int | None, int | None], list[str]]:
    if rtde_client is None:
        raise RobotTelemetryError("The RTDE Python client library is not installed.")

    _install_protocol_fallback()
    original_timeout = rtde_client.DEFAULT_TIMEOUT
    rtde_client.DEFAULT_TIMEOUT = timeout_seconds
    try:
        connection = rtde_client.RTDE(host, port)
        connection.connect()
        try:
            recipe_used: list[str] | None = None
            # Older controllers use RTDE protocol v1, but still support many
            # motion fields. Try richer compatible recipes before accepting
            # the minimal legacy I/O-only recipe.
            recipes = (
                FULL_OUTPUT_RECIPE,
                MID_OUTPUT_RECIPE,
                MOTION_OUTPUT_RECIPE,
                POSE_OUTPUT_RECIPE,
                JOINT_OUTPUT_RECIPE,
                CORE_OUTPUT_RECIPE,
                LEGACY_OUTPUT_RECIPE,
            )
            for recipe in recipes:
                try:
                    if connection.send_output_setup(recipe, frequency=poll_hz):
                        recipe_used = recipe
                        break
                except (ValueError, TypeError):
                    # The controller replies NOT_FOUND for unsupported fields.
                    # Keep trying smaller recipes instead of failing the page.
                    continue
            if recipe_used is None:
                raise RobotTelemetryError(
                    "The robot rejected the RTDE output recipe. Check controller RTDE support."
                )
            if not connection.send_start():
                raise RobotTelemetryError("The robot refused to start RTDE data synchronization.")
            return connection, (None, None, None, None), recipe_used
        except Exception:
            connection.disconnect()
            raise
    except Exception as exc:  # pragma: no cover - exercised via endpoint behavior
        if isinstance(exc, RobotTelemetryError):
            raise
        raise RobotTelemetryError(str(exc)) from exc
    finally:
        rtde_client.DEFAULT_TIMEOUT = original_timeout


def _connect_and_sample(host: str, port: int, poll_hz: int, timeout_seconds: float) -> tuple[Any, tuple[int | None, int | None, int | None, int | None], list[str]]:
    global _LIVE_CONNECTION, _LIVE_CONNECTION_KEY, _LIVE_CONTROLLER_VERSION, _LIVE_RECIPE

    if rtde_client is None:
        raise RobotTelemetryError("The RTDE Python client library is not installed.")

    key = (host, port, poll_hz)
    with _LIVE_CONNECTION_LOCK:
        if _LIVE_CONNECTION is None or _LIVE_CONNECTION_KEY != key:
            _disconnect_live_connection()
            (
                _LIVE_CONNECTION,
                _LIVE_CONTROLLER_VERSION,
                _LIVE_RECIPE,
            ) = _open_live_connection(host, port, poll_hz, timeout_seconds)
            _LIVE_CONNECTION_KEY = key

        original_timeout = rtde_client.DEFAULT_TIMEOUT
        rtde_client.DEFAULT_TIMEOUT = timeout_seconds
        try:
            sample = _LIVE_CONNECTION.receive()
            # Protocol v1 streams at the controller's fixed rate. The UI polls
            # far more slowly, so discard buffered history and report the most
            # recent packet instead of replaying stale I/O transitions.
            while True:
                buffered = _LIVE_CONNECTION._RTDE__recv_from_buffer(  # noqa: SLF001
                    rtde_client.Command.RTDE_DATA_PACKAGE,
                )
                if buffered is not None:
                    sample = buffered
                    continue
                if not _LIVE_CONNECTION._RTDE__recv_to_buffer(0):  # noqa: SLF001
                    break
            if sample is None:
                raise RobotTelemetryError("No RTDE sample was received before timeout.")
            return sample, _LIVE_CONTROLLER_VERSION, _LIVE_RECIPE
        except Exception as exc:
            _disconnect_live_connection()
            if isinstance(exc, RobotTelemetryError):
                raise
            raise RobotTelemetryError(str(exc)) from exc
        finally:
            rtde_client.DEFAULT_TIMEOUT = original_timeout


def read_robot_snapshot(host: str, port: int, poll_hz: int, timeout_seconds: float) -> dict[str, Any]:
    sample, controller_version, recipe_used = _connect_and_sample(
        host,
        port,
        poll_hz,
        timeout_seconds,
    )
    digital_inputs = _read_attr(sample, "actual_digital_input_bits")
    digital_outputs = _read_attr(sample, "actual_digital_output_bits")
    configurable_inputs: int | None = None
    configurable_outputs: int | None = None
    legacy_registers: dict[int, int] | None = None
    legacy_standard_io = recipe_used == LEGACY_OUTPUT_RECIPE
    if legacy_standard_io:
        try:
            legacy_registers = _read_legacy_controller_io(host, timeout_seconds)
            digital_inputs = legacy_registers[0]
            digital_outputs = legacy_registers[1]
            configurable_inputs = legacy_registers[30]
            configurable_outputs = legacy_registers[31]
        except RobotTelemetryError:
            legacy_standard_io = False
    analog_modes = _read_attr(sample, "analog_io_types")
    tool_analog_modes = _read_attr(sample, "tool_analog_input_types")
    analog_inputs = [
        _analog_row("AI0", "Standard analog input 0", _read_attr(sample, "standard_analog_input0"), analog_modes, 0),
        _analog_row("AI1", "Standard analog input 1", _read_attr(sample, "standard_analog_input1"), analog_modes, 1),
        _analog_row("TAI0", "Tool analog input 0", _read_attr(sample, "tool_analog_input0"), tool_analog_modes, 0),
        _analog_row("TAI1", "Tool analog input 1", _read_attr(sample, "tool_analog_input1"), tool_analog_modes, 1),
    ]
    analog_outputs = [
        _analog_row("AO0", "Standard analog output 0", _read_attr(sample, "standard_analog_output0"), analog_modes, 2),
        _analog_row("AO1", "Standard analog output 1", _read_attr(sample, "standard_analog_output1"), analog_modes, 3),
    ]
    if legacy_registers is not None:
        domain = lambda value: "voltage" if value else "current"
        analog_inputs = [
            _analog_row("AI0", f"Standard analog input 0 ({domain(legacy_registers[5])}, raw)", legacy_registers[4], legacy_registers[5]),
            _analog_row("AI1", f"Standard analog input 1 ({domain(legacy_registers[7])}, raw)", legacy_registers[6], legacy_registers[7]),
            _analog_row("TAI0", f"Tool analog input 0 ({domain(legacy_registers[9])}, raw)", legacy_registers[8], legacy_registers[9]),
            _analog_row("TAI1", f"Tool analog input 1 ({domain(legacy_registers[11])}, raw)", legacy_registers[10], legacy_registers[11]),
        ]
        analog_outputs = [
            _analog_row("AO0", f"Standard analog output 0 ({domain(legacy_registers[17])}, raw)", legacy_registers[16], legacy_registers[17]),
            _analog_row("AO1", f"Standard analog output 1 ({domain(legacy_registers[19])}, raw)", legacy_registers[18], legacy_registers[19]),
        ]
    sample_values = dict(getattr(sample, "__dict__", {}))

    handled_vector_fields = {
        "actual_TCP_pose",
        "actual_TCP_speed",
        "actual_TCP_force",
        "actual_q",
        "actual_qd",
        "actual_current",
    }
    extra_actual_rows: list[dict[str, Any]] = []
    for name in sorted(sample_values):
        if not name.startswith("actual_") or name in handled_vector_fields:
            continue
        value = sample_values[name]
        if isinstance(value, (list, tuple)):
            extra_actual_rows.extend(_flatten_named_vector(name, value))
        else:
            extra_actual_rows.append(
                {
                    "channel": name,
                    "label": name,
                    "value": value,
                }
            )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "robot",
        "connected": True,
        "connection_label": "Live RTDE",
        "robot": {
            "host": host,
            "port": port,
            "controller_version": ".".join(
                str(part) for part in controller_version if part is not None
            ),
            "recipe_fields": recipe_used,
        },
        "digital_input_groups": [
            {
                "title": "Standard inputs",
                "rows": _bit_rows(digital_inputs, "DI", 8, 0, direction="input", bank="standard"),
            },
            {
                "title": "Configurable inputs",
                "rows": _bit_rows(
                    configurable_inputs if legacy_standard_io else digital_inputs,
                    "CI",
                    8,
                    0 if legacy_standard_io else 8,
                    direction="input",
                    bank="configurable",
                ),
            },
            {
                "title": "Tool inputs",
                "rows": _bit_rows(
                    digital_inputs,
                    "TI",
                    2,
                    8 if legacy_standard_io else 16,
                    direction="input",
                    bank="tool",
                ),
            },
        ],
        "digital_output_groups": [
            {
                "title": "Standard outputs",
                "rows": _bit_rows(digital_outputs, "DO", 8, 0, direction="output", bank="standard"),
            },
            {
                "title": "Configurable outputs",
                "rows": _bit_rows(
                    configurable_outputs if legacy_standard_io else digital_outputs,
                    "CO",
                    8,
                    0 if legacy_standard_io else 8,
                    direction="output",
                    bank="configurable",
                ),
            },
            {
                "title": "Tool outputs",
                "rows": _bit_rows(
                    digital_outputs,
                    "TO",
                    2,
                    8 if legacy_standard_io else 16,
                    direction="output",
                    bank="tool",
                ),
            },
        ],
        "analog_inputs": analog_inputs,
        "analog_outputs": analog_outputs,
        "state_rows": [
            {"label": "Robot mode", "value": _read_attr(sample, "robot_mode")},
            {"label": "Safety mode", "value": _read_attr(sample, "safety_mode")},
            {"label": "Runtime state", "value": _read_attr(sample, "runtime_state")},
            {"label": "Speed scaling", "value": _read_attr(sample, "speed_scaling")},
            {"label": "Execution time (s)", "value": _read_attr(sample, "actual_execution_time")},
            {"label": "Robot voltage (V)", "value": _read_attr(sample, "actual_robot_voltage")},
            {"label": "Robot current (A)", "value": _read_attr(sample, "actual_robot_current")},
            {"label": "Main voltage (V)", "value": _read_attr(sample, "actual_main_voltage")},
            {"label": "Robot status bits", "value": _read_attr(sample, "robot_status_bits")},
            {"label": "Safety status bits", "value": _read_attr(sample, "safety_status_bits")},
        ],
        "pose_rows": _vector_rows(
            _read_attr(sample, "actual_TCP_pose"),
            "TCP",
            ["X", "Y", "Z", "Rx", "Ry", "Rz"],
        ),
        "tcp_speed_rows": _vector_rows(
            _read_attr(sample, "actual_TCP_speed"),
            "SPD",
            ["Vx", "Vy", "Vz", "Wx", "Wy", "Wz"],
        ),
        "joint_rows": _vector_rows(
            _read_attr(sample, "actual_q"),
            "J",
            ["Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3"],
        ),
        "tcp_detail_rows": _tcp_detail_rows(sample),
        "joint_detail_rows": _joint_detail_rows(sample),
        "extra_actual_rows": extra_actual_rows,
        "notes": (
            "Reading standard digital I/O from the controller Modbus server and live telemetry over RTDE. "
            "Configurable I/O is read from the controller's dedicated Modbus registers."
            if legacy_standard_io
            else "Reading live robot telemetry over RTDE port 30004."
        ),
    }
