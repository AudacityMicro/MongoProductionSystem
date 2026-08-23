from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.robot_supervisor import (
    EVENT_ACCEPTED,
    EVENT_COMPLETED,
    EVENT_LATCHED,
    EVENT_RUNNING,
    FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION,
    KIND_EVENT,
    FrameBuffer,
    DispatchReceipt,
    RobotSupervisorManager,
    SupervisorEvent,
    SupervisorProtocolError,
    decode_frame,
    encode_frame,
)
from app import service
from app import main as application_main
from app.models import RobotSupervisorCommand
from app.schemas import StartPalletMotion, SupervisorReconcile


def event_frame(sequence: int, event: int) -> bytes:
    return encode_frame(KIND_EVENT, [9001, sequence, event, 0, 1, sequence])


def test_only_terminal_no_motion_maintenance_gap_is_automatically_repairable(client) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_last_sequence = 8
        command = RobotSupervisorCommand(
            id="maintenance-gap",
            sequence=8,
            robot_session=9001,
            robot_motion_id=None,
            operation="bootstrap_restart",
            opcode=21,
            argument=0,
            value=0,
            payload_g=0,
            transport="supervisor",
            status="operator_completed",
            attempted=True,
            created_at="2026-08-10T00:00:00+00:00",
        )
        session.add(command)
        session.commit()
        live = {
            "connected": True,
            "latched": False,
            "robot_last_sequence": 7,
            "telemetry": {"safety_mode": 1, "runtime_state": 1},
        }

        assert service._repairable_supervisor_maintenance_gap(session, settings, live) is command

        command.operation = "pick_pool"
        session.commit()
        assert service._repairable_supervisor_maintenance_gap(session, settings, live) is None


def test_stale_connected_supervisor_is_not_treated_as_fresh(client) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_heartbeat_seconds = 1.0

        assert service._robot_supervisor_connection_is_fresh(
            {"connected": True, "telemetry_age_seconds": 3.9}, settings
        ) is True
        assert service._robot_supervisor_connection_is_fresh(
            {"connected": True, "telemetry_age_seconds": 30.0}, settings
        ) is False


def test_auto_recovery_closes_stale_socket_and_reenables_verified_routing(client, monkeypatch) -> None:
    class StaleThenFreshSupervisor:
        def __init__(self) -> None:
            self.stopped = 0
            self.started = 0
            self.fresh = False

        def status(self):
            return {
                "connected": True,
                "listening": True,
                "telemetry_age_seconds": 0.1 if self.fresh else 120.0,
                "heartbeat_age_seconds": 0.1 if self.fresh else 120.0,
                "robot_session": 9001,
                "robot_last_sequence": 0,
                "robot_last_event": "idle",
                "latched": False,
                "telemetry": {"safety_mode": 1, "runtime_state": 1},
            }

        def stop(self) -> None:
            self.stopped += 1

        def start(self, *_args) -> None:
            self.started += 1
            self.fresh = True

    supervisor = StaleThenFreshSupervisor()
    monkeypatch.setattr(service, "robot_supervisor", lambda: supervisor)
    monkeypatch.setattr(service, "robot_dashboard_health", lambda *_args: {"ok": True})

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.41"
        settings.robot_supervisor_enabled = False
        settings.robot_supervisor_activation_verified = False
        session.commit()

        result = service.auto_recover_controller_connections(session)

        restored = service.get_settings(session)
        assert restored.robot_supervisor_enabled is True
        assert restored.robot_supervisor_activation_verified is True

    assert supervisor.stopped == 1
    assert supervisor.started == 1
    assert any(item["action"] == "Stale socket reset" for item in result["results"])
    assert any(item["action"] == "Enabled" for item in result["results"])


def test_failed_bootstrap_preserves_prior_supervisor_activation(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "generated_script_directory", lambda _root: tmp_path)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.41"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.robot_supervisor_maintenance_mode = False
        session.commit()

        with pytest.raises(Exception, match="Rebuild generated scripts"):
            service.bootstrap_robot_supervisor(session)

        restored = service.get_settings(session)
        assert restored.robot_supervisor_enabled is True
        assert restored.robot_supervisor_activation_verified is True
        assert restored.robot_supervisor_maintenance_mode is False


