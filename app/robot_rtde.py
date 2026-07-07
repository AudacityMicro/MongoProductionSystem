from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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


def _read_attr(sample: Any, name: str) -> Any:
    return getattr(sample, name, None)


def _connect_and_sample(host: str, port: int, poll_hz: int, timeout_seconds: float) -> tuple[Any, tuple[int | None, int | None, int | None, int | None], list[str]]:
    if rtde_client is None:
        raise RobotTelemetryError("The RTDE Python client library is not installed.")

    original_timeout = rtde_client.DEFAULT_TIMEOUT
    rtde_client.DEFAULT_TIMEOUT = timeout_seconds
    try:
        connection = rtde_client.RTDE(host, port)
        connection.connect()
        try:
            controller_version = connection.get_controller_version()
            recipe_used: list[str] | None = None
            for recipe in (FULL_OUTPUT_RECIPE, MID_OUTPUT_RECIPE, CORE_OUTPUT_RECIPE):
                if connection.send_output_setup(recipe, frequency=poll_hz):
                    recipe_used = recipe
                    break
            if recipe_used is None:
                raise RobotTelemetryError(
                    "The robot rejected the RTDE output recipe. Check controller RTDE support."
                )
            if not connection.send_start():
                raise RobotTelemetryError("The robot refused to start RTDE data synchronization.")

            sample = None
            for _ in range(3):
                sample = connection.receive()
                if sample is not None:
                    break
            connection.send_pause()
            if sample is None:
                raise RobotTelemetryError("No RTDE sample was received before timeout.")
            return sample, controller_version, recipe_used
        finally:
            connection.disconnect()
    except Exception as exc:  # pragma: no cover - exercised via endpoint behavior
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
    analog_modes = _read_attr(sample, "analog_io_types")
    tool_analog_modes = _read_attr(sample, "tool_analog_input_types")
    sample_values = dict(getattr(sample, "__dict__", {}))

    handled_vector_fields = {
        "actual_TCP_pose",
        "actual_TCP_speed",
        "actual_q",
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
                "rows": _bit_rows(digital_inputs, "CI", 8, 8, direction="input", bank="configurable"),
            },
            {
                "title": "Tool inputs",
                "rows": _bit_rows(digital_inputs, "TI", 2, 16, direction="input", bank="tool"),
            },
        ],
        "digital_output_groups": [
            {
                "title": "Standard outputs",
                "rows": _bit_rows(digital_outputs, "DO", 8, 0, direction="output", bank="standard"),
            },
            {
                "title": "Configurable outputs",
                "rows": _bit_rows(digital_outputs, "CO", 8, 8, direction="output", bank="configurable"),
            },
            {
                "title": "Tool outputs",
                "rows": _bit_rows(digital_outputs, "TO", 2, 16, direction="output", bank="tool"),
            },
        ],
        "analog_inputs": [
            _analog_row(
                "AI0",
                "Standard analog input 0",
                _read_attr(sample, "standard_analog_input0"),
                analog_modes,
                0,
            ),
            _analog_row(
                "AI1",
                "Standard analog input 1",
                _read_attr(sample, "standard_analog_input1"),
                analog_modes,
                1,
            ),
            _analog_row(
                "TAI0",
                "Tool analog input 0",
                _read_attr(sample, "tool_analog_input0"),
                tool_analog_modes,
                0,
            ),
            _analog_row(
                "TAI1",
                "Tool analog input 1",
                _read_attr(sample, "tool_analog_input1"),
                tool_analog_modes,
                1,
            ),
        ],
        "analog_outputs": [
            _analog_row(
                "AO0",
                "Standard analog output 0",
                _read_attr(sample, "standard_analog_output0"),
                analog_modes,
                2,
            ),
            _analog_row(
                "AO1",
                "Standard analog output 1",
                _read_attr(sample, "standard_analog_output1"),
                analog_modes,
                3,
            ),
        ],
        "state_rows": [
            {"label": "Robot mode", "value": _read_attr(sample, "robot_mode")},
            {"label": "Safety mode", "value": _read_attr(sample, "safety_mode")},
            {"label": "Runtime state", "value": _read_attr(sample, "runtime_state")},
            {"label": "Speed scaling", "value": _read_attr(sample, "speed_scaling")},
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
        "extra_actual_rows": extra_actual_rows,
        "notes": "Reading live robot telemetry over RTDE port 30004.",
    }
