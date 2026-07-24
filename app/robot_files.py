from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import PurePosixPath
import socket
import stat
from threading import RLock
from typing import BinaryIO, Iterator

import paramiko


class RobotFileAccessError(Exception):
    pass


class RobotFileConflict(RobotFileAccessError):
    def __init__(self, destination: PurePosixPath):
        self.destination = str(destination)
        super().__init__(f"A file or folder named '{destination.name}' already exists.")


_SFTP_LOCKS_GUARD = RLock()
_SFTP_OPERATION_LOCKS: dict[tuple[str, int, str], RLock] = {}


def _sftp_operation_lock(host: str, port: int, username: str) -> RLock:
    key = (host, port, username)
    with _SFTP_LOCKS_GUARD:
        return _SFTP_OPERATION_LOCKS.setdefault(key, RLock())


def _root_path(directory: str) -> PurePosixPath:
    root = PurePosixPath(directory)
    if not root.is_absolute() or ".." in root.parts:
        raise RobotFileAccessError("Controller program directory must be an absolute path without '..'.")
    return root


def _safe_path(root: PurePosixPath, requested: str | None) -> PurePosixPath:
    path = root if not requested else PurePosixPath(requested)
    if not path.is_absolute():
        path = root / path
    if ".." in path.parts or path.parts[: len(root.parts)] != root.parts:
        raise RobotFileAccessError("That path is outside the configured controller program directory.")
    return path


@contextmanager
def robot_sftp_client(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout_seconds: float,
) -> Iterator[paramiko.SFTPClient]:
    if not username or not password:
        raise RobotFileAccessError("Enter an SFTP username and password in Settings first.")
    connection: socket.socket | None = None
    transport: paramiko.Transport | None = None
    sftp: paramiko.SFTPClient | None = None
    operation_lock = _sftp_operation_lock(host, port, username)
    operation_lock.acquire()
    try:
        connection = socket.create_connection((host, port), timeout=timeout_seconds)
        connection.settimeout(timeout_seconds)
        transport = paramiko.Transport(connection)
        transport.banner_timeout = timeout_seconds
        transport.auth_timeout = timeout_seconds
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        yield sftp
    except (OSError, paramiko.SSHException) as exc:
        raise RobotFileAccessError(f"Could not access controller files: {exc}") from exc
    finally:
        try:
            if sftp is not None:
                sftp.close()
            if transport is not None:
                transport.close()
            elif connection is not None:
                connection.close()
        finally:
            operation_lock.release()


def list_robot_program_files(
    host: str,
    port: int,
    username: str,
    password: str,
    directory: str,
    extensions: set[str] | None,
    timeout_seconds: float,
) -> list[str]:
    root = _root_path(directory)
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
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
    except RobotFileAccessError:
        raise


def list_robot_directory(
    host: str, port: int, username: str, password: str, directory: str, path: str | None, timeout_seconds: float,
    extensions: set[str] | None = None,
) -> dict:
    root = _root_path(directory)
    current = _safe_path(root, path)
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            entries = []
            for entry in sftp.listdir_attr(str(current)):
                entry_path = current / entry.filename
                kind = "directory" if stat.S_ISDIR(entry.st_mode) else "file"
                if kind == "file" and extensions is not None and entry_path.suffix.lower() not in extensions:
                    continue
                entries.append({
                    "name": entry.filename,
                    "path": str(entry_path),
                    "kind": kind,
                    "size": entry.st_size if kind == "file" else None,
                    "modified_at": datetime.fromtimestamp(entry.st_mtime, timezone.utc).isoformat(),
                })
            entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
            parent = str(current.parent) if current != root else None
            return {"root": str(root), "path": str(current), "parent": parent, "entries": entries}
    except RobotFileAccessError:
        raise


def read_robot_file(
    host: str, port: int, username: str, password: str, directory: str, path: str, timeout_seconds: float, limit: int = 1_000_000
) -> dict:
    root = _root_path(directory)
    target = _safe_path(root, path)
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            size = sftp.stat(str(target)).st_size
            if size > limit:
                return {"path": str(target), "too_large": True, "size": size, "text": ""}
            with sftp.open(str(target), "rb") as remote:
                data = remote.read()
            binary = b"\0" in data
            return {"path": str(target), "too_large": False, "size": size, "binary": binary, "text": data.decode("utf-8", errors="replace")}
    except RobotFileAccessError:
        raise