def test_fragmented_numeric_frames_are_reassembled() -> None:
    frame = event_frame(7, EVENT_COMPLETED)
    buffer = FrameBuffer()
    assert buffer.feed(frame[:3]) == []
    assert buffer.feed(frame[3:11]) == []
    frames = buffer.feed(frame[11:])
    assert len(frames) == 1
    assert decode_frame(frames[0])[3:5] == [7, EVENT_COMPLETED]


def test_corrupt_checksum_is_rejected() -> None:
    frame = bytearray(event_frame(2, EVENT_COMPLETED))
    frame[-3] = ord("0") if frame[-3] != ord("0") else ord("1")
    with pytest.raises(SupervisorProtocolError, match="checksum"):
        decode_frame(bytes(frame))


def test_frame_buffer_resynchronizes_after_aligned_garbage() -> None:
    first = event_frame(1, EVENT_COMPLETED)
    second = event_frame(2, EVENT_COMPLETED)
    buffer = FrameBuffer()

    frames = buffer.feed(first + struct.pack("!i", 400) + second)

    assert [decode_frame(frame)[3] for frame in frames] == [1, 2]
    assert buffer.resynchronizations == 1


def test_frame_buffer_skips_truncated_frame_and_recovers_next_checksum() -> None:
    damaged = event_frame(1, EVENT_COMPLETED)[:-4]
    valid = event_frame(2, EVENT_COMPLETED)
    buffer = FrameBuffer()

    frames = buffer.feed(damaged + valid)

    assert [decode_frame(frame)[3] for frame in frames] == [2]
    assert buffer.resynchronizations >= 1


def test_event_ordering_rejects_regression_but_allows_duplicate() -> None:
    manager = RobotSupervisorManager()
    manager._handle_frame(decode_frame(event_frame(4, EVENT_ACCEPTED)))
    manager._handle_frame(decode_frame(event_frame(4, EVENT_RUNNING)))
    manager._handle_frame(decode_frame(event_frame(4, EVENT_RUNNING)))
    with pytest.raises(SupervisorProtocolError, match="Out-of-order"):
        manager._handle_frame(decode_frame(event_frame(4, EVENT_ACCEPTED)))


def test_robot_status_document_checkpoints_robot_event(tmp_path: Path) -> None:
    document_path = tmp_path / "robot-supervisor-status.json"
    manager = RobotSupervisorManager(status_document_path=document_path)

    manager._handle_frame(decode_frame(event_frame(42, EVENT_COMPLETED)))

    status = manager.status_document()
    assert status["available"] is True
    assert status["document"]["version"] == "MPS-ROBOT-STATUS-V1"
    assert status["document"]["command"]["last_sequence"] == 42
    assert status["document"]["command"]["last_event"] == "completed"
    assert status["document"]["command"]["fault_code"] == 0
    assert "telemetry" not in status["document"]


def test_conflicting_terminal_events_are_rejected() -> None:
    manager = RobotSupervisorManager()
    manager._handle_frame(decode_frame(event_frame(4, EVENT_COMPLETED)))
    with pytest.raises(SupervisorProtocolError, match="Conflicting terminal"):
        manager._handle_frame(decode_frame(event_frame(4, 4)))


def test_unvalidated_socket_is_not_reported_as_connected() -> None:
    manager = RobotSupervisorManager()
    manager._connections.add(object())  # type: ignore[arg-type]
    assert manager.status()["connected"] is False


def test_connection_generation_advances_when_same_robot_session_reconnects() -> None:
    class Connection:
        def close(self) -> None:
            pass

    manager = RobotSupervisorManager()
    hello = [1, 10, 9001, 4, EVENT_COMPLETED, 0, 1, 0]
    manager._activate_connection(Connection(), "robot:50010", hello)  # type: ignore[arg-type]
    first_generation = manager.status()["connection_generation"]

    manager._activate_connection(Connection(), "robot:50011", hello)  # type: ignore[arg-type]

    assert manager.status()["robot_session"] == 9001
    assert manager.status()["connection_generation"] == first_generation + 1
    assert manager.wait_for_connection_generation(first_generation, 0.01) is True


