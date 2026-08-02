from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile

from app.backup import BackupConfig, BackupManager, PORTABLE_PREFIX


def test_portable_backup_round_trip_and_import():
    root = Path(tempfile.mkdtemp(prefix="mps-backup-test-"))
    database_path = root / "source.db"
    backup_dir = root / "backups"
    export_path = root / "transfer" / "mps-backup.mps"
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE entered_data (id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO entered_data(value) VALUES (?)", ("operator data",))
            connection.commit()

        config = BackupConfig(enabled=True, directory=str(backup_dir), interval_seconds=900, debounce_seconds=5)
        manager = BackupManager(f"sqlite:///{database_path.as_posix()}", config)

        exported = manager.export_file(export_path, "cross-computer")
        assert exported["status"] == "exported"
        assert export_path.read_bytes().startswith(PORTABLE_PREFIX)

        imported = manager.import_file(export_path)
        assert imported["status"] == "imported"
        backups = manager.list_backups()
        assert len(backups) == 1

        restored_path = root / "restored.db"
        manifest = manager.extract_backup(imported["reference"], restored_path)
        assert manifest["encrypted"] is False
        with sqlite3.connect(restored_path) as connection:
            assert connection.execute("SELECT value FROM entered_data").fetchone() == ("operator data",)
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_backup_status_is_safe_when_not_configured(client):
    status = client.get("/api/system/backups")
    assert status.status_code == 200
    assert isinstance(status.json()["configured"], bool)
    assert isinstance(status.json()["backups"], list)
