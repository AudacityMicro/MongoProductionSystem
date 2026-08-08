import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app import service
from app.models import MillSupervisorCommand, Pallet, RecoverySession, RobotMotion, RobotSupervisorCommand


def _start(client: TestClient) -> dict:
    response = client.post("/api/recovery/start")
    assert response.status_code == 200
    return response.json()


def test_recovery_wizard_persists_happy_path_until_final_approval(client: TestClient) -> None:
    initial = client.get("/api/recovery/status")
    assert initial.status_code == 200
    assert initial.json()["active"] is False

    started = _start(client)
    session = started["session"]
    assert started["active"] is True
    assert session["status"] == "awaiting_safety"
    assert session["step"] == "safety"

    missing_confirmation = client.post(
        "/api/recovery/answer",
        json={"session_id": session["id"], "answers": {"cell_clear": False}},
    )
    assert missing_confirmation.status_code == 422

    ready = client.post(
        "/api/recovery/answer",
        json={"session_id": session["id"], "answers": {"cell_clear": True}},
    )
    assert ready.status_code == 200
    assert ready.json()["session"]["status"] == "ready"
    assert ready.json()["session"]["step"] == "final_approval"

    completed = client.post(
        "/api/recovery/answer",
        json={"session_id": session["id"], "answers": {"final_approval": True}},
    )
    assert completed.status_code == 200
    assert completed.json()["active"] is False
    assert completed.json()["session"]["status"] == "completed"
    assert "separate operator-controlled production start" in completed.json()["session"]["message"]

    persisted = client.get("/api/recovery/status").json()
    assert persisted["active"] is False
    assert persisted["session"] is None


def test_recovery_start_is_idempotent_while_a_session_is_active(client: TestClient) -> None:
    first = _start(client)
    second = _start(client)

    assert second["session"]["id"] == first["session"]["id"]
    assert second["session"]["status"] == "awaiting_safety"


def test_recovery_hands_off_when_robot_motion_state_is_uncertain(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            RobotMotion(
                id=str(uuid4()),
                pallet_id=str(uuid4()),
                operation="pick",
                source_slot=1,
                destination_slot=None,
                program_path="/programs/pick_1.urp",
                status="faulted",
                created_at=datetime.now(timezone.utc).isoformat(),
                failure_detail="controller stopped responding",
            )
        )
        session.commit()

    started = _start(client)
    response = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["status"] == "handoff"
    assert payload["session"]["step"] == "physical_intervention"
    assert any(fault["code"] == "active_or_faulted_motion" for fault in payload["faults"])
    assert "Physical inspection" in payload["session"]["message"]


def test_recovery_reconciles_verified_pallet_location_and_supervisor_command(
    client: TestClient,
    monkeypatch,
) -> None:
    motion_id = str(uuid4())
    pallet_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.robot_supervisor_last_sequence = 12
        session.add(Pallet(
            id=pallet_id,
            name="ABBA",
            workholding="Fixture",
            weight_kg=1,
            content_status="raw_stock",
            location="robot_held",
            return_pool_slot_number=6,
        ))
        session.add(RobotMotion(
            id=motion_id,
            pallet_id=pallet_id,
            operation="put",
            destination_slot=6,
            program_path="/programs/put_6.urp",
            status="faulted",
            created_at=now,
            failure_detail="Physical result uncertain.",
        ))
        session.add(RobotSupervisorCommand(
            id=str(uuid4()),
            sequence=12,
            robot_motion_id=motion_id,
            operation="put_pool",
            opcode=2,
            argument=6,
            status="uncertain",
            attempted=True,
            created_at=now,
            fault_detail="Timed out after dispatch.",
        ))
        session.commit()

    class ConnectedRobot:
        def status(self) -> dict:
            return {"connected": True, "latched": False, "robot_last_sequence": 12}

    monkeypatch.setattr(service, "robot_supervisor", lambda: ConnectedRobot())
    monkeypatch.setattr(service, "reset_robot_connections", lambda: None)
    monkeypatch.setattr(
        service,
        "auto_recover_controller_connections",
        lambda _session, progress=None: {
            "results": [{"controller": "Mongo", "action": "Healthy", "detail": "Connected."}]
        },
    )
    monkeypatch.setattr(
        service,
        "clear_robot_controller_fault",
        lambda *_args: {"action": "Healthy", "message": "No robot controller fault is active."},
    )

    started = _start(client)
    handoff = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    )
    assert handoff.status_code == 200
    motion_fault = next(item for item in handoff.json()["faults"] if item["code"] == "active_or_faulted_motion")
    assert {option["value"] for option in motion_fault["options"]} == {"robot_held", "destination_pool"}

    recovered = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "resolution": "destination_pool"},
    )

    assert recovered.status_code == 200
    assert recovered.json()["session"]["status"] == "ready"
    with client.app.state.session_factory() as session:
        pallet = session.get(Pallet, pallet_id)
        motion = session.get(RobotMotion, motion_id)
        command = session.scalar(service.select(RobotSupervisorCommand).where(RobotSupervisorCommand.sequence == 12))
        assert (pallet.location, pallet.pool_slot_number) == ("pool", 6)
        assert motion.status == "reconciled"
        assert command.status == "operator_completed"


