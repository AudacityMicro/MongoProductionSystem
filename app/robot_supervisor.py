from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
import socket
import struct
import threading
import time
from typing import Iterable

from app.diagnostics import diagnostics


PROTOCOL_VERSION = 1
CHECKSUM_MODULUS = 65521

KIND_COMMAND = 10
KIND_HEARTBEAT = 11
KIND_HELLO = 20
KIND_EVENT = 21
KIND_TELEMETRY = 22

EVENT_ACCEPTED = 1
EVENT_RUNNING = 2
EVENT_COMPLETED = 3
EVENT_FAULTED = 4
EVENT_LATCHED = 5
EVENT_IDLE = 6

EVENT_NAMES = {
    EVENT_ACCEPTED: "accepted",
    EVENT_RUNNING: "running",
    EVENT_COMPLETED: "completed",
    EVENT_FAULTED: "faulted",
    EVENT_LATCHED: "latched",
    EVENT_IDLE: "idle",
}
TERMINAL_EVENTS = {EVENT_COMPLETED, EVENT_FAULTED, EVENT_LATCHED}

OP_PICK_POOL = 1
OP_PUT_POOL = 2
OP_LOAD_MILL = 3
OP_UNLOAD_MILL = 4
OP_RELIABILITY_POOL = 5
OP_SET_STANDARD_OUTPUT = 10
OP_SET_CONFIGURABLE_OUTPUT = 11
OP_SET_TOOL_OUTPUT = 12
OP_CLEAR_LATCH = 20
OP_ENTER_MAINTENANCE = 21


class SupervisorProtocolError(ValueError):
    pass


def frame_checksum(values: Iterable[int]) -> int:
    return sum(int(value) for value in values) % CHECKSUM_MODULUS


def encode_frame(kind: int, fields: Iterable[int]) -> bytes:
    values = [PROTOCOL_VERSION, int(kind), *(int(value) for value in fields)]
    values.append(frame_checksum(values))
    try:
        return struct.pack(f"!{len(values)}i", *values)
    except struct.error as exc:
        raise SupervisorProtocolError("Supervisor frame contains a value outside the signed 32-bit range.") from exc


def decode_frame(frame: bytes) -> list[int]:
    if len(frame) % 4:
        raise SupervisorProtocolError("Supervisor binary frame is not aligned to 32-bit integers.")
    try:
        values = list(struct.unpack(f"!{len(frame) // 4}i", frame))
    except struct.error as exc:
        raise SupervisorProtocolError("Supervisor binary frame could not be decoded.") from exc
    if len(values) < 4:
        raise SupervisorProtocolError("Supervisor frame is too short.")
    if values[0] != PROTOCOL_VERSION:
        raise SupervisorProtocolError(f"Unsupported supervisor protocol version {values[0]}.")
    if frame_checksum(values[:-1]) != values[-1]:
        raise SupervisorProtocolError("Supervisor frame checksum is invalid.")
    return values[:-1]


class FrameBuffer:
    """Reassemble fixed-schema binary integer frames from fragmented TCP reads."""

    _FRAME_INTS = {
        KIND_COMMAND: 9,
        KIND_HEARTBEAT: 9,
        KIND_HELLO: 9,
        KIND_EVENT: 9,
        KIND_TELEMETRY: 50,
    }

    def __init__(self, maximum_bytes: int = 32768):
        self._buffer = bytearray()
        self.maximum_bytes = maximum_bytes
        self.resynchronizations = 0

    def _discard_until_header(self) -> bool:
        """Discard aligned garbage while retaining a possible partial frame header."""
        for offset in range(4, len(self._buffer) - 7, 4):
            version, kind = struct.unpack("!2i", self._buffer[offset : offset + 8])
            if version == PROTOCOL_VERSION and kind in self._FRAME_INTS:
                del self._buffer[:offset]
                self.resynchronizations += 1
                return True
        keep = self._buffer[-4:] if len(self._buffer) >= 4 and struct.unpack("!i", self._buffer[-4:])[0] == PROTOCOL_VERSION else b""
        self._buffer.clear()
        self._buffer.extend(keep)
        self.resynchronizations += 1
        return False

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        if len(self._buffer) > self.maximum_bytes:
            self._buffer.clear()
            raise SupervisorProtocolError("Supervisor receive buffer exceeded its limit.")
        frames: list[bytes] = []
        while True:
            if len(self._buffer) < 8:
                break
            version, kind = struct.unpack("!2i", self._buffer[:8])
            frame_ints = self._FRAME_INTS.get(kind)
            if version != PROTOCOL_VERSION or frame_ints is None:
                if not self._discard_until_header():
                    break
                continue
            frame_bytes = frame_ints * 4
            if len(self._buffer) < frame_bytes:
                break
            candidate = bytes(self._buffer[:frame_bytes])
            values = struct.unpack(f"!{frame_ints}i", candidate)
            if frame_checksum(values[:-1]) != values[-1]:
                # An interrupted sequence of socket_send_int calls can leave one
                # malformed frame in an otherwise healthy old-controller stream.
                del self._buffer[:4]
                self.resynchronizations += 1
                continue
            frames.append(candidate)
            del self._buffer[:frame_bytes]
        return frames


