from __future__ import annotations

from pathlib import Path

from app import service
from app.mill_supervisor import MillSupervisorEvent, MessageBuffer, MillSupervisorManager, encode_message
from app.models import MillSupervisorCommand
from app.schemas import MillSupervisorReconcile


def test_mill_supervisor_message_buffer_reassembles_fragmented_json() -> None:
    frame = encode_message({"protocol": 1, "kind": "hello", "session": 41})
    buffer = MessageBuffer()
    assert buffer.feed(frame[:5]) == []
    assert buffer.feed(frame[5:]) == [{"protocol": 1, "kind": "hello", "session": 41}]


def test_mill_supervisor_rejects_wrong_protocol() -> None:
    buffer = MessageBuffer()
    frame = encode_message({"protocol": 1, "kind": "heartbeat"})
    frame = frame[:4] + frame[4:].replace(b'"protocol":1', b'"protocol":2')
    try:
        buffer.feed(frame)
    except ValueError as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("Expected wrong protocol to be rejected")


def test_mill_supervisor_status_is_inert_until_explicit_start() -> None:
    manager = MillSupervisorManager()
    status = manager.status()
    assert not status["listening"]
    assert not status["connected"]
    assert status["listen_port"] == 50011


def test_reconnect_rehydrates_persisted_terminal_result() -> None:
    class Connection:
        def sendall(self, _payload):
            pass

        def close(self):
            pass

    manager = MillSupervisorManager()
    manager._activate(Connection(), {
        "session": 77,
        "last_sequence": 9,
        "last_result": {
            "sequence": 9,
            "event": "completed",
            "detail": "",
            "result": {"program": "/home/operator/gcode/Gcode/test.nc"},
        },
    })

    event = manager.wait_for_event(9, {"completed"}, 0.01)
    assert event is not None
    assert event.result["program"].endswith("test.nc")


def test_staged_agent_contains_a_hard_execution_gate() -> None:
    source = (Path(__file__).parents[1] / "app" / "mill_supervisor_agent.py").read_text(encoding="utf-8")
    assert 'config.get("execution_enabled", False)' in source
    assert '"event": "latched", "detail": "Mill supervisor command execution is not activated."' in source
    assert "reconnect_index = min(reconnect_index + 1" in source
    assert "def completed_status(" in source
    assert 'config["status_path"]' in source
    assert 'state["active"] = active' in source
    assert '"Stale command sequence was rejected."' in source
    assert 'operation == "probe"' in source
    assert 'active.get("require_completion_status", False)' in source
    assert '"optional_stop": bool(getattr(status, "optional_stop", False))' in source


def test_staged_agent_loads_the_selected_program_without_a_temporary_copy() -> None:
    source = (Path(__file__).parents[1] / "app" / "mill_supervisor_agent.py").read_text(encoding="utf-8")

    assert "shutil.copy2" not in source
    assert "mps-mill-supervisor-gcode.file" not in source
    assert "command.program_open(target)" in source
    assert "command.program_open(target, os.path.dirname(target))" not in source
    assert 'os.path.realpath(getattr(status, "file", "") or "") == target' in source


def test_agent_launcher_uses_pathpilot_linuxcnc_python_runtime() -> None:
    source = (Path(__file__).parents[1] / "app" / "cnc_linuxcnc.py").read_text(encoding="utf-8")
    assert "_PATHPILOT_LINUXCNC_ENVIRONMENT" in source
    assert "nohup bash -lc {agent}" in source
    assert "def test_mill_supervisor_runtime(" in source


def test_mill_supervisor_api_reports_inactive_rollout_by_default(client) -> None:
    response = client.get("/api/debug/cnc/supervisor")

    assert response.status_code == 200
    status = response.json()
    assert status["staged"] is True
    assert status["enabled"] is False
    assert status["activation_verified"] is False
    assert status["reconciliation_required"] is False


class CompletedMillSupervisor:
    def __init__(self, *, sent=True):
        self.sequence = 0
        self.sent = sent
        self.arguments = None

    def status(self):
        return {
            "connected": True,
            "heartbeat_age_seconds": 0.01,
            "mill_session": 77,
            "app_session": 88,
            "mill_last_sequence": self.sequence,
            "telemetry": {"interp_state": 1, "estop": False, "enabled": True},
        }

    def dispatch(self, sequence, _operation, arguments):
        self.sequence = sequence
        self.arguments = arguments
        return self.sent, "wire failed after send attempt" if not self.sent else ""

    def wait_for_event(self, sequence, _names, _timeout):
        return MillSupervisorEvent(sequence, "completed", result={"program": "job.nc"})

    def events_for(self, sequence):
        return [
            MillSupervisorEvent(sequence, "accepted"),
            MillSupervisorEvent(sequence, "running"),
            MillSupervisorEvent(sequence, "completed", result={"program": "job.nc"}),
        ]


def test_enabled_mill_supervisor_cycle_uses_durable_ledger(client, monkeypatch) -> None:
    fake = CompletedMillSupervisor()
    monkeypatch.setattr(service, "mill_supervisor", lambda: fake)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        session.commit()

        assert service._run_cnc_cycle(
            settings,
            "/home/operator/gcode/Gcode/job.nc",
            session=session,
            cycle_label="Test cycle",
            timeout_seconds=10,
        ) is True

        command = session.query(MillSupervisorCommand).one()
        assert command.status == "completed"
        assert command.accepted_at is not None
        assert command.started_at is not None
        assert command.attempted is True
        assert fake.arguments["require_completion_status"] is False


def test_uncertain_mill_supervisor_send_never_falls_back_to_ssh(client, monkeypatch) -> None:
    fake = CompletedMillSupervisor(sent=False)
    monkeypatch.setattr(service, "mill_supervisor", lambda: fake)
    monkeypatch.setattr(
        service,
        "run_linuxcnc_program",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("SSH fallback must not run")),
    )
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        session.commit()

        try:
            service._run_cnc_cycle(
                settings,
                "/home/operator/gcode/Gcode/job.nc",
                session=session,
                cycle_label="Test cycle",
                timeout_seconds=10,
            )
        except Exception as exc:
            assert "uncertain" in str(exc).lower()
        else:
            raise AssertionError("Expected uncertain supervisor dispatch to stop the cycle")

        assert session.query(MillSupervisorCommand).one().status == "uncertain"


def test_no_motion_probe_uses_real_sequence_ledger(client, monkeypatch) -> None:
    fake = CompletedMillSupervisor()
    monkeypatch.setattr(service, "mill_supervisor", lambda: fake)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        session.commit()

        result = service.test_mill_supervisor_command_path(session)

        assert result["sequence"] == 1
        command = session.query(MillSupervisorCommand).one()
        assert command.operation == "probe"
        assert command.status == "completed"


def test_mill_command_reconciliation_never_dispatches(client, monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "_dispatch_mill_supervisor_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reconciliation must not dispatch")),
    )
    with client.app.state.session_factory() as session:
        command = service._new_mill_supervisor_command(session, "run_program", {"program": "job.nc"})
        command.status = "latched"
        session.commit()
        settings = service.get_settings(session)

        service.reconcile_mill_supervisor_command(
            session,
            MillSupervisorReconcile(
                expected_revision=settings.revision,
                sequence=command.sequence,
                resolution="accept_completed",
            ),
        )

        assert command.status == "operator_completed"
