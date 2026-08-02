from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_PREFIX = "mps-backups/"
BACKUP_SUFFIX = ".mps"
BACKUP_REF_RE = re.compile(r"^(?:mps-backups/[0-9]{4}/[0-9]{2}/[0-9]{2}/[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+)\.mps$")
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
PORTABLE_PREFIX = b"MPS-PORTABLE-BACKUP-V1\n"


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool
    directory: str
    interval_seconds: int
    debounce_seconds: int

    @classmethod
    def from_environment(cls) -> "BackupConfig":
        return cls(
            enabled=os.getenv("MPS_BACKUP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            directory=os.getenv("MPS_BACKUP_DIRECTORY", "runtime/backups").strip() or "runtime/backups",
            interval_seconds=max(60, int(os.getenv("MPS_BACKUP_INTERVAL_SECONDS", "900"))),
            debounce_seconds=max(5, int(os.getenv("MPS_BACKUP_DEBOUNCE_SECONDS", "15"))),
        )

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.directory)

    @property
    def resolved_directory(self) -> Path:
        path = Path(self.directory)
        return path if path.is_absolute() else PROJECT_ROOT / path


def _database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise BackupError("Portable backups currently support only the SQLite database.")
    raw = database_url[len("sqlite:///") :]
    path = Path(raw)
    if not path.is_absolute() and len(raw) >= 3 and raw[1:3] == ":/":
        path = Path(raw)
    return path.resolve()


def _safe_ref(value: str) -> str:
    ref = value.strip().replace("\\", "/")
    if not BACKUP_REF_RE.fullmatch(ref):
        raise BackupError("Invalid backup reference.")
    return ref


def _make_snapshot(database_url: str) -> bytes:
    source_path = _database_path(database_url)
    if not source_path.is_file():
        raise BackupError(f"Database file was not found: {source_path}")
    created_at = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="mps-backup-") as temp_dir:
        temp = Path(temp_dir)
        snapshot_path = temp / "database.sqlite3"
        try:
            source = sqlite3.connect(str(source_path), timeout=30)
            destination = sqlite3.connect(str(snapshot_path), timeout=30)
            try:
                source.execute("PRAGMA busy_timeout=30000")
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source.close()
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite backup failed: {exc}") from exc

        _validate_database(snapshot_path, "SQLite snapshot")
        manifest = {
            "format": 2,
            "created_at": created_at,
            "database": "sqlite",
            "encrypted": False,
            "transport": "portable-file",
        }
        archive_path = temp / "snapshot.zip"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode())
            archive.write(snapshot_path, "database.sqlite3")
        payload = PORTABLE_PREFIX + archive_path.read_bytes()
        if len(payload) > MAX_BACKUP_BYTES:
            raise BackupError("Portable backup exceeds the 2 GB size limit.")
        return payload


def _validate_database(path: Path, label: str) -> None:
    try:
        validation = sqlite3.connect(str(path))
        try:
            check = validation.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise BackupError(f"{label} failed integrity check: {check}")
        finally:
            validation.close()
    except sqlite3.Error as exc:
        raise BackupError(f"{label} is not a valid SQLite database: {exc}") from exc


