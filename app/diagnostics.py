from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4


_SENSITIVE_FRAGMENTS = ("password", "secret", "token", "credential")


def _sanitize(value: Any, key: str = "") -> Any:
    if any(fragment in key.casefold() for fragment in _SENSITIVE_FRAGMENTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DiagnosticRecorder:
    """Keep a bounded in-memory timeline and a small rotating JSONL history."""

    def __init__(
        self,
        path: Path,
        *,
        memory_limit: int = 1000,
        file_limit_bytes: int = 2_000_000,
        backup_count: int = 3,
    ) -> None:
        self.path = path
        self.memory_limit = memory_limit
        self.file_limit_bytes = file_limit_bytes
        self.backup_count = backup_count
        self._events: deque[dict[str, Any]] = deque(maxlen=memory_limit)
        self._lock = threading.RLock()
        self._load_existing()

    def _load_existing(self) -> None:
        try:
            if not self.path.is_file():
                return
            for line in self.path.read_text(encoding="utf-8").splitlines()[-self.memory_limit :]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    self._events.append(item)
        except OSError:
            pass

    def record(
        self,
        component: str,
        event: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "component": component,
            "event": event,
            "message": message,
            "correlation_id": correlation_id,
            "details": _sanitize(details or {}),
        }
        with self._lock:
            self._events.append(item)
            try:
                self._append(item)
            except OSError:
                # Diagnostics must never interrupt machine control.
                pass
        return dict(item)

    def recent(self, limit: int = 200, *, severity: str | None = None) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), self.memory_limit))
        with self._lock:
            items = list(self._events)
        if severity:
            items = [item for item in items if item["severity"] == severity]
        return [dict(item) for item in items[-bounded:]]

    def _append(self, item: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size >= self.file_limit_bytes:
            self._rotate()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, separators=(",", ":"), ensure_ascii=True) + "\n")

    def _rotate(self) -> None:
        oldest = self.path.with_suffix(f"{self.path.suffix}.{self.backup_count}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_suffix(f"{self.path.suffix}.{index}")
            if source.exists():
                source.replace(self.path.with_suffix(f"{self.path.suffix}.{index + 1}"))
        if self.path.exists():
            self.path.replace(self.path.with_suffix(f"{self.path.suffix}.1"))


_RECORDER = DiagnosticRecorder(Path(__file__).parents[1] / "data" / "diagnostics.jsonl")


def diagnostics() -> DiagnosticRecorder:
    return _RECORDER
