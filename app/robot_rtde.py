from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import socket
import struct
from threading import Event, RLock, Thread
from time import monotonic, sleep
from typing import Any
from types import SimpleNamespace

from app.diagnostics import diagnostics

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
_REALTIME_CONNECTION: socket.socket | None = None
_REALTIME_CONNECTION_HOST: str | None = None
_REALTIME_BUFFER = bytearray()
_REALTIME_READER: Thread | None = None
_REALTIME_READER_GENERATION = 0
_REALTIME_READER_STARTED_AT = 0.0
_REALTIME_LATEST_PACKET: bytes | None = None
_REALTIME_LATEST_AT = 0.0
_REALTIME_READER_ERROR: str | None = None
_REALTIME_READER_ERROR_REPORTED = False
_REALTIME_SAMPLE_EVENT = Event()
_MODBUS_CONNECTION: socket.socket | None = None
_MODBUS_CONNECTION_HOST: str | None = None
_SNAPSHOT_LOCK = RLock()
_LEGACY_REALTIME_KEYS: set[tuple[str, int]] = set()
_MODBUS_RETRY_AFTER: dict[str, float] = {}
_MODBUS_IO_CACHE: dict[str, tuple[float, dict[int, int]]] = {}
_MODBUS_IO_LOCK = RLock()
_TELEMETRY_RETRY_AFTER: dict[tuple[str, int], float] = {}
_TELEMETRY_FAILURE_COUNT: dict[tuple[str, int], int] = {}
_TELEMETRY_LAST_ERROR: dict[tuple[str, int], str] = {}
# Reconnect gently after a packet gap. These values limit retry pressure while
# recovering fast enough that an in-progress robot motion is not misclassified.
_TELEMETRY_RETRY_BASE_SECONDS = 2
_TELEMETRY_RETRY_MAX_SECONDS = 12
# The configured timeout remains the maximum age accepted for a live motion
# sample. Establishing a new CB-series stream is slower on a lossy network and
# needs a separate window so a healthy controller is not rejected at startup.
_REALTIME_CONNECT_TIMEOUT_SECONDS = 8.0
_REALTIME_INITIAL_SAMPLE_TIMEOUT_SECONDS = 8.0
_REALTIME_READER_STALL_SECONDS = 20.0
_PRIMARY_INTERFACE_PORT = 30001
_RECORDED_REALTIME_PACKET_SIZES = {812, 1044, 1060, 1108, 1116, 1140, 1220}
_CONNECTIONS_SUSPENDED = False


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
    with _MODBUS_IO_LOCK:
        connection = _persistent_modbus_connection(host, timeout_seconds)
        try:
            return _read_modbus_registers_on_connection(connection, address, count)
        except (OSError, RobotTelemetryError):
            _disconnect_modbus_connection()
            raise


def _read_modbus_registers_on_connection(
    connection: socket.socket,
    address: int,
    count: int,
) -> list[int]:
    """Read registers over an existing Modbus TCP session."""
    transaction_id = address + 1
    pdu = struct.pack(">BHH", 3, address, count)  # Read Holding Registers.
    request = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, 0) + pdu
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
    with _MODBUS_IO_LOCK:
        connection = _persistent_modbus_connection(host, timeout_seconds)
        try:
            _write_modbus_register_on_connection(connection, address, value)
        except (OSError, RobotTelemetryError):
            _disconnect_modbus_connection()
            raise


def _write_modbus_register_on_connection(connection: socket.socket, address: int, value: int) -> None:
    transaction_id = address + 101
    pdu = struct.pack(">BHH", 6, address, value)  # Write Single Register.
    request = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, 0) + pdu
    connection.sendall(request)
    response = b""
    while len(response) < 12:
        chunk = connection.recv(12 - len(response))
        if not chunk:
            break
        response += chunk
    if len(response) != 12 or response[7] != 6 or response[8:] != pdu[1:]:
        raise RobotTelemetryError("The robot rejected the Modbus output write.")


def _disconnect_modbus_connection() -> None:
    global _MODBUS_CONNECTION, _MODBUS_CONNECTION_HOST
    if _MODBUS_CONNECTION is not None:
        try:
            _MODBUS_CONNECTION.close()
        except OSError:
            pass
    _MODBUS_CONNECTION = None
    _MODBUS_CONNECTION_HOST = None


