from __future__ import annotations

from threading import RLock


_ROBOT_COMMAND_LOCKS_GUARD = RLock()
_ROBOT_COMMAND_LOCKS: dict[str, RLock] = {}


def robot_command_lock(host: str) -> RLock:
    """Return the shared command-channel lock for one robot controller."""
    normalized_host = host.strip().casefold()
    with _ROBOT_COMMAND_LOCKS_GUARD:
        return _ROBOT_COMMAND_LOCKS.setdefault(normalized_host, RLock())
