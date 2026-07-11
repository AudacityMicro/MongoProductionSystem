from __future__ import annotations

from pathlib import PurePosixPath
import stat

import paramiko


class RobotFileAccessError(Exception):
    pass


def list_robot_program_files(
    host: str,
    port: int,
    username: str,
    password: str,
    directory: str,
    extensions: set[str] | None,
    timeout_seconds: float,
) -> list[str]:
    root = PurePosixPath(directory)
    if not root.is_absolute() or ".." in root.parts:
        raise RobotFileAccessError("Robot program directory must be an absolute path without '..'.")
    if not username or not password:
        raise RobotFileAccessError("Enter an SFTP username and password in Settings first.")

    transport: paramiko.Transport | None = None
    sftp: paramiko.SFTPClient | None = None
    try:
        transport = paramiko.Transport((host, port))
        transport.banner_timeout = timeout_seconds
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        files: list[str] = []

        def scan(path: PurePosixPath, depth: int = 0) -> None:
            if depth > 6 or len(files) >= 1000:
                return
            for entry in sftp.listdir_attr(str(path)):
                entry_path = path / entry.filename
                if stat.S_ISDIR(entry.st_mode):
                    scan(entry_path, depth + 1)
                elif stat.S_ISREG(entry.st_mode) and (
                    extensions is None or entry_path.suffix.lower() in extensions
                ):
                    files.append(str(entry_path))

        scan(root)
        return sorted(files, key=str.casefold)
    except (OSError, paramiko.SSHException) as exc:
        raise RobotFileAccessError(f"Could not list robot files: {exc}") from exc
    finally:
        if sftp is not None:
            sftp.close()
        if transport is not None:
            transport.close()