def _persistent_modbus_connection(host: str, timeout_seconds: float) -> socket.socket:
    global _MODBUS_CONNECTION, _MODBUS_CONNECTION_HOST
    if _CONNECTIONS_SUSPENDED:
        raise RobotTelemetryError("Robot communications are suspended while the backend relaunches.")
    if _MODBUS_CONNECTION is None or _MODBUS_CONNECTION_HOST != host:
        _disconnect_modbus_connection()
        try:
            _MODBUS_CONNECTION = socket.create_connection((host, 502), timeout=timeout_seconds)
            _MODBUS_CONNECTION_HOST = host
        except OSError as exc:
            _disconnect_modbus_connection()
            raise RobotTelemetryError(f"Robot Modbus connection failed: {exc}") from exc
    _MODBUS_CONNECTION.settimeout(timeout_seconds)
    return _MODBUS_CONNECTION


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

    with _MODBUS_IO_LOCK:
        # Manual output changes are exceptional. Use one short-lived session so
        # an old controller never retains an idle Modbus client or receives
        # automatic register polling alongside its realtime stream.
        try:
            connection = socket.create_connection((host, 502), timeout=timeout_seconds)
            connection.settimeout(timeout_seconds)
            current_value = _read_modbus_registers_on_connection(connection, register, 1)[0]
            new_value = current_value ^ (1 << bit)
            _write_modbus_register_on_connection(connection, register, new_value)
        except (OSError, RobotTelemetryError) as exc:
            raise RobotTelemetryError(f"Robot Modbus output change failed: {exc}") from exc
        finally:
            if "connection" in locals():
                try:
                    connection.close()
                except OSError:
                    pass
        cached = _MODBUS_IO_CACHE.get(host)
        if cached:
            values = dict(cached[1])
            values[register] = new_value
            _MODBUS_IO_CACHE[host] = (monotonic(), values)
        _MODBUS_RETRY_AFTER.pop(host, None)


def _read_legacy_controller_io(host: str, timeout_seconds: float) -> dict[int, int]:
    # UR CB-series Modbus registers 0/1 are standard input/output and 30/31
    # are configurable input/output. This avoids RTDE v1's unreliable masks.
    groups = ((0, 2), (4, 8), (16, 4), (30, 2))
    result: dict[int, int] = {}
    with _MODBUS_IO_LOCK:
        try:
            connection = _persistent_modbus_connection(host, timeout_seconds)
            for address, count in groups:
                values = _read_modbus_registers_on_connection(connection, address, count)
                result.update({address + offset: value for offset, value in enumerate(values)})
        except (OSError, RobotTelemetryError) as exc:
            _disconnect_modbus_connection()
            raise RobotTelemetryError(f"Robot Modbus I/O read failed: {exc}") from exc
    return result


def _cached_legacy_controller_io(host: str, timeout_seconds: float) -> dict[int, int]:
    with _MODBUS_IO_LOCK:
        cached = _MODBUS_IO_CACHE.get(host)
        now = monotonic()
        if cached and now - cached[0] < 1.5:
            return cached[1]
        if now < _MODBUS_RETRY_AFTER.get(host, 0):
            raise RobotTelemetryError("Robot Modbus I/O is in connection backoff.")
        try:
            values = _read_legacy_controller_io(host, min(timeout_seconds, 0.5))
        except RobotTelemetryError:
            _MODBUS_RETRY_AFTER[host] = now + 30
            raise
        _MODBUS_RETRY_AFTER.pop(host, None)
        _MODBUS_IO_CACHE[host] = (now, values)
        return values


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


def _realtime_value(packet: bytes, column: int) -> float | None:
    """Read one 1-based DOUBLE column from the legacy UR realtime packet."""
    offset = 4 + ((column - 1) * 8)
    if offset + 8 > len(packet):
        return None
    return struct.unpack_from(">d", packet, offset)[0]


def _realtime_vector(packet: bytes, first_column: int, count: int = 6) -> list[float] | None:
    values = [_realtime_value(packet, first_column + index) for index in range(count)]
    return values if all(value is not None for value in values) else None


def _primary_subpackages(packet: bytes) -> dict[int, bytes]:
    """Decode one Primary-interface RobotState message by subpackage type."""
    if len(packet) < 5 or packet[4] != 16:
        raise RobotTelemetryError("The robot returned a non-state Primary-interface packet.")
    packages: dict[int, bytes] = {}
    offset = 5
    while offset + 5 <= len(packet):
        package_size = struct.unpack_from(">I", packet, offset)[0]
        if package_size < 5 or offset + package_size > len(packet):
            raise RobotTelemetryError("The robot returned a malformed Primary-interface state package.")
        packages[packet[offset + 4]] = packet[offset + 5:offset + package_size]
        offset += package_size
    if offset != len(packet):
        raise RobotTelemetryError("The robot returned a truncated Primary-interface state package.")
    return packages