def test_recovery_guides_and_reconciles_uncertain_robot_command_without_motion(
    client: TestClient,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.robot_supervisor_last_sequence = 4
        session.add(RobotSupervisorCommand(
            id=str(uuid4()), sequence=4, operation="set_output", opcode=5,
            status="uncertain", attempted=True, created_at=now,
            fault_detail="Result was not received.",
        ))
        session.commit()

    class ConnectedRobot:
        def status(self) -> dict:
            return {
                "connected": True, "latched": False, "robot_last_sequence": 4,
                "telemetry_age_seconds": 0.1,
            }

    monkeypatch.setattr(service, "robot_supervisor", lambda: ConnectedRobot())
    monkeypatch.setattr(service, "reset_robot_connections", lambda: None)
    monkeypatch.setattr(service, "robot_dashboard_health", lambda *_args: {"ok": True})
    monkeypatch.setattr(
        service,
        "clear_robot_controller_fault",
        lambda *_args: {"action": "Healthy", "message": "No fault."},
    )

    started = _start(client)
    handoff = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    ).json()
    assert handoff["session"]["status"] == "handoff"
    guidance = next(item for item in handoff["guidance"] if item["key"] == "robot_command")
    assert guidance["title"] == "Confirm the Mongo connection action"
    assert "does not move the robot or a pallet" in guidance["detail"]
    assert guidance["options"][0]["label"] == "It completed — continue."

    recovered = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "choices": {"robot_command": "mark_faulted"}},
    )
    assert recovered.status_code == 200
    assert recovered.json()["session"]["status"] == "ready"
    with client.app.state.session_factory() as session:
        command = session.scalar(service.select(RobotSupervisorCommand).where(RobotSupervisorCommand.sequence == 4))
        assert command.status == "operator_faulted"


def test_recovery_guides_and_reconciles_uncertain_mill_command(
    client: TestClient,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.cnc_telemetry_enabled = True
        settings.cnc_host = "192.168.0.42"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "operator"
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        settings.mill_supervisor_last_sequence = 8
        session.add(MillSupervisorCommand(
            id=str(uuid4()), sequence=8, operation="run_program",
            status="uncertain", attempted=True, created_at=now,
            fault_detail="Completion was not received.",
        ))
        session.commit()

    class ConnectedMill:
        def status(self) -> dict:
            return {
                "connected": True, "mill_last_sequence": 8,
                "telemetry_age_seconds": 0.1, "last_result": {},
            }

    monkeypatch.setattr(service, "mill_supervisor", lambda: ConnectedMill())
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *_args: {"interp_state": 1})

    started = _start(client)
    handoff = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    ).json()
    assert handoff["session"]["status"] == "handoff"
    guidance = next(item for item in handoff["guidance"] if item["key"] == "mill_command")
    assert guidance["title"] == "Check the last mill action"
    assert "will not rerun a program" in guidance["detail"]

    recovered = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "choices": {"mill_command": "accept_completed"}},
    )
    assert recovered.status_code == 200
    assert recovered.json()["session"]["status"] == "ready"
    with client.app.state.session_factory() as session:
        command = session.scalar(service.select(MillSupervisorCommand).where(MillSupervisorCommand.sequence == 8))
        assert command.status == "operator_completed"