def _extract_snapshot(payload: bytes, output_path: Path) -> dict:
    if not payload.startswith(PORTABLE_PREFIX):
        raise BackupError("Backup format is not recognized. Expected an unencrypted .mps portable backup.")
    archive_data = payload[len(PORTABLE_PREFIX) :]
    try:
        with ZipFile(BytesIO(archive_data)) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names or "database.sqlite3" not in names:
                raise BackupError("Backup is missing its manifest or database snapshot.")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise BackupError("Backup manifest is invalid.")
            if manifest.get("encrypted") is not False:
                raise BackupError("Only unencrypted portable backups are accepted.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="mps-validated-", suffix=".db", delete=False) as staged:
                staged_path = Path(staged.name)
            try:
                with archive.open("database.sqlite3") as source, staged_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                _validate_database(staged_path, "Restored snapshot")
                staged_path.replace(output_path)
            finally:
                staged_path.unlink(missing_ok=True)
            return manifest
    except (BadZipFile, OSError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError(f"Portable backup could not be read: {exc}") from exc


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BackupManager:
    def __init__(self, database_url: str, config: BackupConfig | None = None) -> None:
        self.database_url = database_url
        self.config = config or BackupConfig.from_environment()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_reason = ""
        self._pending_deadline = 0.0
        self._last_attempt: str | None = None
        self._last_success: str | None = None
        self._last_error: str | None = None
        self._latest_ref: str | None = None

    def start(self) -> None:
        if not self.config.configured or self._thread:
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name="portable-backup-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def _worker(self) -> None:
        next_periodic = time.monotonic() + self.config.interval_seconds
        while not self._stop.wait(1):
            now = time.monotonic()
            reason = None
            with self._lock:
                if self._pending_reason and now >= self._pending_deadline:
                    reason = self._pending_reason
                    self._pending_reason = ""
                elif now >= next_periodic:
                    reason = "scheduled"
                    next_periodic = now + self.config.interval_seconds
            if reason:
                try:
                    self.run_now(reason)
                except Exception:
                    pass

    def request_backup(self, reason: str = "mutation") -> dict[str, object]:
        if not self.config.configured:
            return {"status": "not_configured", **self.status()}
        with self._lock:
            self._pending_reason = reason
            self._pending_deadline = time.monotonic() + self.config.debounce_seconds
        self._wake.set()
        return {"status": "scheduled", **self.status()}

    def run_now(self, reason: str = "manual") -> dict[str, object]:
        if not self.config.configured:
            raise BackupError("Configure MPS_BACKUP_ENABLED and MPS_BACKUP_DIRECTORY first.")
        started = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._last_attempt = started
            self._last_error = None
        try:
            payload = _make_snapshot(self.database_url)
            reference = self._new_reference()
            target = self.config.resolved_directory / reference
            _write_atomic(target, payload)
            with self._lock:
                self._last_success = datetime.now(timezone.utc).isoformat()
                self._latest_ref = reference
            return {"status": "saved", "reference": reference, "path": str(target), **self.status()}
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            raise

    def export_file(self, output_path: Path, reason: str = "export") -> dict[str, object]:
        payload = _make_snapshot(self.database_url)
        output_path = output_path.resolve()
        if output_path.suffix.lower() != BACKUP_SUFFIX:
            raise BackupError("Portable backup files must use the .mps extension.")
        _write_atomic(output_path, payload)
        return {"status": "exported", "path": str(output_path), "reason": reason}

    def import_file(self, source_path: Path, reference: str | None = None) -> dict[str, object]:
        if not self.config.configured:
            raise BackupError("Configure MPS_BACKUP_ENABLED and MPS_BACKUP_DIRECTORY first.")
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise BackupError(f"Backup file was not found: {source_path}")
        payload = source_path.read_bytes()
        with tempfile.NamedTemporaryFile(prefix="mps-import-", suffix=".db", delete=False) as staged:
            staged_path = Path(staged.name)
        try:
            manifest = _extract_snapshot(payload, staged_path)
        finally:
            staged_path.unlink(missing_ok=True)
        target_ref = _safe_ref(reference) if reference else self._new_reference(prefix=f"{source_path.stem}-import")
        target = self.config.resolved_directory / target_ref
        _write_atomic(target, payload)
        return {"status": "imported", "reference": target_ref, "path": str(target), "manifest": manifest}

    def _new_reference(self, prefix: str = "") -> str:
        timestamp = datetime.now(timezone.utc)
        name = f"{prefix + '-' if prefix else ''}{timestamp:%Y%m%dT%H%M%SZ}-{uuid4().hex[:10]}{BACKUP_SUFFIX}"
        return f"{BACKUP_PREFIX}{timestamp:%Y/%m/%d}/{name}"

    def _path_for_reference(self, reference: str) -> Path:
        safe = _safe_ref(reference)
        root = self.config.resolved_directory.resolve()
        candidate = (root / safe).resolve()
        if candidate != root and root not in candidate.parents:
            raise BackupError("Backup reference points outside the backup directory.")
        return candidate

    def list_backups(self) -> list[dict[str, object]]:
        if not self.config.configured:
            return []
        root = self.config.resolved_directory
        if not root.is_dir():
            return []
        backups = []
        for path in root.rglob(f"*{BACKUP_SUFFIX}"):
            if not path.is_file():
                continue
            reference = path.relative_to(root).as_posix()
            if BACKUP_REF_RE.fullmatch(reference):
                backups.append({"reference": reference, "size_bytes": path.stat().st_size})
        return sorted(backups, key=lambda item: str(item["reference"]), reverse=True)

    def extract_backup(self, reference: str, output_path: Path) -> dict:
        if not self.config.configured:
            raise BackupError("Configure portable backups before recovery.")
        payload_path = self._path_for_reference(reference)
        if not payload_path.is_file():
            raise BackupError("The requested portable backup was not found in the backup directory.")
        return _extract_snapshot(payload_path.read_bytes(), output_path)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "configured": self.config.configured,
                "directory": str(self.config.resolved_directory),
                "interval_seconds": self.config.interval_seconds,
                "last_attempt": self._last_attempt,
                "last_success": self._last_success,
                "last_error": self._last_error,
                "latest_reference": self._latest_ref,
            }