def _parse_primary_state(packet: bytes) -> Any:
    """Map the CB-series Primary stream to the existing telemetry sample contract."""
    packages = _primary_subpackages(packet)
    robot_mode_data = packages.get(0)
    joint_data = packages.get(1)
    masterboard_data = packages.get(3)
    cartesian_data = packages.get(4)
    tool_data = packages.get(2)
    if not robot_mode_data or len(robot_mode_data) < 41:
        raise RobotTelemetryError("Primary telemetry omitted robot mode data.")
    if not joint_data or len(joint_data) < 41 * 6:
        raise RobotTelemetryError("Primary telemetry omitted joint data.")
    if not masterboard_data or len(masterboard_data) < 62:
        raise RobotTelemetryError("Primary telemetry omitted masterboard I/O data.")
    if not cartesian_data or len(cartesian_data) < 48:
        raise RobotTelemetryError("Primary telemetry omitted the TCP pose.")

    (
        timestamp,
        _robot_connected,
        _real_robot_enabled,
        _robot_power_on,
        _emergency_stopped,
        _protective_stopped,
        program_running,
        program_paused,
        robot_mode,
        _control_mode,
        _target_speed_fraction,
        speed_scaling,
        _target_speed_limit,
    ) = struct.unpack_from(">Q???????BBddd", robot_mode_data)

    actual_q: list[float] = []
    actual_qd: list[float] = []
    actual_current: list[float] = []
    joint_temperatures: list[float] = []
    for index in range(6):
        offset = index * 41
        actual_q.append(struct.unpack_from(">d", joint_data, offset)[0])
        actual_qd.append(struct.unpack_from(">d", joint_data, offset + 16)[0])
        actual_current.append(struct.unpack_from(">f", joint_data, offset + 24)[0])
        joint_temperatures.append(struct.unpack_from(">f", joint_data, offset + 32)[0])

    digital_inputs, digital_outputs = struct.unpack_from(">II", masterboard_data)
    analog_input_range0 = masterboard_data[8]
    analog_input_range1 = masterboard_data[9]
    standard_analog_input0 = struct.unpack_from(">d", masterboard_data, 10)[0]
    standard_analog_input1 = struct.unpack_from(">d", masterboard_data, 18)[0]
    analog_output_domain0 = masterboard_data[26]
    analog_output_domain1 = masterboard_data[27]
    standard_analog_output0 = struct.unpack_from(">d", masterboard_data, 28)[0]
    standard_analog_output1 = struct.unpack_from(">d", masterboard_data, 36)[0]
    analog_io_types = (
        analog_input_range0
        | (analog_input_range1 << 1)
        | (analog_output_domain0 << 2)
        | (analog_output_domain1 << 3)
    )
    tool_analog_input0 = None
    tool_analog_input1 = None
    tool_analog_input_types = None
    if tool_data and len(tool_data) >= 18:
        tool_analog_input_types = tool_data[0] | (tool_data[1] << 1)
        tool_analog_input0 = struct.unpack_from(">d", tool_data, 2)[0]
        tool_analog_input1 = struct.unpack_from(">d", tool_data, 10)[0]
    # Safety mode follows the four masterboard float values in all CB3 state
    # package revisions. Ignore optional Euromap fields after this byte.
    safety_mode = masterboard_data[60]
    actual_tcp_pose = list(struct.unpack_from(">6d", cartesian_data))
    if program_paused:
        runtime_state = "paused"
        program_state = 4.0
    elif program_running:
        runtime_state = "playing"
        program_state = 2.0
    else:
        runtime_state = "stopped"
        program_state = 1.0

    return SimpleNamespace(
        timestamp=float(timestamp) / 1_000_000.0,
        actual_q=actual_q,
        actual_qd=actual_qd,
        actual_current=actual_current,
        joint_temperatures=joint_temperatures,
        actual_TCP_pose=actual_tcp_pose,
        # Primary RobotState does not expose TCP speed on this PolyScope build.
        actual_TCP_speed=None,
        actual_digital_input_bits=digital_inputs,
        actual_digital_output_bits=digital_outputs,
        standard_analog_input0=standard_analog_input0,
        standard_analog_input1=standard_analog_input1,
        standard_analog_output0=standard_analog_output0,
        standard_analog_output1=standard_analog_output1,
        tool_analog_input0=tool_analog_input0,
        tool_analog_input1=tool_analog_input1,
        analog_io_types=analog_io_types,
        tool_analog_input_types=tool_analog_input_types,
        robot_mode=float(robot_mode),
        safety_mode=float(safety_mode),
        speed_scaling=speed_scaling,
        runtime_state=runtime_state,
        program_state=program_state,
    )