def test_recovery_guides_safe_run_mode_stop(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_state = "running"
        session.commit()

    started = _start(client)
    handoff = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    ).json()
    assert any(item["key"] == "run_mode" for item in handoff["guidance"])

    recovered = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "choices": {"run_mode": "request_stop"}},
    )
    assert recovered.status_code == 200
    assert recovered.json()["session"]["status"] == "ready"
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        assert settings.run_mode_enabled is False
        assert settings.run_mode_state == "stopped"


def test_recovery_preserves_machine_pallet_during_guided_run_stop(client: TestClient) -> None:
    pallet_id = str(uuid4())
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_state = "running"
        session.add(Pallet(
            id=pallet_id, name="In Mill", workholding="Vise", weight_kg=1,
            content_status="raw_stock", program_path="job.nc", location="machine",
            return_pool_slot_number=2,
        ))
        session.commit()

    started = _start(client)
    client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "answers": {"cell_clear": True}},
    )
    stopping = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "choices": {"run_mode": "request_stop"}},
    )
    assert stopping.status_code == 200
    assert stopping.json()["session"]["status"] == "handoff"
    assert any(item["key"] == "run_mode_fault" for item in stopping.json()["guidance"])

    recovered = client.post(
        "/api/recovery/answer",
        json={"session_id": started["session"]["id"], "choices": {"run_mode_fault": "stop_keep_machine"}},
    )
    assert recovered.status_code == 200
    assert recovered.json()["session"]["status"] == "ready"
    with client.app.state.session_factory() as session:
        pallet = session.get(Pallet, pallet_id)
        assert pallet.location == "machine"
        assert pallet.return_pool_slot_number == 2


def test_recovery_explains_robot_connectivity_checks(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "robot_supervisor_status",
        lambda _session: {"connected": False, "last_disconnect_detail": "Connection refused."},
    )
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()

    started = _start(client)
    guidance = next(item for item in started["guidance"] if item["key"] == "robot_connectivity")
    assert "powered on" in guidance["detail"]
    assert "firewall" in guidance["detail"]


def test_recovery_can_be_cancelled(client: TestClient) -> None:
    started = _start(client)
    response = client.post(
        "/api/recovery/cancel",
        json={"session_id": started["session"]["id"]},
    )

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["session"]["status"] == "cancelled"
    assert client.get("/api/recovery/status").json()["session"] is None


def test_controller_recovery_does_not_skip_configured_services(client: TestClient, monkeypatch) -> None:
    class FakeSupervisor:
        def __init__(self) -> None:
            self.connected = False

        def status(self) -> dict:
            return {"connected": self.connected, "telemetry_age_seconds": 0.1 if self.connected else None}

    robot = FakeSupervisor()
    mill = FakeSupervisor()
    monkeypatch.setattr(service, "robot_supervisor", lambda: robot)
    monkeypatch.setattr(service, "mill_supervisor", lambda: mill)
    monkeypatch.setattr(service, "robot_supervisor_status", lambda _session: {"connected": robot.connected, "reconciliation": None})
    monkeypatch.setattr(service, "robot_program_status", lambda *_args: {"running": False, "loaded_program": "/programs/operator_job.urp"})
    monkeypatch.setattr(service, "robot_dashboard_health", lambda *_args: {"ok": True})
    monkeypatch.setattr(service, "bootstrap_robot_supervisor", lambda _session: setattr(robot, "connected", True))
    monkeypatch.setattr(service, "bootstrap_mill_supervisor", lambda _session: setattr(mill, "connected", True))
    monkeypatch.setattr(service, "set_mill_supervisor_activation", lambda *_args: None)

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        settings.robot_supervisor_enabled = False
        settings.robot_supervisor_activation_verified = True
        settings.cnc_telemetry_enabled = True
        settings.cnc_host = "192.168.0.42"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "operator"
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = False
        session.commit()

        result = service.auto_recover_controller_connections(session)

    actions = {(item["controller"], item["action"]) for item in result["results"]}
    assert ("Mongo", "Recovered") in actions
    assert ("Mongo", "Selected program preserved") in actions
    assert ("Mill", "Recovered") in actions
    assert ("Mongo", "Skipped") not in actions
    assert ("Mill", "Skipped") not in actions
    with client.app.state.session_factory() as session:
        assert service.get_settings(session).robot_supervisor_enabled is True