def read_robot_file_prefix(
    host: str,
    port: int,
    username: str,
    password: str,
    directory: str,
    path: str,
    timeout_seconds: float,
    limit: int = 64 * 1024,
) -> dict:
    """Read only the beginning of a regular controller file for cheap header inspection."""
    root = _root_path(directory)
    target = _safe_path(root, path)
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            attributes = sftp.stat(str(target))
            if not stat.S_ISREG(attributes.st_mode):
                raise RobotFileAccessError(f"Controller path is not a regular file: {target}")
            with sftp.open(str(target), "rb") as remote:
                data = remote.read(limit)
            return {
                "path": str(target),
                "size": int(attributes.st_size),
                "text": data.decode("utf-8", errors="replace"),
            }
    except RobotFileAccessError:
        raise


def download_robot_file(
    host: str, port: int, username: str, password: str, directory: str, path: str, timeout_seconds: float, limit: int = 100_000_000
) -> tuple[str, bytes]:
    root = _root_path(directory)
    target = _safe_path(root, path)
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            size = sftp.stat(str(target)).st_size
            if size > limit:
                raise RobotFileAccessError("The file is too large to download through the web interface.")
            with sftp.open(str(target), "rb") as remote:
                return target.name, remote.read()
    except RobotFileAccessError:
        raise


def upload_robot_file(
    host: str, port: int, username: str, password: str, directory: str, destination: str | None, filename: str, content: BinaryIO, timeout_seconds: float
) -> str:
    root = _root_path(directory)
    destination_path = _safe_path(root, destination)
    clean_name = PurePosixPath(filename).name
    if not clean_name or clean_name in {".", ".."}:
        raise RobotFileAccessError("Choose a valid file to upload.")
    target = _safe_path(root, str(destination_path / clean_name))
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            sftp.putfo(content, str(target), confirm=True)
            return str(target)
    except RobotFileAccessError:
        raise


def _path_exists(sftp: paramiko.SFTPClient, path: PurePosixPath) -> bool:
    try:
        sftp.stat(str(path))
        return True
    except OSError as error:
        if getattr(error, "errno", None) == 2:
            return False
        raise


def _renamed_destination(sftp: paramiko.SFTPClient, destination: PurePosixPath) -> PurePosixPath:
    suffix = destination.suffix
    stem = destination.name[:-len(suffix)] if suffix else destination.name
    for index in range(1, 1000):
        candidate = destination.parent / f"{stem} ({index}){suffix}"
        if not _path_exists(sftp, candidate):
            return candidate
    raise RobotFileAccessError("Could not find an available filename for the copied item.")


def _transfer_destination(
    sftp: paramiko.SFTPClient, source: PurePosixPath, destination: PurePosixPath, conflict_strategy: str
) -> PurePosixPath | None:
    if source == destination:
        return None
    if not _path_exists(sftp, destination):
        return destination
    if conflict_strategy == "skip":
        return None
    if conflict_strategy == "rename":
        return _renamed_destination(sftp, destination)
    if conflict_strategy == "overwrite":
        return destination
    raise RobotFileConflict(destination)


def _remove_existing(sftp: paramiko.SFTPClient, path: PurePosixPath) -> None:
    if stat.S_ISDIR(sftp.stat(str(path)).st_mode):
        sftp.rmdir(str(path))
    else:
        sftp.remove(str(path))


def copy_robot_file(
    host: str, port: int, username: str, password: str, directory: str, source: str, destination_directory: str,
    timeout_seconds: float, conflict_strategy: str = "prompt",
) -> str | None:
    root = _root_path(directory)
    source_path = _safe_path(root, source)
    destination = _safe_path(root, destination_directory) / source_path.name
    destination = _safe_path(root, str(destination))
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            destination = _transfer_destination(sftp, source_path, destination, conflict_strategy)
            if destination is None:
                return None
            with sftp.open(str(source_path), "rb") as source_file, sftp.open(str(destination), "wb") as destination_file:
                while chunk := source_file.read(65536):
                    destination_file.write(chunk)
            return str(destination)
    except RobotFileAccessError:
        raise


def remote_file_signature(
    host: str,
    port: int,
    username: str,
    password: str,
    directory: str,
    path: str,
    timeout_seconds: float,
) -> dict[str, int | str] | None:
    """Return enough metadata to prove that a controller file changed."""
    root = _root_path(directory)
    target = _safe_path(root, path)
    with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
        try:
            attributes = sftp.stat(str(target))
        except OSError as exc:
            if getattr(exc, "errno", None) == 2:
                return None
            raise
        if not stat.S_ISREG(attributes.st_mode):
            raise RobotFileAccessError(f"Controller path is not a regular file: {target}")
        digest = hashlib.sha256()
        with sftp.open(str(target), "rb") as source_file:
            while chunk := source_file.read(65536):
                digest.update(chunk)
        return {
            "size": int(attributes.st_size),
            "mtime": int(attributes.st_mtime),
            "sha256": digest.hexdigest(),
        }