def _disconnect_legacy_realtime() -> None:
    global _REALTIME_CONNECTION, _REALTIME_CONNECTION_HOST, _REALTIME_READER
    global _REALTIME_READER_GENERATION, _REALTIME_READER_STARTED_AT
    global _REALTIME_LATEST_PACKET, _REALTIME_LATEST_AT
    global _REALTIME_READER_ERROR, _REALTIME_READER_ERROR_REPORTED

    _REALTIME_READER_GENERATION += 1
    connection = _REALTIME_CONNECTION
    # Clear ownership before interrupting recv(). The superseded reader can
    # then finish without clearing or reporting an error against its successor.
    _REALTIME_CONNECTION = None
    _REALTIME_CONNECTION_HOST = None
    _REALTIME_READER = None
    _REALTIME_READER_STARTED_AT = 0.0
    if connection is not None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        try:
            connection.close()
        except OSError:
            pass
    _REALTIME_LATEST_PACKET = None
    _REALTIME_LATEST_AT = 0.0
    _REALTIME_READER_ERROR = None
    _REALTIME_READER_ERROR_REPORTED = False
    _REALTIME_SAMPLE_EVENT.clear()
    _REALTIME_BUFFER.clear()


def robot_telemetry_transport_status() -> dict[str, Any]:
    """Expose enough transport state to diagnose failures without opening another robot socket."""
    with _LIVE_CONNECTION_LOCK:
        age = monotonic() - _REALTIME_LATEST_AT if _REALTIME_LATEST_AT else None
        return {
            "transport": "primary_interface",
            "host": _REALTIME_CONNECTION_HOST,
            "port": _PRIMARY_INTERFACE_PORT,
            "reader_alive": bool(_REALTIME_READER and _REALTIME_READER.is_alive()),
            "sample_age_seconds": round(age, 3) if age is not None else None,
            "generation": _REALTIME_READER_GENERATION,
            "last_error": _REALTIME_READER_ERROR,
            "connections_suspended": _CONNECTIONS_SUSPENDED,
        }


def _configure_realtime_socket(connection: socket.socket) -> None:
    """Keep one long-lived telemetry socket healthy across transient network loss."""
    try:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_NODELAY"):
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (AttributeError, OSError):
        # Test doubles and a few embedded socket implementations do not expose
        # every socket option. Telemetry can still operate without these hints.
        pass
    if hasattr(socket, "SIO_KEEPALIVE_VALS"):
        try:
            connection.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10_000, 3_000))
        except (AttributeError, OSError):
            pass


def _legacy_realtime_reader(connection: socket.socket, generation: int, timeout_seconds: float) -> None:
    """Continuously drain the CB Primary stream so URControl never accumulates output."""
    global _REALTIME_CONNECTION, _REALTIME_CONNECTION_HOST, _REALTIME_READER
    global _REALTIME_LATEST_PACKET, _REALTIME_LATEST_AT
    global _REALTIME_READER_ERROR, _REALTIME_READER_ERROR_REPORTED

    reader_host = _REALTIME_CONNECTION_HOST
    buffer = bytearray()
    error: str | None = None
    try:
        # Keep the single persistent CB-series stream through brief network stalls.
        # Callers still enforce their own short freshness timeout for motion safety.
        connection.settimeout(max(_REALTIME_READER_STALL_SECONDS, timeout_seconds * 5.0))
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                raise RobotTelemetryError("The robot closed its realtime telemetry connection.")
            buffer.extend(chunk)
            latest: bytes | None = None
            while len(buffer) >= 4:
                packet_size = struct.unpack_from(">I", buffer)[0]
                if not 5 <= packet_size <= 1_000_000:
                    raise RobotTelemetryError("The robot returned an invalid Primary-interface packet.")
                if len(buffer) < packet_size:
                    break
                packet = bytes(buffer[:packet_size])
                del buffer[:packet_size]
                if (
                    len(packet) >= 5
                    and (packet[4] == 16 or packet_size in _RECORDED_REALTIME_PACKET_SIZES)
                ):
                    latest = packet
            if latest is None:
                continue
            with _LIVE_CONNECTION_LOCK:
                if generation != _REALTIME_READER_GENERATION or connection is not _REALTIME_CONNECTION:
                    return
                _REALTIME_LATEST_PACKET = latest
                _REALTIME_LATEST_AT = monotonic()
                _REALTIME_READER_ERROR = None
                _REALTIME_READER_ERROR_REPORTED = False
                _REALTIME_SAMPLE_EVENT.set()
    except (OSError, RobotTelemetryError) as exc:
        error = str(exc)
    finally:
        try:
            connection.close()
        except OSError:
            pass
        with _LIVE_CONNECTION_LOCK:
            owns_connection = (
                generation == _REALTIME_READER_GENERATION
                and connection is _REALTIME_CONNECTION
            )
            if owns_connection:
                _REALTIME_CONNECTION = None
                _REALTIME_CONNECTION_HOST = None
                _REALTIME_READER = None
                _REALTIME_READER_ERROR = error or "The realtime telemetry reader stopped."
                _REALTIME_READER_ERROR_REPORTED = False
                _REALTIME_SAMPLE_EVENT.set()
        if owns_connection:
            diagnostics().record(
                "robot_telemetry",
                "primary_reader_stopped",
                "The robot Primary-interface telemetry reader stopped.",
                severity="warning",
                details={"host": reader_host, "generation": generation, "error": error},
            )