def test_controller_recovery_notes_different_running_mill_program_and_continues(client: TestClient, monkeypatch) -> None:
    class DisconnectedMill:
        def status(self) -> dict:
            return {"connected": False, "telemetry_age_seconds": None}

        def start(self, *_args) -> None:
            raise AssertionError("Recovery must not restart a helper while a different program is running.")

    monkeypatch.setattr(service, "mill_supervisor", DisconnectedMill)
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *_args: {
        "interp_state": 2,
        "program": "/home/operator/gcode/operator_job.nc",
    })

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.cnc_telemetry_enabled = True
        settings.cnc_host = "192.0.2.42"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "operator"
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        session.commit()

        result = service.auto_recover_controller_connections(session)

    assert any(item["action"] == "Different program running" for item in result["results"])
    assert not any(item["controller"] == "Mill" and item["action"] == "Deferred" for item in result["results"])


def test_mill_bootstrap_restarts_a_stale_helper_before_launch(client: TestClient, monkeypatch) -> None:
    events: list[str] = []

    class ConnectedMill:
        def start(self, *_args) -> None:
            events.append("listener")

        def wait_for_event(self, *_args):
            return None

        def status(self) -> dict:
            return {
                "connected": True,
                "mill_last_sequence": 0,
                "telemetry": {"interp_state": 1, "estop": False, "enabled": True},
            }

    monkeypatch.setattr(service, "mill_supervisor", lambda: ConnectedMill())
    monkeypatch.setattr(service, "test_mill_supervisor_runtime", lambda *_args: None)
    monkeypatch.setattr(service, "write_robot_text_file", lambda **_kwargs: events.append("write"))
    monkeypatch.setattr(service, "stop_mill_supervisor_agent", lambda *_args: events.append("stop"))
    monkeypatch.setattr(service, "start_mill_supervisor_agent", lambda *_args: events.append("start"))

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.cnc_host = "192.168.0.42"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "operator"
        session.commit()

        result = service.bootstrap_mill_supervisor(session)

    assert result["connected"] is True
    assert events[-2:] == ["stop", "start"]


def test_successful_retry_ignores_deferred_actions_from_an_earlier_attempt(client: TestClient, monkeypatch) -> None:
    started = _start(client)
    session_id = started["session"]["id"]
    with client.app.state.session_factory() as session:
        recovery = session.get(RecoverySession, session_id)
        recovery.status = "handoff"
        recovery.step = "services"
        recovery.actions_json = json.dumps([
            {"controller": "Mongo", "action": "Deferred", "detail": "Earlier failure."}
        ])
        session.commit()

    monkeypatch.setattr(
        service,
        "auto_recover_controller_connections",
        lambda _session, progress=None: {
            "results": [{"controller": "Mongo", "action": "Healthy", "detail": "Connected."}]
        },
    )
    response = client.post(
        "/api/recovery/answer",
        json={"session_id": session_id, "answers": {"retry": True}},
    )

    assert response.status_code == 200
    assert response.json()["session"]["status"] == "ready"
    assert any(item["detail"] == "Earlier failure." for item in response.json()["session"]["actions"])