def test_event_retention_is_bounded() -> None:
    manager = RobotSupervisorManager()
    for sequence in range(1, 530):
        manager._handle_frame(decode_frame(event_frame(sequence, EVENT_COMPLETED)))
    status = manager.status()
    assert status["retained_event_sequences"] == 512
    assert manager.events_for(1) == []


def test_disconnected_dispatch_is_unambiguously_not_attempted() -> None:
    manager = RobotSupervisorManager()
    receipt = manager.dispatch(1, 1, 1)
    assert receipt.attempted is False
    assert receipt.sent is False


def test_supervisor_settings_default_to_safe_inactive_state(client) -> None:
    board = client.get("/api/board").json()
    settings = board["settings"]
    assert settings["robot_supervisor_enabled"] is False
    assert settings["robot_supervisor_activation_verified"] is False
    assert settings["robot_supervisor_port"] == 50010

    status = client.get("/api/debug/robot-supervisor")
    assert status.status_code == 200
    assert status.json()["enabled"] is False


def test_supervisor_cannot_be_enabled_before_no_motion_handshake(client) -> None:
    board = client.get("/api/board").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "robot_supervisor_enabled": True,
        },
    )
    assert response.status_code == 409


def test_unchanged_supervisor_settings_preserve_verified_connection(client) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()

    board = client.get("/api/board").json()
    settings = board["settings"]
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "robot_supervisor_enabled": True,
            "robot_supervisor_hostname": settings["robot_supervisor_hostname"],
            "robot_supervisor_listen_host": settings["robot_supervisor_listen_host"],
            "robot_supervisor_port": settings["robot_supervisor_port"],
            "robot_supervisor_heartbeat_seconds": settings["robot_supervisor_heartbeat_seconds"],
            "robot_supervisor_telemetry_hz": settings["robot_supervisor_telemetry_hz"],
            "robot_supervisor_reconnect_limit_seconds": settings["robot_supervisor_reconnect_limit_seconds"],
        },
    )

    assert response.status_code == 200
    refreshed = client.get("/api/board").json()["settings"]
    assert refreshed["robot_supervisor_enabled"] is True
    assert refreshed["robot_supervisor_activation_verified"] is True


def test_live_supervisor_listener_prevents_legacy_telemetry_polling(client, monkeypatch) -> None:
    class ListeningSupervisor:
        def status(self):
            return {"listening": True, "connected": False, "telemetry": {}}

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.48"
        session.commit()
        expected_revision = settings.revision

    monkeypatch.setattr(service, "robot_supervisor", lambda: ListeningSupervisor())
    monkeypatch.setattr(
        service,
        "_cached_robot_telemetry",
        lambda *_: (_ for _ in ()).throw(AssertionError("legacy telemetry should not be polled")),
    )

    response = client.get("/api/debug/robot-io")

    assert response.status_code == 200
    assert response.json()["source"] == "robot-supervisor"
    assert response.json()["revision"] == expected_revision


def test_idle_supervisor_recovery_restarts_only_the_local_listener(client, monkeypatch) -> None:
    class DisconnectedSupervisor:
        def __init__(self):
            self.stopped = 0
            self.started = []

        def status(self):
            return {"connected": False}

        def stop(self):
            self.stopped += 1

        def start(self, *args):
            self.started.append(args)

    supervisor = DisconnectedSupervisor()
    monkeypatch.setattr(service, "robot_supervisor", lambda: supervisor)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()
        assert service.recover_robot_supervisor_connection(session, trigger="test") is True
    assert supervisor.stopped == 1
    assert supervisor.started


def test_supervisor_recovery_refuses_active_run_mode(client, monkeypatch) -> None:
    class DisconnectedSupervisor:
        def status(self):
            return {"connected": False}

        def stop(self):
            raise AssertionError("must not reset during Run Mode")

    monkeypatch.setattr(service, "robot_supervisor", lambda: DisconnectedSupervisor())
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.run_mode_enabled = True
        session.commit()
        assert service.recover_robot_supervisor_connection(session, trigger="test") is False


