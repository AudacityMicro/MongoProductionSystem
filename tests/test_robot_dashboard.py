from app.robot_dashboard import run_robot_program


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
