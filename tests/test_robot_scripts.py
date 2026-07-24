import socket

import pytest

from app import robot_scripts
from app.robot_scripts import RobotScriptTransferUncertain


class FakeRobotCommandConnection:
    def __init__(self, responses: list[bytes], *, shutdown_error: OSError | None = None):
        self.responses = iter(responses)
        self.shutdown_error = shutdown_error
        self.sent: list[bytes] = []
        self.shutdown_modes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout: float) -> None:
        return None

    def recv(self, _size: int) -> bytes:
        return next(self.responses, b"")

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def shutdown(self, mode: int) -> None:
        self.shutdown_modes.append(mode)
        if self.shutdown_error:
            raise self.shutdown_error


def test_robot_script_sends_complete_payload_and_orderly_write_shutdown(monkeypatch) -> None:
    connection = FakeRobotCommandConnection([])
    monkeypatch.setattr(robot_scripts.socket, "create_connection", lambda *_args, **_kwargs: connection)

    robot_scripts.run_robot_script("mongo", "def test():\nend\n", 1.0)

    assert connection.sent == [b"def test():\nend\n"]
    assert connection.shutdown_modes == [socket.SHUT_WR]


def test_robot_script_does_not_require_unsolicited_command_channel_data(monkeypatch) -> None:
    connection = FakeRobotCommandConnection([b""])
    monkeypatch.setattr(robot_scripts.socket, "create_connection", lambda *_args, **_kwargs: connection)

    robot_scripts.run_robot_script("mongo", "def test():\nend\n", 1.0)

    assert connection.sent == [b"def test():\nend\n"]
    assert connection.shutdown_modes == [socket.SHUT_WR]


def test_robot_script_marks_post_send_socket_failure_uncertain(monkeypatch) -> None:
    connection = FakeRobotCommandConnection([b"x" * 79, b"y" * 79], shutdown_error=OSError("reset"))
    monkeypatch.setattr(robot_scripts.socket, "create_connection", lambda *_args, **_kwargs: connection)

    with pytest.raises(RobotScriptTransferUncertain, match="not retried"):
        robot_scripts.run_robot_script("mongo", "def test():\nend\n", 1.0)

    assert len(connection.sent) == 1