def test_manual_program_recovery_refuses_to_replace_a_loaded_controller_program(client, monkeypatch) -> None:
    class DisconnectedSupervisor:
        def status(self):
            return {"connected": False}

    monkeypatch.setattr(service, "robot_supervisor", lambda: DisconnectedSupervisor())
    monkeypatch.setattr(
        service,
        "robot_program_status",
        lambda *_args: {"running": False, "loaded_program": "/programs/operator-job.urp"},
    )
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.48"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()
        with pytest.raises(Exception, match="controller program selected"):
            service.recover_robot_supervisor_program(session, trigger="test")


def test_manual_program_recovery_bootstraps_only_when_dashboard_is_idle(client, monkeypatch) -> None:
    class DisconnectedSupervisor:
        def status(self):
            return {"connected": False}

    monkeypatch.setattr(service, "robot_supervisor", lambda: DisconnectedSupervisor())
    monkeypatch.setattr(service, "robot_program_status", lambda *_args: {"running": False, "loaded_program": None})
    expected = {"connected": True, "reconciliation_required": True}
    monkeypatch.setattr(service, "bootstrap_robot_supervisor", lambda _session: expected)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.48"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()
        assert service.recover_robot_supervisor_program(session, trigger="test") == expected


def test_manual_jog_recovery_restarts_only_the_stopped_mongo_supervisor(client, monkeypatch) -> None:
    class DisconnectedSupervisor:
        def status(self):
            return {"connected": False}

    monkeypatch.setattr(service, "robot_supervisor", lambda: DisconnectedSupervisor())
    monkeypatch.setattr(
        service,
        "robot_program_status",
        lambda *_args: {"running": False, "loaded_program": "/programs/mongo-production-system/mongo_supervisor.script"},
    )
    expected = {"connected": True, "reconciliation_required": False}
    monkeypatch.setattr(service, "bootstrap_robot_supervisor", lambda _session: expected)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.48"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()
        assert service.recover_robot_supervisor_program(session, trigger="watchdog") == expected


def test_manual_program_recovery_api_uses_the_guarded_service(client, monkeypatch) -> None:
    monkeypatch.setattr(
        application_main,
        "recover_robot_supervisor_program",
        lambda _session, *, trigger: {"connected": True, "trigger": trigger},
    )

    response = client.post("/api/debug/robot-supervisor/recover", json={})

    assert response.status_code == 200
    assert response.json() == {"connected": True, "trigger": "operator"}


def test_manual_robot_reconnect_is_available_during_a_paused_mill_retry(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(service, "robot_supervisor_status", lambda _session: {"reconciliation_required": False})
    monkeypatch.setattr(
        service,
        "recover_robot_supervisor_program",
        lambda _session, **kwargs: calls.append(kwargs) or {"connected": True},
    )
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.86.48"
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.run_mode_enabled = True
        settings.run_mode_state = "retry_cnc_program"
        session.commit()

        result = service.reconnect_robot_after_manual_control(session)

    assert result["status"] == "reconnected"
    assert "Run Mode remains paused" in result["message"]
    assert calls == [{"trigger": "manual-control-reconnect", "allow_run_mode_paused": True}]


def test_reconciled_command_from_prior_robot_session_does_not_block_recovery(client, monkeypatch) -> None:
    class NewRobotSession:
        def status(self):
            return {
                "connected": True,
                "robot_session": 22,
                "robot_last_sequence": 7,
                "robot_last_event": "idle",
                "telemetry_age_seconds": 0.1,
            }

    monkeypatch.setattr(service, "robot_supervisor", NewRobotSession)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_last_sequence = 7
        session.add(RobotSupervisorCommand(
            id="prior-session-command", sequence=7, robot_session=11,
            operation="pick_pool", opcode=1, status="operator_faulted", attempted=True,
            created_at="2026-08-06T00:00:00+00:00", completed_at="2026-08-06T00:01:00+00:00",
        ))
        session.commit()

        status = service.robot_supervisor_status(session)

    assert status["session_mismatch"] is False
    assert status["reconciliation_required"] is False


def test_auto_controller_recovery_leaves_live_supervisors_untouched(client, monkeypatch) -> None:
    class ConnectedSupervisor:
        def status(self):
            return {"connected": True, "telemetry_age_seconds": 0.1}

    monkeypatch.setattr(service, "robot_supervisor", lambda: ConnectedSupervisor())
    monkeypatch.setattr(service, "mill_supervisor", lambda: ConnectedSupervisor())
    monkeypatch.setattr(service, "robot_dashboard_health", lambda *_args: {"ok": True})
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *_args: {"interp_state": 1})
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.mill_supervisor_enabled = True
        settings.mill_supervisor_activation_verified = True
        session.commit()

        result = service.auto_recover_controller_connections(session)

    assert result["results"] == [
        {"controller": "Mongo", "action": "Healthy", "detail": "Supervisor connection and telemetry are live."},
        {"controller": "Mongo", "action": "Dashboard checked", "detail": "Dashboard responded."},
        {"controller": "Mill", "action": "PathPilot checked", "detail": "PathPilot reports Idle."},
        {"controller": "Mill", "action": "Healthy", "detail": "Mill supervisor connection and telemetry are live."},
    ]