def _start_legacy_realtime_reader(host: str, timeout_seconds: float) -> None:
    global _REALTIME_CONNECTION, _REALTIME_CONNECTION_HOST, _REALTIME_READER
    global _REALTIME_READER_GENERATION, _REALTIME_READER_STARTED_AT
    global _REALTIME_READER_ERROR, _REALTIME_READER_ERROR_REPORTED

    replacing_connection = _REALTIME_CONNECTION is not None
    _disconnect_legacy_realtime()
    if replacing_connection:
        # Give URControl and Windows enough time to retire the old Primary
        # session before opening its replacement. This avoids accumulating
        # accepted-but-silent sockets after a network stall.
        sleep(0.2)
    try:
        connection = socket.create_connection(
            (host, _PRIMARY_INTERFACE_PORT),
            timeout=max(timeout_seconds, _REALTIME_CONNECT_TIMEOUT_SECONDS),
        )
        _configure_realtime_socket(connection)
    except OSError as exc:
        _REALTIME_READER_ERROR = str(exc)
        _REALTIME_READER_ERROR_REPORTED = True
        raise RobotTelemetryError(str(exc)) from exc
    _REALTIME_CONNECTION = connection
    _REALTIME_CONNECTION_HOST = host
    _REALTIME_READER_ERROR = None
    _REALTIME_READER_ERROR_REPORTED = False
    generation = _REALTIME_READER_GENERATION
    _REALTIME_READER_STARTED_AT = monotonic()
    _REALTIME_READER = Thread(
        target=_legacy_realtime_reader,
        args=(connection, generation, timeout_seconds),
        daemon=True,
        name="robot-realtime-reader",
    )
    _REALTIME_READER.start()
    diagnostics().record(
        "robot_telemetry",
        "primary_reader_started",
        "The robot Primary-interface telemetry reader started.",
        details={"host": host, "port": _PRIMARY_INTERFACE_PORT, "generation": generation},
    )


def _latest_buffered_realtime_packet() -> bytes | None:
    """Remove all complete frames and return only the newest controller sample."""
    latest: bytes | None = None
    while len(_REALTIME_BUFFER) >= 4:
        packet_size = struct.unpack_from(">I", _REALTIME_BUFFER)[0]
        if not 812 <= packet_size <= 2048:
            raise RobotTelemetryError("The robot returned an invalid realtime telemetry packet.")
        if len(_REALTIME_BUFFER) < packet_size:
            break
        latest = bytes(_REALTIME_BUFFER[:packet_size])
        del _REALTIME_BUFFER[:packet_size]
    return latest


def _receive_latest_realtime_packet(connection: socket.socket, timeout_seconds: float) -> bytes:
    """Read through any socket backlog so callers receive current, not queued, telemetry."""
    latest = _latest_buffered_realtime_packet()
    deadline = monotonic() + timeout_seconds
    while latest is None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RobotTelemetryError("No realtime telemetry packet was received before timeout.")
        connection.settimeout(remaining)
        chunk = connection.recv(65536)
        if not chunk:
            raise RobotTelemetryError("The robot closed its realtime telemetry connection.")
        _REALTIME_BUFFER.extend(chunk)
        latest = _latest_buffered_realtime_packet()

    # The controller streams much faster than the UI polls. Drain all bytes that
    # are already waiting and retain only the newest complete packet.
    drain_deadline = monotonic() + min(0.025, timeout_seconds)
    connection.settimeout(0.0)
    while monotonic() < drain_deadline:
        try:
            chunk = connection.recv(65536)
        except (BlockingIOError, socket.timeout):
            break
        if not chunk:
            raise RobotTelemetryError("The robot closed its realtime telemetry connection.")
        _REALTIME_BUFFER.extend(chunk)
        buffered = _latest_buffered_realtime_packet()
        if buffered is not None:
            latest = buffered
    connection.settimeout(timeout_seconds)
    return latest