def _ensure_remote_directory(
    sftp: paramiko.SFTPClient,
    root: PurePosixPath,
    directory: PurePosixPath,
) -> None:
    current = root
    for part in directory.parts[len(root.parts):]:
        current /= part
        try:
            attributes = sftp.stat(str(current))
        except OSError as exc:
            if getattr(exc, "errno", None) != 2:
                raise
            sftp.mkdir(str(current))
            continue
        if not stat.S_ISDIR(attributes.st_mode):
            raise RobotFileAccessError(f"Controller archive path is not a directory: {current}")


def copy_remote_file_as(
    host: str,
    port: int,
    username: str,
    password: str,
    directory: str,
    source: str,
    destination_directory: str,
    destination_name: str,
    timeout_seconds: float,
) -> str:
    """Copy a controller file to a new, explicitly named archive file."""
    root = _root_path(directory)
    source_path = _safe_path(root, source)
    destination_root = _safe_path(root, destination_directory)
    clean_name = PurePosixPath(destination_name).name
    if not clean_name or clean_name in {".", ".."} or clean_name != destination_name:
        raise RobotFileAccessError("Choose a valid archive filename without path separators.")
    destination = _safe_path(root, str(destination_root / clean_name))
    with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
        _ensure_remote_directory(sftp, root, destination_root)
        try:
            sftp.stat(str(destination))
        except OSError as exc:
            if getattr(exc, "errno", None) != 2:
                raise
        else:
            raise RobotFileConflict(destination)
        with sftp.open(str(source_path), "rb") as source_file, sftp.open(str(destination), "wb") as destination_file:
            while chunk := source_file.read(65536):
                destination_file.write(chunk)
    return str(destination)


def move_robot_file(
    host: str, port: int, username: str, password: str, directory: str, source: str, destination_directory: str,
    timeout_seconds: float, conflict_strategy: str = "prompt",
) -> str | None:
    root = _root_path(directory)
    source_path = _safe_path(root, source)
    destination = _safe_path(root, str(_safe_path(root, destination_directory) / source_path.name))
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            destination = _transfer_destination(sftp, source_path, destination, conflict_strategy)
            if destination is None:
                return None
            if conflict_strategy == "overwrite" and _path_exists(sftp, destination):
                _remove_existing(sftp, destination)
            sftp.rename(str(source_path), str(destination))
            return str(destination)
    except RobotFileAccessError:
        raise


def rename_robot_file(
    host: str, port: int, username: str, password: str, directory: str, path: str, name: str, timeout_seconds: float
) -> str:
    root = _root_path(directory)
    source = _safe_path(root, path)
    clean_name = PurePosixPath(name).name
    if not clean_name or clean_name in {".", ".."}:
        raise RobotFileAccessError("Enter a valid file or folder name.")
    destination = _safe_path(root, str(source.parent / clean_name))
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            sftp.rename(str(source), str(destination))
            return str(destination)
    except RobotFileAccessError:
        raise


def delete_robot_path(
    host: str, port: int, username: str, password: str, directory: str, path: str, timeout_seconds: float
) -> None:
    root = _root_path(directory)
    target = _safe_path(root, path)
    if target == root:
        raise RobotFileAccessError("The configured Robot program directory cannot be deleted.")
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            if stat.S_ISDIR(sftp.stat(str(target)).st_mode):
                sftp.rmdir(str(target))
            else:
                sftp.remove(str(target))
    except RobotFileAccessError:
        raise


def create_robot_directory(
    host: str, port: int, username: str, password: str, directory: str, parent: str, name: str, timeout_seconds: float
) -> str:
    root = _root_path(directory)
    clean_name = PurePosixPath(name).name
    if not clean_name or clean_name in {".", ".."}:
        raise RobotFileAccessError("Enter a valid folder name.")
    target = _safe_path(root, str(_safe_path(root, parent) / clean_name))
    try:
        with robot_sftp_client(host, port, username, password, timeout_seconds) as sftp:
            sftp.mkdir(str(target))
            return str(target)
    except RobotFileAccessError:
        raise
