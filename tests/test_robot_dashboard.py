import pytest

from app import robot_dashboard
from app.robot_dashboard import RobotDashboardError, clear_robot_fault, run_robot_program


class FakeDashboardConnection:
    def __init__(self) -> None:
        self.responses = [
            b"Connected: Universal Robots Dashboard Server\n",
            b"Loading program: /programs/job.urp\n",
            b"Loaded program: /programs/job.urp\n",
            b"Starting program\n",
        ]
        self.commands: list[str] = []

    def settimeout(self, _: float) -> None:
        pass

    def recv(self, _: int) -> bytes:
        return self.responses.pop(0)

    def sendall(self, value: bytes) -> None:
        self.commands.append(value.decode("utf-8").strip())

    def close(self) -> None:
        pass


def test_run_waits_for_requested_program_before_play(monkeypatch) -> None:
    connection = FakeDashboardConnection()
    monkeypatch.setattr("app.robot_dashboard.socket.create_connection", lambda *args, **kwargs: connection)
    monkeypatch.setattr("app.robot_dashboard.time.sleep", lambda _: None)

    response = run_robot_program("192.168.0.10", "/programs/job.urp", 1.0)

    assert response == "Starting program"
    assert connection.commands == [
        "load /programs/job.urp",
        "get loaded program",
        "play",
    ]


def test_dashboard_reads_responses_split_across_tcp_packets(monkeypatch) -> None:
    connection = FakeDashboardConnection()
    connection.responses = [
        b"Connected: Universal ",
        b"Robots Dashboard Server\n",
        b"Loading program: /programs/",
        b"job.urp\n",
        b"Loaded program: /programs/job.urp\n",
        b"Starting ",
        b"program\n",
    ]
    monkeypatch.setattr("app.robot_dashboard.socket.create_connection", lambda *args, **kwargs: connection)
    monkeypatch.setattr("app.robot_dashboard.time.sleep", lambda _: None)

    assert run_robot_program("192.0.2.55", "/programs/job.urp", 1.0) == "Starting program"


def test_dashboard_health_probe_is_cached(monkeypatch) -> None:
    host = "192.0.2.56"
    calls: list[str] = []
    with robot_dashboard._DASHBOARD_HEALTH_LOCK:
        robot_dashboard._DASHBOARD_HEALTH_CACHE.pop(host, None)
    monkeypatch.setattr(
        robot_dashboard,
        "_dashboard_command",
        lambda requested_host, command, _timeout: calls.append(f"{requested_host}:{command}") or "Robotmode: IDLE",
    )

    first = robot_dashboard.robot_dashboard_health(host, 1.0)
    second = robot_dashboard.robot_dashboard_health(host, 1.0)

    assert first["reachable"] is True
    assert second == first
    assert calls == [f"{host}:robotmode"]


@pytest.mark.parametrize(
    ("responses", "expected_action", "expected_commands"),
    [
        (
            [
                b"Connected: Universal Robots Dashboard Server\n",
                b"Safetymode: PROTECTIVE_STOP\n",
                b"closing safety popup\n",
                b"Protective stop releasing\n",
            ],
            "protective_stop_unlocked",
            ["safetymode", "close safety popup", "unlock protective stop"],
        ),
        (
            [
                b"Connected: Universal Robots Dashboard Server\n",
                b"Safetymode: FAULT\n",
                b"closing safety popup\n",
                b"Restarting safety\n",
            ],
            "safety_restarted",
            ["safetymode", "close safety popup", "restart safety"],
        ),
    ],
)
def test_clear_robot_fault_uses_state_specific_recovery(
    monkeypatch,
    responses,
    expected_action,
    expected_commands,
) -> None:
    connection = FakeDashboardConnection()
    connection.responses = responses
    monkeypatch.setattr("app.robot_dashboard.socket.create_connection", lambda *args, **kwargs: connection)

    result = clear_robot_fault("192.168.0.10", 1.0)

    assert result["action"] == expected_action
    assert connection.commands == expected_commands


def test_clear_robot_fault_does_not_bypass_emergency_stop(monkeypatch) -> None:
    connection = FakeDashboardConnection()
    connection.responses = [
        b"Connected: Universal Robots Dashboard Server\n",
        b"Safetymode: ROBOT_EMERGENCY_STOP\n",
    ]
    monkeypatch.setattr("app.robot_dashboard.socket.create_connection", lambda *args, **kwargs: connection)

    with pytest.raises(RobotDashboardError, match="physical safety condition"):
        clear_robot_fault("192.168.0.10", 1.0)

    assert connection.commands == ["safetymode"]