def _read_legacy_realtime_sample(host: str, timeout_seconds: float) -> Any:
    """Read one controller state packet when an older robot does not stream RTDE."""
    global _REALTIME_READER_ERROR_REPORTED

    # Some older controllers permit only one realtime client. A dedicated reader
    # continuously drains that stream while browser requests consume its newest sample.
    with _LIVE_CONNECTION_LOCK:
        packet_fresh = (
            _REALTIME_LATEST_PACKET is not None
            and monotonic() - _REALTIME_LATEST_AT <= max(1.0, timeout_seconds)
        )
        reader_stale = (
            _REALTIME_READER is not None
            and _REALTIME_READER.is_alive()
            and monotonic() - (_REALTIME_LATEST_AT or _REALTIME_READER_STARTED_AT)
            > _REALTIME_READER_STALL_SECONDS
        )
        if not packet_fresh and (
            _REALTIME_READER is None or not _REALTIME_READER.is_alive() or reader_stale
        ):
            # A reader can remain blocked in recv after a network drop. Do not
            # replace it for an ordinary stale sample: opening overlapping
            # Primary sessions can leave older UR controllers silent. Replace
            # only after the reader's independent stall window expires.
            _start_legacy_realtime_reader(host, timeout_seconds)
        elif _REALTIME_CONNECTION_HOST != host:
            _start_legacy_realtime_reader(host, timeout_seconds)
        if packet_fresh:
            packet = _REALTIME_LATEST_PACKET
        else:
            _REALTIME_SAMPLE_EVENT.clear()

    if not packet_fresh:
        _REALTIME_SAMPLE_EVENT.wait(max(timeout_seconds, _REALTIME_INITIAL_SAMPLE_TIMEOUT_SECONDS))
        with _LIVE_CONNECTION_LOCK:
            if (
                _REALTIME_LATEST_PACKET is not None
                and monotonic() - _REALTIME_LATEST_AT <= max(1.0, timeout_seconds)
            ):
                packet = _REALTIME_LATEST_PACKET
            else:
                _REALTIME_READER_ERROR_REPORTED = True
                raise RobotTelemetryError(
                    _REALTIME_READER_ERROR or "No realtime telemetry packet was received before timeout."
                )

    if packet is None:  # Defensive narrowing for static type checkers.
        raise RobotTelemetryError("No realtime telemetry packet was received before timeout.")

    if len(packet) >= 5 and packet[4] == 16:
        return _parse_primary_state(packet)

    # Retain decoding support for recorded legacy realtime packets used by tests
    # and diagnostics, but production connections use the safer Primary stream.
    program_state = _realtime_value(packet, 132)
    runtime_states = {
        0: "stopping",
        1: "stopped",
        2: "playing",
        3: "pausing",
        4: "paused",
        5: "resuming",
    }
    return SimpleNamespace(
        timestamp=_realtime_value(packet, 1),
        actual_q=_realtime_vector(packet, 32),
        actual_qd=_realtime_vector(packet, 38),
        actual_current=_realtime_vector(packet, 44),
        actual_TCP_pose=_realtime_vector(packet, 56),
        actual_TCP_speed=_realtime_vector(packet, 62),
        actual_digital_input_bits=int(_realtime_value(packet, 86) or 0),
        robot_mode=_realtime_value(packet, 95),
        safety_mode=_realtime_value(packet, 102),
        speed_scaling=_realtime_value(packet, 118),
        actual_digital_output_bits=int(_realtime_value(packet, 131) or 0),
        runtime_state=runtime_states.get(int(program_state)) if program_state is not None else None,
        program_state=program_state,
    )


