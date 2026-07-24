import paramiko
import pytest

from app import cnc_linuxcnc


class _Stream:
    def __init__(self, content: bytes = b"") -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content


class _Transport:
    def __init__(self) -> None:
        self.active = True
        self.keepalive = None

    def is_active(self) -> bool:
        return self.active

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive = seconds


class _Client:
    def __init__(self) -> None:
        self.transport = _Transport()
        self.connect_calls = 0
        self.exec_calls = 0

    def set_missing_host_key_policy(self, policy) -> None:
        pass

    def connect(self, **kwargs) -> None:
        self.connect_calls += 1

    def get_transport(self) -> _Transport:
        return self.transport

    def exec_command(self, command, timeout, get_pty):
        self.exec_calls += 1
        output = b'MONGO_CNC_CYCLE={"interp_state": 1}\n'
        return _Stream(), _Stream(output), _Stream()

    def close(self) -> None:
        self.transport.active = False


def test_cnc_ssh_connection_is_reused(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(cnc_linuxcnc.paramiko, "SSHClient", lambda: client)
    cnc_linuxcnc.resume_cnc_connections()
    try:
        first = cnc_linuxcnc.read_linuxcnc_cycle_state("mill", 22, "operator", "secret", 5)
        second = cnc_linuxcnc.read_linuxcnc_cycle_state("mill", 22, "operator", "secret", 5)
    finally:
        cnc_linuxcnc.suspend_cnc_connections()
        cnc_linuxcnc.resume_cnc_connections()

    assert first["interp_state"] == 1
    assert second["interp_state"] == 1
    assert client.connect_calls == 1
    assert client.exec_calls == 2
    assert client.transport.keepalive == 15


def test_cnc_ssh_connection_failure_enters_cooldown(monkeypatch) -> None:
    attempts = 0

    class FailingClient(_Client):
        def connect(self, **kwargs) -> None:
            nonlocal attempts
            attempts += 1
            raise paramiko.SSHException("controller refused connection")

    monkeypatch.setattr(cnc_linuxcnc.paramiko, "SSHClient", FailingClient)
    cnc_linuxcnc.resume_cnc_connections()
    try:
        with pytest.raises(cnc_linuxcnc.CncTelemetryError, match="controller refused connection"):
            cnc_linuxcnc.read_linuxcnc_cycle_state("mill", 22, "operator", "secret", 5)
        with pytest.raises(cnc_linuxcnc.CncTelemetryError, match="cooling down"):
            cnc_linuxcnc.read_linuxcnc_cycle_state("mill", 22, "operator", "secret", 5)
    finally:
        cnc_linuxcnc.resume_cnc_connections()

    assert attempts == 1