@dataclass(slots=True)
class SupervisorEvent:
    sequence: int
    event_code: int
    fault_code: int = 0
    robot_state: int = 0
    robot_session: int = 0
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def name(self) -> str:
        return EVENT_NAMES.get(self.event_code, f"unknown_{self.event_code}")


@dataclass(slots=True)
class DispatchReceipt:
    sequence: int
    attempted: bool
    sent: bool
    detail: str = ""


class RobotSupervisorManager:
    """Own the one robot-originated socket without coupling socket threads to SQLAlchemy."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._connections: set[socket.socket] = set()
        self._connection_threads: set[threading.Thread] = set()
        self._peer: str | None = None
        self._listen_host = "0.0.0.0"
        self._listen_port = 50010
        self._heartbeat_seconds = 1.0
        self._app_session = secrets.randbelow(1_999_999_999) + 1
        self._connected_at: float | None = None
        self._connection_generation = 0
        self._last_seen_at: float | None = None
        self._last_disconnect_at: float | None = None
        self._last_disconnect_detail = ""
        self._robot_session: int | None = None
        self._robot_last_sequence = 0
        self._robot_last_event = 0
        self._robot_latched = False
        self._events: OrderedDict[int, SupervisorEvent] = OrderedDict()
        self._event_history: OrderedDict[int, list[SupervisorEvent]] = OrderedDict()
        self._telemetry: dict[str, object] = {}
        self._telemetry_at: float | None = None
        self._protocol_errors = 0
        self._protocol_resynchronizations = 0
        self._rejected_connections = 0
        self._maximum_retained_sequences = 512

    def start(self, host: str, port: int, heartbeat_seconds: float, telemetry_hz: float = 1.0) -> None:
        with self._lock:
            unchanged = (
                self._listener_thread
                and self._listener_thread.is_alive()
                and self._listen_host == host
                and self._listen_port == port
            )
            configured_heartbeat = max(0.25, float(heartbeat_seconds))
            telemetry_tick = max(0.1, 1.0 / max(0.25, float(telemetry_hz)))
            # PolyScope 3.2 socket reads have no timeout argument. Heartbeat frames
            # also wake the reader often enough to maintain the requested telemetry rate.
            self._heartbeat_seconds = min(configured_heartbeat, telemetry_tick)
            if unchanged:
                return
        self.stop()
        with self._lock:
            self._listen_host = host
            self._listen_port = int(port)
            self._stop.clear()
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                daemon=True,
                name="robot-supervisor-listener",
            )
            self._listener_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            was_running = bool(self._listener_thread or self._listener or self._connections)
            sockets = [*self._connections, self._listener]
            thread = self._listener_thread
            self._connection = None
            self._listener = None
            self._listener_thread = None
            self._connections.clear()
            self._condition.notify_all()
        for item in sockets:
            if item:
                try:
                    item.close()
                except OSError:
                    pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        if was_running:
            diagnostics().record("robot_supervisor", "listener_stopped", "Robot supervisor listener stopped.")

    def _listen_loop(self) -> None:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._listen_host, self._listen_port))
            listener.listen(2)
            listener.settimeout(0.5)
            with self._lock:
                self._listener = listener
            diagnostics().record(
                "robot_supervisor",
                "listener_started",
                "Robot supervisor listener started.",
                details={"host": self._listen_host, "port": self._listen_port},
            )
        except OSError as exc:
            with self._lock:
                self._last_disconnect_detail = f"Supervisor listener failed: {exc}"
                self._condition.notify_all()
            diagnostics().record(
                "robot_supervisor",
                "listener_failed",
                "Robot supervisor listener could not start.",
                severity="error",
                details={"error": str(exc), "host": self._listen_host, "port": self._listen_port},
            )
            return

        while not self._stop.is_set():
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connection.settimeout(0.25)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            with self._lock:
                self._connections.add(connection)
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection, address),
                daemon=True,
                name=f"robot-supervisor-{address[0]}",
            )
            with self._lock:
                self._connection_threads.add(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket, address: tuple[str, int]) -> None:
        peer = f"{address[0]}:{address[1]}"
        buffer = FrameBuffer()
        handshake_deadline = time.monotonic() + 5.0
        validated = False
        disconnect_detail = "Robot closed the supervisor connection."
        last_heartbeat = 0.0
        diagnostics().record(
            "robot_supervisor",
            "tcp_accepted",
            "Supervisor TCP connection is awaiting robot handshake.",
            details={"peer": peer},
        )
        try:
            while not self._stop.is_set():
                if not validated and time.monotonic() >= handshake_deadline:
                    raise SupervisorProtocolError("Robot did not send a valid HELLO within five seconds.")
                if validated and time.monotonic() - last_heartbeat >= self._heartbeat_seconds:
                    self._send_heartbeat(connection)
                    last_heartbeat = time.monotonic()
                try:
                    chunk = connection.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                previous_resynchronizations = buffer.resynchronizations
                frames = buffer.feed(chunk)
                if buffer.resynchronizations > previous_resynchronizations:
                    recovered = buffer.resynchronizations - previous_resynchronizations
                    with self._lock:
                        self._protocol_resynchronizations += recovered
                    diagnostics().record(
                        "robot_supervisor",
                        "stream_resynchronized",
                        "Discarded a malformed supervisor frame and resumed on the next valid frame.",
                        severity="warning",
                        details={"peer": peer, "discarded_segments": recovered},
                    )
                for frame in frames:
                    values = decode_frame(frame)
                    if not validated:
                        if values[1] != KIND_HELLO:
                            raise SupervisorProtocolError("First robot frame was not HELLO.")
                        self._activate_connection(connection, peer, values)
                        validated = True
                        continue
                    self._handle_frame(values, connection)
        except SupervisorProtocolError as exc:
            disconnect_detail = f"Supervisor protocol fault: {exc}"
            with self._lock:
                self._protocol_errors += 1
                if not validated:
                    self._rejected_connections += 1
            diagnostics().record(
                "robot_supervisor",
                "protocol_fault",
                disconnect_detail,
                severity="error",
                details={"peer": peer, "validated": validated},
            )
        except OSError as exc:
            disconnect_detail = f"Supervisor connection failed: {exc}"
        finally:
            try:
                connection.close()
            except OSError:
                pass
            with self._lock:
                self._connections.discard(connection)
                self._connection_threads.discard(threading.current_thread())
                if self._connection is connection:
                    self._connection = None
                    self._peer = None
                    self._last_disconnect_at = time.monotonic()
                    self._last_disconnect_detail = disconnect_detail
                self._condition.notify_all()
            if validated:
                diagnostics().record(
                    "robot_supervisor",
                    "disconnected",
                    disconnect_detail,
                    severity="warning",
                    details={"peer": peer},
                )

    def _activate_connection(self, connection: socket.socket, peer: str, values: list[int]) -> None:
        if len(values) != 8:
            raise SupervisorProtocolError("Supervisor hello frame has the wrong field count.")
        _, _, session, last_sequence, last_event, latched, _state, _reserved = values
        with self._lock:
            old = self._connection
            previous_session = self._robot_session
            self._connection = connection
            self._connection_generation += 1
            now = time.monotonic()
            self._connected_at = now
            self._last_seen_at = now
            self._last_disconnect_detail = ""
            self._peer = peer
            self._robot_session = session
            self._robot_last_sequence = last_sequence
            self._robot_last_event = last_event
            self._robot_latched = bool(latched)
            if previous_session is not None and previous_session != session:
                self._events.clear()
                self._event_history.clear()
                self._telemetry.clear()
                self._telemetry_at = None
            self._condition.notify_all()
        if old and old is not connection:
            try:
                old.close()
            except OSError:
                pass
        diagnostics().record(
            "robot_supervisor",
            "handshake_completed",
            "Validated robot supervisor handshake.",
            details={
                "peer": peer,
                "robot_session": session,
                "last_sequence": last_sequence,
                "last_event": EVENT_NAMES.get(last_event, last_event),
                "latched": bool(latched),
            },
        )

    def _send_heartbeat(self, connection: socket.socket) -> None:
        with self._lock:
            last_sequence = self._robot_last_sequence
        frame = encode_frame(
            KIND_HEARTBEAT,
            [self._app_session, last_sequence, 0, 0, 0, 0],
        )
        with self._send_lock:
            connection.sendall(frame)

    def _handle_frame(self, values: list[int], connection: socket.socket | None = None) -> None:
        kind = values[1]
        with self._lock:
            if connection is not None and self._connection is not connection:
                raise SupervisorProtocolError("Frame arrived from a superseded supervisor connection.")
            self._last_seen_at = time.monotonic()
            if kind == KIND_HELLO:
                if len(values) != 8:
                    raise SupervisorProtocolError("Supervisor hello frame has the wrong field count.")
                _, _, session, last_sequence, last_event, latched, _state, _reserved = values
                if self._robot_session is not None and session != self._robot_session:
                    raise SupervisorProtocolError("Robot session changed without a new TCP handshake.")
                self._robot_session = session
                self._robot_last_sequence = last_sequence
                self._robot_last_event = last_event
                self._robot_latched = bool(latched)
            elif kind == KIND_EVENT:
                if len(values) != 8:
                    raise SupervisorProtocolError("Supervisor event frame has the wrong field count.")
                _, _, session, sequence, event_code, fault_code, robot_state, last_sequence = values
                if self._robot_session is not None and session != self._robot_session:
                    raise SupervisorProtocolError(f"Event session {session} does not match active robot session {self._robot_session}.")
                if event_code not in EVENT_NAMES:
                    raise SupervisorProtocolError(f"Unknown event code {event_code} for sequence {sequence}.")
                event = SupervisorEvent(sequence, event_code, fault_code, robot_state, session)
                previous = self._events.get(sequence)
                if sequence <= 0:
                    raise SupervisorProtocolError("Supervisor event sequence must be positive.")
                if (
                    previous
                    and previous.event_code in TERMINAL_EVENTS
                    and event_code != previous.event_code
                ):
                    raise SupervisorProtocolError(f"Conflicting terminal event for sequence {sequence}.")
                if previous and self._event_rank(event_code) < self._event_rank(previous.event_code):
                    raise SupervisorProtocolError(f"Out-of-order event for sequence {sequence}.")
                self._events[sequence] = event
                self._events.move_to_end(sequence)
                history = self._event_history.setdefault(sequence, [])
                history.append(event)
                del history[:-16]
                self._event_history.move_to_end(sequence)
                self._prune_events()
                self._robot_session = session
                self._robot_last_sequence = max(last_sequence, sequence)
                self._robot_last_event = event_code
                self._robot_latched = event_code == EVENT_LATCHED
            elif kind == KIND_TELEMETRY:
                telemetry = self._decode_telemetry(values)
                telemetry_session = int(telemetry["robot_session"])
                if self._robot_session is not None and telemetry_session != self._robot_session:
                    raise SupervisorProtocolError(
                        f"Telemetry session {telemetry_session} does not match active robot session {self._robot_session}."
                    )
                self._telemetry = telemetry
                self._telemetry_at = time.monotonic()
                self._robot_session = telemetry_session
                self._robot_last_sequence = int(self._telemetry["sequence"])
            else:
                raise SupervisorProtocolError(f"Unexpected robot frame kind {kind}.")
            self._condition.notify_all()

    def _prune_events(self) -> None:
        while len(self._events) > self._maximum_retained_sequences:
            sequence, _event = self._events.popitem(last=False)
            self._event_history.pop(sequence, None)

    @staticmethod
    def _event_rank(event_code: int) -> int:
        return {
            EVENT_ACCEPTED: 1,
            EVENT_RUNNING: 2,
            EVENT_COMPLETED: 3,
            EVENT_FAULTED: 3,
            EVENT_LATCHED: 4,
            EVENT_IDLE: 0,
        }.get(event_code, -1)

    @staticmethod
    def _decode_telemetry(values: list[int]) -> dict[str, object]:
        # Header through I/O consumes eleven fields; six groups of six values follow.
        if len(values) != 49:
            raise SupervisorProtocolError(
                f"Supervisor telemetry frame has {len(values)} fields; expected 49."
            )
        session, sequence = values[2], values[3]
        telemetry: dict[str, object] = {
            "robot_session": session,
            "sequence": sequence,
            "robot_mode": values[4],
            "safety_mode": values[5],
            "runtime_state": values[6],
            "standard_inputs": values[7],
            "standard_outputs": values[8],
            "configurable_inputs": values[9],
            "configurable_outputs": values[10],
            "tool_inputs": values[11],
            "tool_outputs": values[12],
        }
        offset = 13
        scales = (
            ("joint_positions_rad", 1000.0),
            ("joint_velocities_rad_s", 1000.0),
            ("joint_currents_a", 1000.0),
            ("joint_temperatures_c", 10.0),
            ("tcp_pose", 1000.0),
            ("tcp_speed", 1000.0),
        )
        for name, scale in scales:
            telemetry[name] = [value / scale for value in values[offset : offset + 6]]
            offset += 6
        telemetry["received_at"] = datetime.now(timezone.utc).isoformat()
        return telemetry

    def dispatch(
        self,
        sequence: int,
        opcode: int,
        argument: int = 0,
        value: int = 0,
        payload_g: int = 0,
        *,
        expected_robot_session: int | None = None,
    ) -> DispatchReceipt:
        with self._lock:
            connection = self._connection
            if not connection:
                return DispatchReceipt(sequence, attempted=False, sent=False, detail="Supervisor is not connected.")
            if expected_robot_session is not None and self._robot_session != expected_robot_session:
                return DispatchReceipt(
                    sequence,
                    attempted=False,
                    sent=False,
                    detail="Robot session changed before command transmission; reconcile before retrying.",
                )
        frame = encode_frame(
            KIND_COMMAND,
            [self._app_session, sequence, opcode, argument, value, payload_g],
        )
        try:
            with self._send_lock:
                with self._lock:
                    if self._connection is not connection:
                        return DispatchReceipt(sequence, attempted=False, sent=False, detail="Supervisor connection changed before transmission.")
                connection.sendall(frame)
        except OSError as exc:
            return DispatchReceipt(
                sequence,
                attempted=True,
                sent=False,
                detail=f"Supervisor command transmission became uncertain: {exc}",
            )
        diagnostics().record(
            "robot_supervisor",
            "command_sent",
            "Supervisor command frame sent.",
            details={"sequence": sequence, "opcode": opcode, "argument": argument},
        )
        return DispatchReceipt(sequence, attempted=True, sent=True)

    def wait_for_event(
        self,
        sequence: int,
        timeout_seconds: float,
        *,
        terminal: bool = True,
        expected_robot_session: int | None = None,
    ) -> SupervisorEvent | None:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                event = self._events.get(sequence)
                if (
                    event
                    and (expected_robot_session is None or event.robot_session == expected_robot_session)
                    and (not terminal or event.event_code in TERMINAL_EVENTS)
                ):
                    return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(min(remaining, 0.5))

    def wait_until_connected(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while not self._connection:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.25))
            return True

    def wait_for_robot_session_change(self, previous_session: int | None, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while not self._connection or self._robot_session == previous_session:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.25))
            return True

    def wait_for_connection_generation(self, previous_generation: int, timeout_seconds: float) -> bool:
        """Wait for a newly validated socket even when old controllers reuse a generated session ID."""
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while not self._connection or self._connection_generation <= previous_generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.25))
            return True

    def events_for(self, sequence: int) -> list[SupervisorEvent]:
        with self._lock:
            return list(self._event_history.get(sequence, ()))

    def status(self) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            connected = self._connection is not None
            return {
                "protocol": "binary-int32-v1",
                "listening": bool(self._listener_thread and self._listener_thread.is_alive() and self._listener),
                "listen_host": self._listen_host,
                "listen_port": self._listen_port,
                "connected": connected,
                "peer": self._peer,
                "connection_age_seconds": round(now - self._connected_at, 3) if connected and self._connected_at else None,
                "connection_generation": self._connection_generation,
                "heartbeat_age_seconds": round(now - self._last_seen_at, 3) if self._last_seen_at else None,
                "telemetry_age_seconds": round(now - self._telemetry_at, 3) if self._telemetry_at else None,
                "disconnect_age_seconds": round(now - self._last_disconnect_at, 3) if self._last_disconnect_at else None,
                "app_session": self._app_session,
                "robot_session": self._robot_session,
                "robot_last_sequence": self._robot_last_sequence,
                "robot_last_event": EVENT_NAMES.get(self._robot_last_event, self._robot_last_event),
                "latched": self._robot_latched,
                "last_disconnect_detail": self._last_disconnect_detail or None,
                "protocol_errors": self._protocol_errors,
                "protocol_resynchronizations": self._protocol_resynchronizations,
                "rejected_connections": self._rejected_connections,
                "retained_event_sequences": len(self._events),
                "telemetry": dict(self._telemetry),
            }


_SUPERVISOR = RobotSupervisorManager()


def robot_supervisor() -> RobotSupervisorManager:
    return _SUPERVISOR
