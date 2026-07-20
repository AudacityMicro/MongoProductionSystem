import struct

import pytest

from app import robot_rtde


class FakeModbusConnection:
    def __init__(self) -> None:
        self.response = b""
        self.requests = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def close(self) -> None:
        return None

    def sendall(self, request: bytes) -> None:
        self.requests += 1
        if request[7] == 6:
            self.response = request
            return
        transaction_id = struct.unpack(">H", request[:2])[0]
        address, count = struct.unpack(">HH", request[8:12])
        values = [address + offset for offset in range(count)]
        payload = struct.pack(">BB", 3, count * 2) + struct.pack(f">{count}H", *values)
        self.response = struct.pack(">HHHB", transaction_id, 0, len(payload) + 1, 0) + payload

    def recv(self, size: int) -> bytes:
        result, self.response = self.response[:size], self.response[size:]
        return result


class FakeRealtimeConnection:
    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)
        self.timeout = 1.0

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, size: int) -> bytes:
        if not self.data:
            if self.timeout == 0:
                raise BlockingIOError
            return b""
        result = bytes(self.data[:size])
        del self.data[:size]
        return result

    def close(self) -> None:
        return None


def realtime_packet(timestamp: float) -> bytes:
    packet = bytearray(1060)
    struct.pack_into(">I", packet, 0, len(packet))
    struct.pack_into(">d", packet, 4, timestamp)
    struct.pack_into(">d", packet, 4 + ((132 - 1) * 8), 1.0)
    return bytes(packet)


def test_legacy_io_uses_one_modbus_connection(monkeypatch) -> None:
    robot_rtde.resume_robot_connections()
    with robot_rtde._MODBUS_IO_LOCK:
        robot_rtde._disconnect_modbus_connection()
    connection = FakeModbusConnection()
    connections = []

    def fake_connect(*_args, **_kwargs):
        connections.append(connection)
        return connection

    monkeypatch.setattr(robot_rtde.socket, "create_connection", fake_connect)

    first = robot_rtde._read_legacy_controller_io("192.0.2.10", 0.5)
    second = robot_rtde._read_legacy_controller_io("192.0.2.10", 0.5)

    assert len(connections) == 1
    assert connection.requests == 8
    expected = {address: address for address in (0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 19, 30, 31)}
    assert first == expected
    assert second == expected
    with robot_rtde._MODBUS_IO_LOCK:
        robot_rtde._disconnect_modbus_connection()


def test_realtime_reader_discards_queued_old_packets() -> None:
    robot_rtde._REALTIME_BUFFER.clear()
    connection = FakeRealtimeConnection(
        realtime_packet(10.0) + realtime_packet(11.0) + realtime_packet(12.0)
    )

    packet = robot_rtde._receive_latest_realtime_packet(connection, 0.5)

    assert robot_rtde._realtime_value(packet, 1) == 12.0
    assert robot_rtde._REALTIME_BUFFER == bytearray()


def test_background_realtime_reader_continuously_drains_to_latest_packet(monkeypatch) -> None:
    robot_rtde.resume_robot_connections()
    with robot_rtde._LIVE_CONNECTION_LOCK:
        robot_rtde._disconnect_legacy_realtime()
    connection = FakeRealtimeConnection(
        realtime_packet(20.0) + realtime_packet(21.0) + realtime_packet(22.0)
    )
    monkeypatch.setattr(robot_rtde.socket, "create_connection", lambda *_args, **_kwargs: connection)

    sample = robot_rtde._read_legacy_realtime_sample("192.0.2.40", 0.5)

    assert sample.timestamp == 22.0
    assert connection.data == bytearray()
    with robot_rtde._LIVE_CONNECTION_LOCK:
        robot_rtde._disconnect_legacy_realtime()


def test_realtime_snapshot_does_not_poll_modbus_automatically(monkeypatch) -> None:
    sample = robot_rtde.SimpleNamespace(
        timestamp=1.0,
        actual_q=[0.0] * 6,
        actual_qd=[0.0] * 6,
        actual_current=[0.0] * 6,
        actual_TCP_pose=[0.0] * 6,
        actual_TCP_speed=[0.0] * 6,
        actual_digital_input_bits=0x201,
        actual_digital_output_bits=0x402,
        robot_mode=7.0,
        safety_mode=1.0,
        speed_scaling=1.0,
        runtime_state="stopped",
        program_state=1.0,
    )
    monkeypatch.setattr(robot_rtde, "_read_legacy_realtime_sample", lambda *_args: sample)
    monkeypatch.setattr(
        robot_rtde,
        "_cached_legacy_controller_io",
        lambda *_args: (_ for _ in ()).throw(AssertionError("automatic Modbus read")),
    )

    snapshot = robot_rtde._legacy_realtime_snapshot("192.0.2.50", 0.5)

    assert snapshot["connected"] is True
    assert snapshot["digital_input_groups"][0]["rows"][0]["value"] is True
    assert "automatic Modbus polling is disabled" in snapshot["notes"]