def _legacy_realtime_snapshot(host: str, timeout_seconds: float) -> dict[str, Any]:
    sample = _read_legacy_realtime_sample(host, timeout_seconds)
    digital_inputs = sample.actual_digital_input_bits
    digital_outputs = sample.actual_digital_output_bits
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "robot_primary",
        "connected": True,
        "transport_health": robot_telemetry_transport_status(),
        "connection_label": "Live Primary-interface telemetry",
        "robot": {
            "host": host,
            "port": _PRIMARY_INTERFACE_PORT,
            "controller_version": "",
            "recipe_fields": ["primary_robot_state"],
            "sample_time_seconds": sample.timestamp,
        },
        "digital_input_groups": [
            {"title": "Standard inputs", "rows": _bit_rows(digital_inputs, "DI", 8, direction="input", bank="standard")},
            {"title": "Configurable inputs", "rows": _bit_rows(digital_inputs, "CI", 8, 8, direction="input", bank="configurable")},
            {"title": "Tool inputs", "rows": _bit_rows(digital_inputs, "TI", 2, 16, direction="input", bank="tool")},
        ],
        "digital_output_groups": [
            {"title": "Standard outputs", "rows": _bit_rows(digital_outputs, "DO", 8, direction="output", bank="standard")},
            {"title": "Configurable outputs", "rows": _bit_rows(digital_outputs, "CO", 8, 8, direction="output", bank="configurable")},
            {"title": "Tool outputs", "rows": _bit_rows(digital_outputs, "TO", 2, 16, direction="output", bank="tool")},
        ],
        "analog_inputs": [
            _analog_row("AI0", "Standard analog input 0", _read_attr(sample, "standard_analog_input0"), _read_attr(sample, "analog_io_types"), 0),
            _analog_row("AI1", "Standard analog input 1", _read_attr(sample, "standard_analog_input1"), _read_attr(sample, "analog_io_types"), 1),
            _analog_row("TAI0", "Tool analog input 0", _read_attr(sample, "tool_analog_input0"), _read_attr(sample, "tool_analog_input_types"), 0),
            _analog_row("TAI1", "Tool analog input 1", _read_attr(sample, "tool_analog_input1"), _read_attr(sample, "tool_analog_input_types"), 1),
        ],
        "analog_outputs": [
            _analog_row("AO0", "Standard analog output 0", _read_attr(sample, "standard_analog_output0"), _read_attr(sample, "analog_io_types"), 2),
            _analog_row("AO1", "Standard analog output 1", _read_attr(sample, "standard_analog_output1"), _read_attr(sample, "analog_io_types"), 3),
        ],
        "state_rows": [
            {"label": "Robot mode", "value": sample.robot_mode},
            {"label": "Safety mode", "value": sample.safety_mode},
            {"label": "Runtime state", "value": sample.runtime_state},
            {"label": "Program state", "value": sample.program_state},
            {"label": "Speed scaling", "value": sample.speed_scaling},
        ],
        "pose_rows": _vector_rows(sample.actual_TCP_pose, "TCP", ["X", "Y", "Z", "Rx", "Ry", "Rz"]),
        "tcp_speed_rows": _vector_rows(sample.actual_TCP_speed, "SPD", ["Vx", "Vy", "Vz", "Wx", "Wy", "Wz"]),
        "joint_rows": _vector_rows(sample.actual_q, "J", ["Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3"]),
        "tcp_detail_rows": _tcp_detail_rows(sample),
        "joint_detail_rows": _joint_detail_rows(sample),
        "extra_actual_rows": [],
        "notes": (
            "This CB-series controller is read through one continuously drained Primary-interface connection on port 30001. "
            "I/O and motion state come from the same RobotState packet; automatic Modbus polling is disabled to protect this controller."
        ),
    }


def _read_robot_snapshot_once(host: str, port: int, poll_hz: int, timeout_seconds: float) -> dict[str, Any]:
    key = (host, port)
    with _SNAPSHOT_LOCK:
        if port in {30001, 30002, 30003}:
            _LEGACY_REALTIME_KEYS.add(key)
        # CB-series controllers can accept TCP 30004 while never emitting RTDE
        # packets. Once the Primary fallback is confirmed, keep using its single
        # persistent stream instead of renegotiating a silent port on every poll.
        if key in _LEGACY_REALTIME_KEYS:
            return _legacy_realtime_snapshot(host, timeout_seconds)
        try:
            sample, controller_version, recipe_used = _connect_and_sample(
                host,
                port,
                poll_hz,
                timeout_seconds,
            )
        except RobotTelemetryError as rtde_error:
            try:
                snapshot = _legacy_realtime_snapshot(host, timeout_seconds)
                _LEGACY_REALTIME_KEYS.add(key)
                return snapshot
            except RobotTelemetryError as realtime_error:
                raise RobotTelemetryError(
                    f"RTDE telemetry failed ({rtde_error}); realtime fallback failed ({realtime_error})"
                ) from realtime_error
    digital_inputs = _read_attr(sample, "actual_digital_input_bits")
    digital_outputs = _read_attr(sample, "actual_digital_output_bits")
    configurable_inputs: int | None = None
    configurable_outputs: int | None = None
    legacy_registers: dict[int, int] | None = None
    legacy_standard_io = recipe_used == LEGACY_OUTPUT_RECIPE
    if legacy_standard_io:
        try:
            legacy_registers = _cached_legacy_controller_io(host, timeout_seconds)
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


