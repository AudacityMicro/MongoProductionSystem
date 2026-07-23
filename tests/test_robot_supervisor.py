from __future__ import annotations

import struct

import pytest

from app.robot_supervisor import (
    EVENT_ACCEPTED,
    EVENT_COMPLETED,
    EVENT_RUNNING,
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
from app.models import RobotSupervisorCommand
from app.schemas import StartPalletMotion


def event_frame(sequence: int, event: int) -> bytes:
    return encode_frame(KIND_EVENT, [9001, sequence, event, 0, 1, sequence])


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
    assert "no-motion" in response.json()["detail"]


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