def test_supervisor_latch_cannot_clear_an_unconfirmed_command(client, monkeypatch) -> None:
    fake = CompletedSupervisor()
    monkeypatch.setattr(service, "robot_supervisor", lambda: fake)
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        session.commit()
        command = service._new_supervisor_command(
            session,
            motion=None,
            operation="load_mill",
            opcode=1,
        )
        command.status = "uncertain"
        session.commit()

        with pytest.raises(Exception, match="Confirm whether the physical operation completed"):
            service.reconcile_robot_supervisor(
                session,
                SupervisorReconcile(
                    expected_revision=service.get_settings(session).revision,
                    sequence=command.sequence,
                    resolution="clear_latch",
                ),
            )

        service.reconcile_robot_supervisor(
            session,
            SupervisorReconcile(
                expected_revision=service.get_settings(session).revision,
                sequence=command.sequence,
                resolution="accept_completed",
            ),
        )
        assert session.get(RobotSupervisorCommand, command.id).status == "operator_completed"


def test_explicit_listener_restart_refuses_active_run_mode(client, monkeypatch) -> None:
    class Supervisor:
        def stop(self):
            raise AssertionError("must not restart during Run Mode")

    monkeypatch.setattr(service, "robot_supervisor", lambda: Supervisor())
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.run_mode_enabled = True
        session.commit()
        with pytest.raises(Exception, match="Stop Run Mode"):
            service.restart_robot_supervisor_listener(session, trigger="test")


class CompletedSupervisor:
    def __init__(self, *, uncertain: bool = False):
        self.sequence = 0
        self.uncertain = uncertain

    def status(self):
        return {
            "connected": True,
            "robot_session": 9001,
            "app_session": 7001,
            "robot_last_sequence": self.sequence,
            "robot_last_event": "completed" if self.sequence else "idle",
            "latched": False,
            "telemetry": {"safety_mode": 1, "runtime_state": 1, "tcp_speed": [0] * 6},
            "heartbeat_age_seconds": 0.01,
        }

    def dispatch(self, sequence, *_args, **_kwargs):
        self.sequence = sequence
        if self.uncertain:
            return DispatchReceipt(sequence, attempted=True, sent=False, detail="wire failed after send attempt")
        return DispatchReceipt(sequence, attempted=True, sent=True)

    def wait_for_event(self, sequence, _timeout, **_kwargs):
        return SupervisorEvent(sequence, EVENT_COMPLETED, robot_session=9001)

    def events_for(self, sequence):
        return [
            SupervisorEvent(sequence, EVENT_ACCEPTED, robot_session=9001),
            SupervisorEvent(sequence, EVENT_RUNNING, robot_session=9001),
            SupervisorEvent(sequence, EVENT_COMPLETED, robot_session=9001),
        ]


