from __future__ import annotations

import argparse
from pathlib import Path

from app.backup import BackupError, BackupManager
from app.settings import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Mongo Production System portable backup utility")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--reason", default="manual")
    create.add_argument("--output", help="Write one portable .mps file directly to this path")
    extract = subparsers.add_parser("extract")
    extract.add_argument("--backup-ref", required=True)
    extract.add_argument("--output", required=True)
    import_backup = subparsers.add_parser("import")
    import_backup.add_argument("--backup-file", required=True)
    import_backup.add_argument("--reference")
    args = parser.parse_args()
    manager = BackupManager(settings.database_url)
    try:
        if args.command == "create":
            result = manager.export_file(Path(args.output), args.reason) if args.output else manager.run_now(args.reason)
            print(result)
        elif args.command == "extract":
            manifest = manager.extract_backup(args.backup_ref, Path(args.output).resolve())
            print({"status": "extracted", "manifest": manifest, "output": str(Path(args.output).resolve())})
        else:
            result = manager.import_file(Path(args.backup_file), args.reference)
            print(result)
        return 0
    except BackupError as exc:
        print(f"Backup operation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