def read_robot_snapshot(host: str, port: int, poll_hz: int, timeout_seconds: float) -> dict[str, Any]:
    """Read telemetry with a capped exponential retry delay after connection failures."""
    key = (host, port)
    with _SNAPSHOT_LOCK:
        if _CONNECTIONS_SUSPENDED:
            raise RobotTelemetryError("Robot communications are suspended while the backend relaunches.")
        now = monotonic()
        retry_after = _TELEMETRY_RETRY_AFTER.get(key, 0.0)
        if now < retry_after:
            # A persistent reader can recover while the connection circuit is cooling
            # down. Consume that live stream immediately instead of waiting to reconnect.
            with _LIVE_CONNECTION_LOCK:
                stream_recovered = (
                    _REALTIME_CONNECTION_HOST == host
                    and _REALTIME_READER is not None
                    and _REALTIME_READER.is_alive()
                    and _REALTIME_LATEST_PACKET is not None
                    and now - _REALTIME_LATEST_AT <= max(1.0, timeout_seconds)
                )
            if not stream_recovered:
                remaining = max(1, math.ceil(retry_after - now))
                last_error = _TELEMETRY_LAST_ERROR.get(key)
                detail = f" Last failure: {last_error}" if last_error else ""
                raise RobotTelemetryError(
                    f"Robot telemetry reconnect is cooling down for {remaining} second{'s' if remaining != 1 else ''}.{detail}"
                )
            _TELEMETRY_RETRY_AFTER.pop(key, None)
        try:
            snapshot = _read_robot_snapshot_once(host, port, poll_hz, timeout_seconds)
        except RobotTelemetryError as exc:
            failures = _TELEMETRY_FAILURE_COUNT.get(key, 0) + 1
            _TELEMETRY_FAILURE_COUNT[key] = failures
            _TELEMETRY_LAST_ERROR[key] = str(exc)
            delay = min(
                _TELEMETRY_RETRY_BASE_SECONDS * (2 ** (failures - 1)),
                _TELEMETRY_RETRY_MAX_SECONDS,
            )
            _TELEMETRY_RETRY_AFTER[key] = now + delay
            raise
        _TELEMETRY_FAILURE_COUNT.pop(key, None)
        _TELEMETRY_RETRY_AFTER.pop(key, None)
        _TELEMETRY_LAST_ERROR.pop(key, None)
        return snapshot


def reset_robot_connections() -> None:
    """Close controller sockets and clear reconnect cooldowns on operator request."""
    with _SNAPSHOT_LOCK:
        if _CONNECTIONS_SUSPENDED:
            raise RobotTelemetryError("Robot communications are suspended while the backend relaunches.")
        with _LIVE_CONNECTION_LOCK:
            _disconnect_live_connection()
            _disconnect_legacy_realtime()
        with _MODBUS_IO_LOCK:
            _disconnect_modbus_connection()
        _TELEMETRY_RETRY_AFTER.clear()
        _TELEMETRY_FAILURE_COUNT.clear()
        _TELEMETRY_LAST_ERROR.clear()


def suspend_robot_connections() -> None:
    """Stop new controller connections and gracefully close persistent sessions."""
    global _CONNECTIONS_SUSPENDED
    with _SNAPSHOT_LOCK:
        _CONNECTIONS_SUSPENDED = True
        with _LIVE_CONNECTION_LOCK:
            _disconnect_live_connection()
            _disconnect_legacy_realtime()
        with _MODBUS_IO_LOCK:
            _disconnect_modbus_connection()


def resume_robot_connections() -> None:
    """Enable controller access for a newly started application lifecycle."""
    global _CONNECTIONS_SUSPENDED
    with _SNAPSHOT_LOCK:
        _CONNECTIONS_SUSPENDED = False
        _TELEMETRY_RETRY_AFTER.clear()
        _TELEMETRY_FAILURE_COUNT.clear()
        _TELEMETRY_LAST_ERROR.clear()