class CompletedLinkLossSupervisor(CompletedSupervisor):
    """Reports a completed atomic step followed by a recoverable 104 latch."""

    def __init__(self):
        super().__init__()
        self.latched = False
        self.link_loss_sequence = 0

    def status(self):
        result = super().status()
        result["latched"] = self.latched
        result["robot_last_sequence"] = self.sequence
        result["robot_last_event"] = "latched" if self.latched else "completed"
        return result

    def dispatch(self, sequence, *_args, **_kwargs):
        self.sequence = sequence
        if self.link_loss_sequence == 0:
            self.link_loss_sequence = sequence
            self.latched = True
        else:
            self.latched = False
        return DispatchReceipt(sequence, attempted=True, sent=True)

    def wait_for_event(self, sequence, _timeout, **_kwargs):
        if sequence == self.link_loss_sequence:
            return SupervisorEvent(sequence, EVENT_LATCHED, FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION, robot_session=9001)
        return SupervisorEvent(sequence, EVENT_COMPLETED, robot_session=9001)

    def events_for(self, sequence):
        terminal = EVENT_LATCHED if sequence == self.link_loss_sequence else EVENT_COMPLETED
        return [
            SupervisorEvent(sequence, EVENT_ACCEPTED, robot_session=9001),
            SupervisorEvent(sequence, EVENT_RUNNING, robot_session=9001),
            SupervisorEvent(
                sequence,
                terminal,
                FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION if terminal == EVENT_LATCHED else 0,
                robot_session=9001,
            ),
        ]


def _supervisor_motion(client, monkeypatch, fake: CompletedSupervisor):
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": 0,
            "workholding": "Vise",
            "weight_kg": 4.5,
            "content_status": "raw_stock",
        },
    ).json()
    pallet = board["pallets"][0]
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "mongo"
        settings.pallet_motion_enabled = True
        settings.pallet_motion_programs = '[{"slot":1,"pick_program":"/programs/pick.urp","put_program":"/programs/put.urp"}]'
        settings.robot_supervisor_enabled = True
        settings.robot_supervisor_activation_verified = True
        settings.revision += 1
        session.commit()
        revision = settings.revision
    monkeypatch.setattr(service, "robot_supervisor", lambda: fake)
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    monkeypatch.setattr(service, "_assert_pool_motion_position_configured", lambda *_: None)
    with client.app.state.session_factory() as session:
        motion_id = service.start_pallet_motion(
            session,
            StartPalletMotion(
                expected_revision=revision,
                operation="pick",
                pool_slot_number=1,
                pallet_id=pallet["id"],
            ),
        )
    return pallet, motion_id


def test_matching_completed_event_updates_board_once_and_persists_ledger(client, monkeypatch) -> None:
    pallet, motion_id = _supervisor_motion(client, monkeypatch, CompletedSupervisor())
    service.execute_pallet_motion(client.app.state.session_factory, motion_id)
    result = client.get("/api/board").json()
    moved = next(item for item in result["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "robot_held"
    with client.app.state.session_factory() as session:
        ledger = session.query(RobotSupervisorCommand).one()
        assert ledger.status == "completed"
        assert ledger.attempted is True
        assert ledger.accepted_at is not None
        assert ledger.started_at is not None


def test_completed_link_loss_is_cleared_before_applying_the_known_motion_result(client, monkeypatch) -> None:
    pallet, motion_id = _supervisor_motion(client, monkeypatch, CompletedLinkLossSupervisor())

    service.execute_pallet_motion(client.app.state.session_factory, motion_id)

    result = client.get("/api/board").json()
    moved = next(item for item in result["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "robot_held"
    with client.app.state.session_factory() as session:
        ledger = session.query(RobotSupervisorCommand).order_by(RobotSupervisorCommand.sequence).all()
        assert [item.status for item in ledger] == ["completed_link_lost", "completed"]
        assert ledger[0].result_code == FAULT_LINK_LOST_AFTER_ATOMIC_COMPLETION


def test_uncertain_supervisor_send_faults_without_legacy_fallback(client, monkeypatch) -> None:
    _pallet, motion_id = _supervisor_motion(client, monkeypatch, CompletedSupervisor(uncertain=True))
    monkeypatch.setattr(
        service,
        "run_robot_program",
        lambda *_: (_ for _ in ()).throw(AssertionError("legacy fallback must not run after send attempt")),
    )
    service.execute_pallet_motion(client.app.state.session_factory, motion_id)
    result = client.get("/api/board").json()
    assert result["robot_motion"]["active"]["status"] == "faulted"
    assert "wire failed" in result["robot_motion"]["active"]["failure_detail"]
    with client.app.state.session_factory() as session:
        assert session.query(RobotSupervisorCommand).one().status == "uncertain"