def test_output_toggle_updates_cached_register(monkeypatch) -> None:
    host = "192.0.2.20"
    robot_rtde._MODBUS_IO_CACHE[host] = (0.0, {1: 0, 31: 0})
    connection = FakeModbusConnection()
    monkeypatch.setattr(robot_rtde.socket, "create_connection", lambda *_args, **_kwargs: connection)

    robot_rtde.toggle_robot_digital_output(host, 30003, 0.5, "standard", 4)

    assert connection.requests == 2
    assert robot_rtde._MODBUS_IO_CACHE[host][1][1] == 17


def test_telemetry_failure_uses_bounded_automatic_reconnect_backoff(monkeypatch) -> None:
    host = "192.0.2.30"
    key = (host, 30003)
    calls = []
    robot_rtde.resume_robot_connections()

    def fail_once(*args):
        calls.append(args)
        raise robot_rtde.RobotTelemetryError("controller did not answer")

    monkeypatch.setattr(robot_rtde, "_read_robot_snapshot_once", fail_once)
    monkeypatch.setattr(robot_rtde, "monotonic", lambda: 100.0)

    with pytest.raises(robot_rtde.RobotTelemetryError, match="controller did not answer"):
        robot_rtde.read_robot_snapshot(host, 30003, 10, 1.0)
    with pytest.raises(robot_rtde.RobotTelemetryError, match="cooling down for 2 seconds"):
        robot_rtde.read_robot_snapshot(host, 30003, 10, 1.0)

    assert len(calls) == 1
    robot_rtde._TELEMETRY_RETRY_AFTER.pop(key, None)
    robot_rtde._TELEMETRY_FAILURE_COUNT.pop(key, None)


def test_telemetry_retries_after_cooldown_and_clears_backoff_on_success(monkeypatch) -> None:
    host = "192.0.2.31"
    key = (host, 30003)
    now = {"value": 100.0}
    calls = []
    robot_rtde.resume_robot_connections()

    def fail_then_succeed(*args):
        calls.append(args)
        if len(calls) == 1:
            raise robot_rtde.RobotTelemetryError("stream stopped")
        return {"connected": True}

    monkeypatch.setattr(robot_rtde, "_read_robot_snapshot_once", fail_then_succeed)
    monkeypatch.setattr(robot_rtde, "monotonic", lambda: now["value"])

    with pytest.raises(robot_rtde.RobotTelemetryError, match="stream stopped"):
        robot_rtde.read_robot_snapshot(host, 30003, 10, 1.0)

    with pytest.raises(robot_rtde.RobotTelemetryError, match="cooling down for 2 seconds"):
        robot_rtde.read_robot_snapshot(host, 30003, 10, 1.0)
    assert len(calls) == 1
    assert robot_rtde._TELEMETRY_RETRY_AFTER[key] == 102.0

    now["value"] = 102.0
    assert robot_rtde.read_robot_snapshot(host, 30003, 10, 1.0) == {"connected": True}
    assert key not in robot_rtde._TELEMETRY_RETRY_AFTER


def test_stale_legacy_reader_is_replaced_before_its_socket_timeout(monkeypatch) -> None:
    class StaleReader:
        def is_alive(self) -> bool:
            return True

    host = "192.0.2.32"
    starts: list[tuple[str, float]] = []
    robot_rtde.resume_robot_connections()
    with robot_rtde._LIVE_CONNECTION_LOCK:
        robot_rtde._REALTIME_CONNECTION_HOST = host
        robot_rtde._REALTIME_READER = StaleReader()
        robot_rtde._REALTIME_LATEST_PACKET = b"stale"
        robot_rtde._REALTIME_LATEST_AT = 0.0
    monkeypatch.setattr(robot_rtde, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        robot_rtde,
        "_start_legacy_realtime_reader",
        lambda started_host, timeout: starts.append((started_host, timeout)),
    )

    with pytest.raises(robot_rtde.RobotTelemetryError):
        robot_rtde._read_legacy_realtime_sample(host, 0.01)

    assert starts == [(host, 0.01)]
    with robot_rtde._LIVE_CONNECTION_LOCK:
        robot_rtde._disconnect_legacy_realtime()
