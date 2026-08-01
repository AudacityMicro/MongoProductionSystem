"""Passive listener for the PathPilot-originated mill supervisor.

The listener is deliberately inert until an operator explicitly bootstraps the
mill agent. It owns no SSH connection and never sends controller commands by
itself.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets
import socket
import struct
import threading
import time
from typing import Any


PROTOCOL_VERSION = 1
MAXIMUM_FRAME_BYTES = 256 * 1024


class MillSupervisorProtocolError(ValueError):
    pass


def encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > MAXIMUM_FRAME_BYTES:
        raise MillSupervisorProtocolError("Mill supervisor message exceeds the frame limit.")
    return struct.pack("!I", len(payload)) + payload


class MessageBuffer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self.buffer.extend(chunk)
        if len(self.buffer) > MAXIMUM_FRAME_BYTES * 2:
            self.buffer.clear()
            raise MillSupervisorProtocolError("Mill supervisor receive buffer exceeded its limit.")
        messages: list[dict[str, Any]] = []
        while len(self.buffer) >= 4:
            size = struct.unpack("!I", self.buffer[:4])[0]
            if size <= 1 or size > MAXIMUM_FRAME_BYTES:
                self.buffer.clear()
                raise MillSupervisorProtocolError("Mill supervisor frame has an invalid length.")
            if len(self.buffer) < size + 4:
                break
            raw = bytes(self.buffer[4 : size + 4])
            del self.buffer[: size + 4]
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise MillSupervisorProtocolError("Mill supervisor frame is not valid JSON.") from exc
            if not isinstance(message, dict) or message.get("protocol") != PROTOCOL_VERSION:
                raise MillSupervisorProtocolError("Mill supervisor protocol version is unsupported.")
            messages.append(message)
        return messages


@dataclass(slots=True)
class MillSupervisorEvent:
    sequence: int
    name: str
    detail: str = ""
    result: dict[str, Any] | None = None
    received_at: str = ""

    def __post_init__(self) -> None:
        if not self.received_at:
            self.received_at = datetime.now(timezone.utc).isoformat()


class MillSupervisorManager:
    """Own one mill-originated TCP session without coupling it to the database."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.send_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listener: socket.socket | None = None
        self.connection: socket.socket | None = None
        self.listener_thread: threading.Thread | None = None
        self.connection_threads: set[threading.Thread] = set()
        self.listen_host = "0.0.0.0"
        self.listen_port = 50011
        self.app_session = secrets.randbelow(2_000_000_000 - 1) + 1
        self.mill_session: int | None = None
        self.connected_at: float | None = None
        self.last_seen_at: float | None = None
        self.telemetry_at: float | None = None
        self.telemetry: dict[str, Any] = {}
        self.events: OrderedDict[int, list[MillSupervisorEvent]] = OrderedDict()
        self.last_sequence = 0
        self.last_result: dict[str, Any] = {}
        self.last_disconnect_detail = "Mill supervisor has not connected."
        self.connection_generation = 0

    def start(self, host: str, port: int) -> None:
        with self.lock:
            if self.listener_thread and self.listener_thread.is_alive() and (host, port) == (self.listen_host, self.listen_port):
                return
        self.stop()
        with self.lock:
            self.listen_host, self.listen_port = host, port
            self.stop_event.clear()
            self.listener_thread = threading.Thread(target=self._listen, daemon=True, name="mill-supervisor-listener")
            self.listener_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            sockets = [self.connection, self.listener]
            self.connection = None
            self.listener = None
        for item in sockets:
            if item:
                try:
                    item.close()
                except OSError:
                    pass

    def _listen(self) -> None:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.listen_host, self.listen_port))
            listener.listen(2)
            listener.settimeout(1.0)
            with self.lock:
                self.listener = listener
            while not self.stop_event.is_set():
                try:
                    connection, _peer = listener.accept()
                except socket.timeout:
                    continue
                worker = threading.Thread(target=self._serve, args=(connection,), daemon=True, name="mill-supervisor-connection")
                with self.lock:
                    self.connection_threads.add(worker)
                worker.start()
        except OSError as exc:
            with self.lock:
                self.last_disconnect_detail = "Mill supervisor listener failed: {}".format(exc)
                self.condition.notify_all()

    def _serve(self, connection: socket.socket) -> None:
        buffer = MessageBuffer()
        validated = False
        detail = "Mill supervisor closed the connection."
        try:
            connection.settimeout(2.0)
            deadline = time.monotonic() + 8.0
            while not self.stop_event.is_set():
                if not validated and time.monotonic() > deadline:
                    raise MillSupervisorProtocolError("Mill supervisor did not send HELLO within eight seconds.")
                try:
                    chunk = connection.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                for message in buffer.feed(chunk):
                    if not validated:
                        if message.get("kind") != "hello":
                            raise MillSupervisorProtocolError("First mill supervisor message was not HELLO.")
                        self._activate(connection, message)
                        validated = True
                    else:
                        self._handle(message)
        except (OSError, MillSupervisorProtocolError) as exc:
            detail = "Mill supervisor connection failed: {}".format(exc)
        finally:
            try:
                connection.close()
            except OSError:
                pass
            with self.lock:
                if self.connection is connection:
                    self.connection = None
                    self.connected_at = None
                    self.last_disconnect_detail = detail
                self.connection_threads.discard(threading.current_thread())
                self.condition.notify_all()

    def _activate(self, connection: socket.socket, message: dict[str, Any]) -> None:
        session = int(message.get("session", 0))
        if session <= 0:
            raise MillSupervisorProtocolError("Mill supervisor HELLO has no valid session.")
        with self.lock:
            old = self.connection
            self.connection = connection
            self.mill_session = session
            self.last_sequence = int(message.get("last_sequence", 0))
            self.last_result = dict(message.get("last_result") or {})
            result_sequence = int(self.last_result.get("sequence", 0) or 0)
            result_event = str(self.last_result.get("event", ""))
            if result_sequence > 0 and result_event in {"completed", "faulted", "latched"}:
                existing = self.events.get(result_sequence, [])
                if not any(item.name == result_event for item in existing):
                    existing.append(MillSupervisorEvent(
                        result_sequence,
                        result_event,
                        str(self.last_result.get("detail", "")),
                        dict(self.last_result.get("result") or {}),
                    ))
                    self.events[result_sequence] = existing
                    self.events.move_to_end(result_sequence)
                    while len(self.events) > 512:
                        self.events.popitem(last=False)
            self.connected_at = time.monotonic()
            self.last_seen_at = self.connected_at
            self.connection_generation += 1
            self.last_disconnect_detail = ""
            self.condition.notify_all()
        if old and old is not connection:
            try:
                old.close()
            except OSError:
                pass
        self._send(connection, {"protocol": PROTOCOL_VERSION, "kind": "hello_ack", "app_session": self.app_session})

    def _handle(self, message: dict[str, Any]) -> None:
        kind = str(message.get("kind", ""))
        with self.lock:
            self.last_seen_at = time.monotonic()
            if kind == "telemetry":
                self.telemetry = dict(message.get("snapshot") or {})
                self.telemetry_at = self.last_seen_at
            elif kind == "event":
                sequence = int(message.get("sequence", 0))
                if sequence <= 0:
                    raise MillSupervisorProtocolError("Mill supervisor event has no sequence.")
                event = MillSupervisorEvent(sequence, str(message.get("event", "")), str(message.get("detail", "")), message.get("result"))
                self.events.setdefault(sequence, []).append(event)
                self.events.move_to_end(sequence)
                while len(self.events) > 512:
                    self.events.popitem(last=False)
                self.last_sequence = max(self.last_sequence, sequence)
                if event.name in {"completed", "faulted", "latched"}:
                    self.last_result = {"sequence": sequence, "event": event.name, "detail": event.detail, "result": event.result or {}}
            elif kind != "heartbeat":
                raise MillSupervisorProtocolError("Unexpected mill supervisor message kind {}.".format(kind))
            self.condition.notify_all()

    def _send(self, connection: socket.socket, message: dict[str, Any]) -> None:
        with self.send_lock:
            connection.sendall(encode_message(message))

    def dispatch(self, sequence: int, operation: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        with self.lock:
            connection = self.connection
            session = self.mill_session
        if not connection or not session:
            return False, "Mill supervisor is not connected."
        try:
            self._send(connection, {"protocol": PROTOCOL_VERSION, "kind": "command", "sequence": sequence, "operation": operation, "arguments": arguments})
        except OSError as exc:
            return False, "Mill supervisor command transmission is uncertain: {}".format(exc)
        return True, ""

    def wait_for_event(self, sequence: int, names: set[str], timeout_seconds: float) -> MillSupervisorEvent | None:
        deadline = time.monotonic() + timeout_seconds
        with self.condition:
            while True:
                for event in reversed(self.events.get(sequence, [])):
                    if event.name in names:
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)

    def events_for(self, sequence: int) -> list[MillSupervisorEvent]:
        with self.lock:
            return list(self.events.get(sequence, []))

    def status(self) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            return {
                "listening": self.listener is not None,
                "listen_host": self.listen_host,
                "listen_port": self.listen_port,
                "connected": self.connection is not None,
                "connection_generation": self.connection_generation,
                "connection_age_seconds": round(now - self.connected_at, 3) if self.connected_at else None,
                "heartbeat_age_seconds": round(now - self.last_seen_at, 3) if self.last_seen_at else None,
                "telemetry_age_seconds": round(now - self.telemetry_at, 3) if self.telemetry_at else None,
                "app_session": self.app_session,
                "mill_session": self.mill_session,
                "mill_last_sequence": self.last_sequence,
                "last_result": dict(self.last_result),
                "last_disconnect_detail": self.last_disconnect_detail,
                "telemetry": dict(self.telemetry),
            }


_SUPERVISOR = MillSupervisorManager()


def mill_supervisor() -> MillSupervisorManager:
    return _SUPERVISOR
