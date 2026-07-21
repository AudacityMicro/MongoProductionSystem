from fastapi.testclient import TestClient
from fastapi import HTTPException
from concurrent.futures import ThreadPoolExecutor
import pytest
import time

from app.main import create_app
from app.models import Pallet, RobotMotion
from app.schemas import StartRunMode
from app.service import build_mill_load_position_program
from app import cnc_linuxcnc, service


def test_network_diagnostic_reports_packet_loss_and_transit_times(monkeypatch) -> None:
    class PingResult:
        stdout = """Reply from 8.8.8.8: bytes=32 time=12ms TTL=117
Reply from 8.8.8.8: bytes=32 time<1ms TTL=117
Packets: Sent = 20, Received = 2, Lost = 18 (90% loss),
"""
        stderr = ""

    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: PingResult())
    monkeypatch.setattr(service, "_NETWORK_TEST_LAST_MANUAL_START", 0.0)

    result = service.network_diagnostic()

    assert result["target"] == "8.8.8.8"
    assert result["sent"] == 20
    assert result["received"] == 2
    assert result["packet_loss_percent"] == 90.0
    assert result["minimum_ms"] == 1.0
    assert result["maximum_ms"] == 12.0
    assert result["transit_times_ms"] == [12.0, 1.0]


def test_paused_legacy_runtime_requires_dashboard_to_prove_no_program(client: TestClient, monkeypatch) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "mongo"
        session.commit()

    paused = {"Runtime state": "paused", "Safety mode": 1}
    monkeypatch.setattr(service, "robot_program_status", lambda *_: {"running": False, "loaded_program": None})
    assert service._legacy_paused_runtime_is_empty(settings, paused) is True

    monkeypatch.setattr(service, "robot_program_status", lambda *_: {"running": False, "loaded_program": "/programs/job.urp"})
    assert service._legacy_paused_runtime_is_empty(settings, paused) is False


def test_network_diagnostic_returns_full_loss_result(monkeypatch) -> None:
    class PingResult:
        stdout = "Packets: Sent = 20, Received = 0, Lost = 20 (100% loss),"
        stderr = ""

    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: PingResult())
    monkeypatch.setattr(service, "_NETWORK_TEST_LAST_MANUAL_START", 0.0)

    result = service.network_diagnostic()

    assert result["received"] == 0
    assert result["packet_loss_percent"] == 100.0
    assert result["average_ms"] is None


def test_network_diagnostic_manual_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(service, "_NETWORK_TEST_LAST_MANUAL_START", 100.0)
    monkeypatch.setattr(service.time, "monotonic", lambda: 105.0)

    with pytest.raises(HTTPException) as error:
        service.network_diagnostic()

    assert error.value.status_code == 429


def test_robot_connection_loss_starts_network_test_at_most_every_180_seconds(monkeypatch) -> None:
    starts: list[str] = []

    class ImmediateThread:
        def __init__(self, *, target, args, **kwargs) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            starts.append(self.args[0])
            self.target(*self.args)

    monkeypatch.setattr(service, "Thread", ImmediateThread)
    monkeypatch.setattr(service, "_run_network_diagnostic", lambda: {"sent": 20, "received": 20})
    monkeypatch.setattr(service, "_NETWORK_TEST_ACTIVE", False)
    monkeypatch.setattr(service, "_NETWORK_TEST_LAST_AUTOMATIC_START", 0.0)
    clock = iter((200.0, 250.0, 380.0))
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))

    service.trigger_network_diagnostic_on_robot_loss()
    service.trigger_network_diagnostic_on_robot_loss()
    service.trigger_network_diagnostic_on_robot_loss()

    assert starts == ["automatic_robot_disconnect", "automatic_robot_disconnect"]


def wait_for_run_state(client: TestClient, state: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        board = client.get("/api/board").json()
        if board["run_mode"]["state"] == state:
            return board
        time.sleep(0.05)
    raise AssertionError(f"Run mode did not reach {state!r} before timeout.")


def test_health_and_pages(client: TestClient) -> None:
    response = client.get("/api/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["version"] == "0.3.0"
    assert isinstance(payload["process_id"], int)
    assert payload["started_at"]
    schedule_page = client.get("/").text
    assert "Pallet schedule" in schedule_page
    assert 'id="cancel-mill-putaway" type="button"' in schedule_page
    assert 'id="cancel-motion-recovery" type="button"' in schedule_page
    debugging_page = client.get("/debugging").text
    assert "Debugging" in debugging_page
    assert "Mongo controller" in debugging_page
    assert "Tormach 1500MX / PathPilot" in debugging_page
    assert 'id="retry-mongo-connection"' in debugging_page
    assert 'id="clear-mongo-fault"' in debugging_page
    settings_page = client.get("/settings").text
    assert "System settings" in settings_page
    assert "Close and relaunch" in settings_page
    assert "Workholding library" in settings_page
    assert 'id="workholding-options"' in schedule_page


def test_cnc_debug_baseline(client: TestClient) -> None:
    response = client.get("/api/debug/cnc")

    assert response.status_code == 200
    payload = response.json()
    assert payload["machine_model"] == "Tormach 1500MX"
    assert payload["connected"] is False
    assert payload["connection_label"] == "Telemetry disabled"
    assert payload["axis_rows"] == []
    assert payload["revision"] == 0
    assert len(payload["mill_program_controls"]["buttons"]) == 4


def test_mill_load_position_program_raises_z_before_xy() -> None:
    program = build_mill_load_position_program({"x_in": 0.01, "y_in": 4.9, "z_in": 0.0})

    assert "G20" in program
    assert "G90" in program
    assert "G53 G1 Z0.0000 F100.0" in program
    assert "G53 G1 X0.0100 Y4.9000 F100.0" in program
    assert program.index("G53 G1 Z") < program.index("G53 G1 X")
    assert program.rstrip().endswith("M30")


def test_pathpilot_program_run_uses_halui_remote_start(monkeypatch) -> None:
    captured = {}

    def fake_remote(host, port, username, password, timeout, remote_script, marker):
        captured["script"] = remote_script
        captured["marker"] = marker
        return {"accepted": True}

    monkeypatch.setattr(cnc_linuxcnc, "_read_remote_payload", fake_remote)
    result = cnc_linuxcnc.run_linuxcnc_program("mill", 22, "operator", "secret", 10, "/home/operator/gcode/Gcode/job.nc")

    assert result == {"accepted": True}
    assert "command.program_close()" not in captured["script"]
    assert 'wait_for_mode(command, linuxcnc.MODE_MDI, "MDI")' in captured["script"]
    assert 'wait_for_mode(command, linuxcnc.MODE_AUTO, "Auto")' in captured["script"]
    assert "shutil.copy2(filename, pathpilot_program)" in captured["script"]
    assert "command.program_open(pathpilot_program, os.path.dirname(filename))" in captured["script"]
    assert 'active_axes = int(getattr(status, "axes", 0) or 0)' in captured["script"]
    assert 'axis_mask = int(getattr(status, "axis_mask", 0) or 0)' in captured["script"]
    assert "command.auto(" not in captured["script"]
    assert 'set_hal_pin("halui.mode.auto", True)' in captured["script"]
    assert 'read_hal_pin("halui.mode.is-auto")' in captured["script"]
    assert 'set_hal_pin("halui.program.run", True)' in captured["script"]
    assert captured["script"].count('set_hal_pin("halui.program.run", False)') >= 2
    assert captured["script"].count('set_hal_pin("halui.mode.auto", False)') >= 2
    assert "def motion_lockout_active():" in captured["script"]
    assert "Restore the probe and press Reset on PathPilot" in captured["script"]
    assert 'if (getattr(status, "file", "") or "") == pathpilot_program' in captured["script"]
    assert 'PathPilot did not finish loading the requested program' in captured["script"]
    assert 'if last_interp_state != linuxcnc.INTERP_IDLE' in captured["script"]
    assert 'interpreter never left Idle' in captured["script"]
    assert "errors = linuxcnc.error_channel()" in captured["script"]
    assert '"task_mode=" + str(getattr(status, "task_mode", None))' in captured["script"]
    assert captured["marker"] == "MONGO_CNC_RUN="
    compile(captured["script"], "<pathpilot-run-script>", "exec")


def test_pathpilot_abort_waits_for_idle(monkeypatch) -> None:
    captured = {}

    def fake_remote(host, port, username, password, timeout, remote_script, marker):
        captured["script"] = remote_script
        captured["marker"] = marker
        return {"aborted": True, "interp_state": 1}

    monkeypatch.setattr(cnc_linuxcnc, "_read_remote_payload", fake_remote)

    result = cnc_linuxcnc.abort_linuxcnc_program("mill", 22, "operator", "secret", 10)

    assert result == {"aborted": True, "interp_state": 1}
    assert "command.abort()" in captured["script"]
    assert "LinuxCNC did not return to Idle" in captured["script"]
    assert captured["marker"] == "MONGO_CNC_ABORT="


def test_cnc_debug_reports_extended_machine_diagnostics(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.service.read_linuxcnc_snapshot",
        lambda *args: {
            "task_state": 4,
            "task_mode": 2,
            "interp_state": 1,
            "program": "/home/operator/program.nc",
            "tool_in_spindle": 20,
            "spindle_speed": 5000,
            "spindle_enabled": 1,
            "flood": False,
            "mist": False,
            "feedrate": 1.0,
            "axis_rows": [{"axis": "X", "position": 1.0, "commanded": 1.1, "velocity": 2.0, "following_error": 0.0, "homed": True, "limit": 0, "distance_to_go": 0.1}],
            "atc": {"slots": []},
            "tool_table": [],
            "health": {"enabled": True, "homed": [True], "limits": [0]},
            "motion": {"distance_to_go": 0.1},
            "coordinates": {"g5x_index": 1},
            "program_execution": {"g_codes": ["G54"], "m_codes": ["M5"]},
            "spindle_details": {"feedback_speed": 4999},
            "probe": {"tripped": False},
            "tooling": {"prepared_pocket": 3},
            "production": {"m30_a": 12},
            "io": {"digital_inputs": [False, True], "digital_outputs": [True], "analog_inputs": [0.0], "analog_outputs": [1.0]},
        },
    )
    board = client.get("/api/board").json()
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "cnc_telemetry_enabled": True,
            "cnc_host": "tormach",
            "cnc_ssh_username": "operator",
        },
    )
    assert saved.status_code == 200

    payload = client.get("/api/debug/cnc").json()

    assert payload["connected"] is True
    assert payload["health"]["enabled"] is True
    assert payload["motion"]["distance_to_go"] == 0.1
    assert payload["coordinates"]["g5x_index"] == 1
    assert payload["program_execution"]["m_codes"] == ["M5"]
    assert payload["spindle_details"]["feedback_speed"] == 4999
    assert payload["probe"]["tripped"] is False
    assert payload["tooling"]["prepared_pocket"] == 3
    assert payload["production"]["m30_a"] == 12
    assert payload["io"]["digital_inputs"] == [False, True]


def test_cnc_io_labels_are_read_from_pathpilot_hal_map(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.service.read_linuxcnc_io_labels",
        lambda *args: {
            "digital_inputs": {"17": "atc-tray-in"},
            "digital_outputs": {"36": "chip-conveyor-enable"},
            "analog_inputs": {"6": "atc-slot"},
            "analog_outputs": {"20": "chip-conveyor-active-time"},
        },
    )
    board = client.get("/api/board").json()
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "cnc_telemetry_enabled": True,
            "cnc_host": "tormach",
            "cnc_ssh_username": "operator",
        },
    )
    assert saved.status_code == 200

    payload = client.get("/api/debug/cnc/io-labels").json()

    assert payload["connected"] is True
    assert payload["labels"]["digital_inputs"]["17"] == "atc-tray-in"
    assert payload["labels"]["digital_outputs"]["36"] == "chip-conveyor-enable"


def test_cnc_connection_test_requires_connection_details(client: TestClient) -> None:
    response = client.post("/api/debug/cnc/test", json={"host": "", "username": "operator"})

    assert response.status_code == 422


def test_initial_board(client: TestClient) -> None:
    board = client.get("/api/board").json()

    assert board["revision"] == 0
    assert board["pallets"] == []
    assert board["settings"]["weight_unit"] == "lb"
    assert board["settings"]["pool_slot_count"] == 16
    assert board["settings"]["debug_menu_enabled"] is False
    assert board["settings"]["machine_state"] == "idle"
    assert board["settings"]["robot_connection_mode"] == "simulated"
    assert board["settings"]["robot_host"] == ""
    assert board["settings"]["robot_port"] == 30004
    assert board["settings"]["robot_poll_hz"] == 10
    assert board["settings"]["robot_timeout_seconds"] == 1.0
    assert board["settings"]["program_extensions"] == [
        ".nc",
        ".tap",
        ".gcode",
        ".cnc",
        ".urp",
    ]
    assert board["settings"]["manual_io_control_enabled"] is False
    assert board["settings"]["debug_mill_program_button_count"] == 4
    assert board["settings"]["workholding_library"] == []


def test_mill_debug_program_button_is_configured_and_runs(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    monkeypatch.setattr("app.main.cnc_debug_snapshot", lambda _: {"status": "updated"})
    configured = client.post(
        "/api/debug/mill-programs/configure",
        json={
            "expected_revision": board["revision"],
            "index": 0,
            "display_name": "Warm up",
            "filename": "/home/operator/gcode/Gcode/warmup.nc",
            "color": "green",
        },
    )
    assert configured.status_code == 200
    updated = client.get("/api/board").json()
    assert updated["settings"]["debug_mill_program_button_count"] == 4

    monkeypatch.setattr("app.service.run_linuxcnc_program", lambda *args: {"accepted": True})
    response = client.post(
        "/api/debug/mill-programs/run",
        json={"expected_revision": updated["revision"], "index": 0},
    )
    assert response.status_code == 409

    enabled = client.put(
        "/api/settings",
        json={
            "expected_revision": updated["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "cnc_telemetry_enabled": True,
            "cnc_host": "tormach",
            "cnc_ssh_username": "operator",
        },
    )
    assert enabled.status_code == 200
    response = client.post(
        "/api/debug/mill-programs/run",
        json={"expected_revision": enabled.json()["board"]["revision"], "index": 0},
    )
    assert response.status_code == 200


def test_workholding_library_is_normalized_and_persisted(client: TestClient) -> None:
    board = client.get("/api/settings").json()

    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "workholding_library": ["Soft jaws", "  Vise  ", "soft JAWS", "", "Fixture plate"],
        },
    )

    assert response.status_code == 200
    assert response.json()["board"]["settings"]["workholding_library"] == [
        "Soft jaws",
        "Vise",
        "Fixture plate",
    ]
    assert client.get("/api/settings").json()["settings"]["workholding_library"] == [
        "Soft jaws",
        "Vise",
        "Fixture plate",
    ]


def test_settings_update_persists_manual_io_control(client: TestClient) -> None:
    board = client.get("/api/settings").json()

    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "debug_menu_enabled": board["settings"]["debug_menu_enabled"],
            "manual_io_control_enabled": True,
            "robot_connection_mode": board["settings"]["robot_connection_mode"],
            "robot_host": board["settings"]["robot_host"],
            "robot_port": board["settings"]["robot_port"],
            "robot_poll_hz": board["settings"]["robot_poll_hz"],
            "robot_timeout_seconds": board["settings"]["robot_timeout_seconds"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["board"]["settings"]["manual_io_control_enabled"] is True
    assert client.get("/api/settings").json()["settings"]["manual_io_control_enabled"] is True


def test_legacy_settings_save_does_not_reset_manual_io_control(client: TestClient) -> None:
    board = client.get("/api/settings").json()
    enabled = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "manual_io_control_enabled": True,
        },
    ).json()["board"]

    response = client.put(
        "/api/settings",
        json={
            "expected_revision": enabled["revision"],
            "source_folder": enabled["settings"]["source_folder"],
            "program_extensions": enabled["settings"]["program_extensions"],
            "weight_unit": enabled["settings"]["weight_unit"],
            "pool_slot_count": enabled["settings"]["pool_slot_count"],
        },
    )

    assert response.status_code == 200
    assert response.json()["board"]["settings"]["manual_io_control_enabled"] is True


def test_dashboard_reports_pending_atc_telemetry(client: TestClient) -> None:
    dashboard = client.get("/api/dashboard")

    assert dashboard.status_code == 200
    assert dashboard.json()["atc_tools"] == []
    assert dashboard.json()["atc_source"] == "Mill telemetry is not connected yet."


def test_pallet_location_positions_persist(client: TestClient) -> None:
    board = client.get("/api/board").json()
    locations = [
        {"slot": slot, "x_mm": slot * 10, "y_mm": slot * 20, "z_mm": slot * 30}
        for slot in range(1, 17)
    ]
    locations[4] = {"slot": 5, "x_mm": 50.12349, "y_mm": 100.1235, "z_mm": 150.12351}
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "pool_locations": locations,
            "on_deck_location": {"x_mm": 1.98765, "y_mm": 2.12345, "z_mm": 3.55555},
            "dripping_location": {"x_mm": 4, "y_mm": 5, "z_mm": 6},
            "robot_mill_load_unload": {"name": "Mill load/unload", "x_mm": 10.12349, "y_mm": 20.1235, "z_mm": 30.12351, "rx_rad": 0.12349, "ry_rad": 0.1235, "rz_rad": 0.12351},
            "robot_mill_safe_entry_exit": {"name": "Mill safe entry/exit", "x_mm": 40, "y_mm": 50, "z_mm": 60, "rx_rad": 0.4, "ry_rad": 0.5, "rz_rad": 0.6},
            "mill_load_unload_g53": {"x_in": 7, "y_in": 8, "z_in": 9},
        },
    )

    assert response.status_code == 200
    settings = client.get("/api/settings").json()["settings"]
    assert settings["pool_locations"][4] == {"slot": 5, "x_mm": 50.123, "y_mm": 100.124, "z_mm": 150.124}
    assert settings["on_deck_location"] == {"x_mm": 1.988, "y_mm": 2.123, "z_mm": 3.556}
    assert settings["dripping_location"] == {"x_mm": 4.0, "y_mm": 5.0, "z_mm": 6.0}
    assert settings["robot_mill_load_unload"] == {"name": "Mill load/unload", "x_mm": 10.123, "y_mm": 20.124, "z_mm": 30.124, "rx_rad": 0.123, "ry_rad": 0.124, "rz_rad": 0.124}
    assert settings["robot_mill_safe_entry_exit"] == {"name": "Mill safe entry/exit", "x_mm": 40.0, "y_mm": 50.0, "z_mm": 60.0, "rx_rad": 0.4, "ry_rad": 0.5, "rz_rad": 0.6}
    assert settings["mill_load_unload_g53"] == {"x_in": 7.0, "y_in": 8.0, "z_in": 9.0}


def test_robot_telemetry_refresh_retains_last_good_snapshot_on_brief_failure(monkeypatch) -> None:
    key = ("192.0.2.80", 30003, 10, 1.0)
    healthy = {"connected": True, "connection_label": "Live realtime fallback"}
    service._ROBOT_TELEMETRY_CACHE[key] = (100.0, healthy, None, 100.0)
    monkeypatch.setattr(service.time, "monotonic", lambda: 105.0)
    monkeypatch.setattr(
        service,
        "read_robot_snapshot",
        lambda *args: (_ for _ in ()).throw(service.RobotTelemetryError("brief packet gap")),
    )

    completed = service.Event()
    service._refresh_robot_telemetry(key, key, completed)

    cached = service._ROBOT_TELEMETRY_CACHE[key]
    assert cached[1] == healthy
    assert cached[2] == "brief packet gap"
    assert cached[3] == 100.0
    assert completed.is_set()
    service._ROBOT_TELEMETRY_CACHE.pop(key, None)


def test_debug_robot_io_snapshot_defaults_to_simulated(client: TestClient) -> None:
    snapshot = client.get("/api/debug/robot-io").json()

    assert snapshot["connected"] is True
    assert snapshot["machine_state"] == "idle"
    assert snapshot["summary"]["queue_count"] == 0
    assert snapshot["summary"]["pool_open_positions"] == 16
    assert snapshot["source"] == "simulated"
    assert snapshot["robot"]["mode"] == "simulated"
    assert snapshot["digital_input_groups"][0]["rows"][0]["writable"] is False


def test_operator_can_reset_robot_connection_when_idle(client: TestClient, monkeypatch) -> None:
    resets = []
    monkeypatch.setattr(service, "reset_robot_connections", lambda: resets.append(True))

    response = client.post("/api/debug/robot-io/retry", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "retrying"
    assert resets == [True]


def test_operator_can_clear_recoverable_robot_fault_when_idle(client: TestClient, monkeypatch) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        session.commit()
        revision = settings.revision
    recoveries = []
    monkeypatch.setattr(
        service,
        "clear_robot_fault",
        lambda *args: recoveries.append(args) or {
            "action": "protective_stop_unlocked",
            "message": "Protective stop release was accepted.",
        },
    )

    rejected = client.post(
        "/api/debug/robot-fault/clear",
        json={"expected_revision": revision, "confirmed": False},
    )
    assert rejected.status_code == 422
    assert recoveries == []

    response = client.post(
        "/api/debug/robot-fault/clear",
        json={"expected_revision": revision, "confirmed": True},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "protective_stop_unlocked"
    assert recoveries == [("192.168.0.10", 1.0)]


def test_current_robot_pose_converts_rtde_translation_to_millimeters(
    client: TestClient,
    monkeypatch,
) -> None:
    board = client.get("/api/settings").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
        },
    )
    assert response.status_code == 200

    monkeypatch.setattr(
        "app.service.robot_io_snapshot",
        lambda session: {
            "connected": True,
            "timestamp": "2026-07-16T12:00:00+00:00",
            "tcp_detail_rows": [
                {"actual_pose": value}
                for value in (0.123456, -0.234567, 0.345678, 0.1, -0.2, 0.3)
            ],
            "joint_detail_rows": [
                {"actual_position": value}
                for value in (0.1, -1.2, 1.4, -1.6, -1.5, 0.2)
            ],
        },
    )

    pose = client.get("/api/debug/robot-pose")

    assert pose.status_code == 200
    assert pose.json() == {
        "x_mm": 123.456,
        "y_mm": -234.567,
        "z_mm": 345.678,
        "rx_rad": 0.1,
        "ry_rad": -0.2,
        "rz_rad": 0.3,
        "joints_rad": [0.1, -1.2, 1.4, -1.6, -1.5, 0.2],
        "timestamp": "2026-07-16T12:00:00+00:00",
    }


def test_current_robot_pose_requires_physical_mode(client: TestClient) -> None:
    response = client.get("/api/debug/robot-pose")

    assert response.status_code == 409
    assert response.json()["detail"] == "Current robot pose is only available in physical robot mode."


def test_debug_robot_io_snapshot_uses_live_robot_reader_when_configured(
    client: TestClient,
    monkeypatch,
) -> None:
    board = client.get("/api/board").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "debug_menu_enabled": False,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 5,
            "robot_timeout_seconds": 1.5,
        },
    )
    assert response.status_code == 200

    def fake_reader(host: str, port: int, poll_hz: int, timeout_seconds: float) -> dict:
        assert host == "192.168.0.10"
        assert port == 30004
        assert poll_hz == 5
        assert timeout_seconds == 1.5
        return {
            "timestamp": "2026-07-07T00:00:00+00:00",
            "source": "robot",
            "connected": True,
            "connection_label": "Live RTDE",
            "robot": {
                "mode": "physical",
                "host": host,
                "port": port,
                "controller_version": "5.14.0.0",
                "recipe_fields": ["timestamp", "actual_digital_input_bits"],
            },
            "digital_input_groups": [
                {
                    "title": "Standard inputs",
                    "rows": [{"channel": "DI0", "index": 0, "bit": 0, "value": True, "writable": False, "direction": "input", "bank": "standard"}],
                }
            ],
            "digital_output_groups": [],
            "analog_inputs": [],
            "analog_outputs": [],
            "state_rows": [{"label": "Robot mode", "value": 7}],
            "pose_rows": [],
            "tcp_speed_rows": [],
            "joint_rows": [],
            "extra_actual_rows": [],
            "notes": "Reading live robot telemetry over RTDE port 30004.",
        }

    monkeypatch.setattr("app.service.read_robot_snapshot", fake_reader)

    snapshot = client.get("/api/debug/robot-io").json()

    assert snapshot["connected"] is True
    assert snapshot["source"] == "robot"
    assert snapshot["robot"]["mode"] == "physical"
    assert snapshot["robot"]["controller_version"] == "5.14.0.0"
    assert snapshot["digital_input_groups"][0]["rows"][0]["value"] is True


def test_database_persists_across_application_restart(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'persistent.db').as_posix()}"
    with TestClient(create_app(database_url, external_services=False)) as first_client:
        response = first_client.post(
            "/api/pallets",
            json={
                "expected_revision": 0,
                "workholding": "Fixture",
                "weight_kg": 10,
                "content_status": "empty",
                "program_path": None,
            },
        )
        assert response.status_code == 201

    with TestClient(create_app(database_url, external_services=False)) as second_client:
        board = second_client.get("/api/board").json()
        assert board["revision"] == 1
        assert len(board["pallets"]) == 1
        assert board["pallets"][0]["name"] in service.PALLET_NAMES
        assert board["pallets"][0]["pool_slot_number"] == 1


def test_simulated_run_mode_processes_queue_with_step_confirmations(client: TestClient, tmp_path, monkeypatch) -> None:
    command_order = []
    start_transfer = service.start_mill_pallet_transfer

    def track_transfer(session, payload, automated=False):
        command_order.append(f"robot_{payload.operation}")
        return start_transfer(session, payload, automated)

    monkeypatch.setattr(service, "start_mill_pallet_transfer", track_transfer)
    monkeypatch.setattr(
        service,
        "_run_mode_load_position_cycle",
        lambda session_factory, run_token=None: command_order.append("mill_load_position") or True,
    )
    monkeypatch.setattr(
        service,
        "_run_mode_machine_cycle",
        lambda session_factory, pallet_id, run_token=None: command_order.append("mill_assigned_program") or True,
    )
    monkeypatch.setattr(service, "pallet_program_files", lambda session: ["job.nc"])
    gcode_dir = tmp_path / "Gcode"
    gcode_dir.mkdir()
    (gcode_dir / "job.nc").write_text("G0 X0\nM30\n", encoding="ascii")
    board = client.get("/api/settings").json()
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": str(tmp_path),
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
        },
    ).json()["board"]
    created = client.post(
        "/api/pallets",
        json={
            "expected_revision": saved["revision"],
            "workholding": "Fixture",
            "weight_kg": 10,
            "content_status": "raw_stock",
            "program_path": "job.nc",
        },
    ).json()
    pallet = created["pallets"][0]
    queued = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": created["revision"], "queue_index": 0},
    ).json()

    started = client.post(
        "/api/run-mode/start",
        json={"expected_revision": queued["revision"], "safety_confirm": True},
    )
    assert started.status_code == 202

    for expected_action in ("loading", "machining", "unloading"):
        pending = wait_for_run_state(client, "waiting_confirmation")
        assert pending["run_mode"]["pending_action"] == expected_action
        confirmed = client.post(
            "/api/run-mode/confirm",
            json={
                "expected_revision": pending["revision"],
                "token": pending["run_mode"]["confirmation_token"],
                "approved": True,
            },
        )
        assert confirmed.status_code == 200

    completed = wait_for_run_state(client, "complete")
    result = completed["pallets"][0]
    assert completed["run_mode"]["enabled"] is False
    assert result["queue_position"] is None
    assert result["location"] == "pool"
    assert result["pool_slot_number"] == 1
    assert result["content_status"] == "complete_parts"
    assert command_order == [
        "mill_load_position",
        "robot_load",
        "mill_assigned_program",
        "mill_load_position",
        "robot_unload",
    ]


def test_run_mode_cnc_cycle_starts_program_and_waits_for_idle(client: TestClient, monkeypatch) -> None:
    calls = []
    telemetry = iter((
        {"interp_state": 1},  # Baseline read before starting the program.
        {"interp_state": 2},
        {"interp_state": 1},
    ))
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.commit()

    monkeypatch.setattr(
        service,
        "run_linuxcnc_program",
        lambda *args: calls.append(args) or {"accepted": True, "started": True, "interp_state": 2},
    )
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *args: next(telemetry))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    completed = service._run_mode_cnc_cycle(
        client.app.state.session_factory,
        "/home/operator/gcode/Gcode/mongo_mill_load_position.nc",
        cycle_label="Loading position",
        timeout_seconds=10,
    )

    assert completed is True
    assert len(calls) == 1
    assert calls[0][0] == "tormach"
    assert calls[0][5] == "/home/operator/gcode/Gcode/mongo_mill_load_position.nc"


def test_cnc_cycle_refuses_idle_without_confirmed_start(client: TestClient, monkeypatch) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.commit()

    monkeypatch.setattr(
        service,
        "read_linuxcnc_cycle_state",
        lambda *args: {"interp_state": 1, "enabled": True, "interpreter_error": 0},
    )
    monkeypatch.setattr(
        service,
        "run_linuxcnc_program",
        lambda *args: {"accepted": True, "started": False, "interp_state": 1},
    )

    with pytest.raises(cnc_linuxcnc.CncProgramFault, match="did not confirm"):
        service._run_cnc_cycle(
            settings,
            "/home/operator/gcode/Gcode/job.nc",
            cycle_label="The assigned program",
            timeout_seconds=10,
        )


def test_cnc_cycle_treats_alarm_as_failure_before_idle(client: TestClient, monkeypatch) -> None:
    telemetry = iter((
        {"interp_state": 1, "enabled": True, "interpreter_error": 0},
        {"interp_state": 2, "enabled": True},
        {
            "interp_state": 1,
            "enabled": True,
            "error_messages": [{"kind": 11, "text": "Tool breakage check failed", "is_error": True}],
        },
    ))
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.commit()

    monkeypatch.setattr(service, "run_linuxcnc_program", lambda *args: {"accepted": True, "started": True})
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *args: next(telemetry))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    with pytest.raises(cnc_linuxcnc.CncProgramFault, match="Tool breakage check failed"):
        service._run_cnc_cycle(
            settings,
            "/home/operator/gcode/Gcode/job.nc",
            cycle_label="The assigned program",
            timeout_seconds=10,
        )


def test_cnc_cycle_reports_pathpilot_motion_lockout() -> None:
    detail = service._cnc_cycle_fault_detail({
        "enabled": True,
        "jog_lockout_configured": True,
        "jog_locked_out": True,
        "motion_stop_lockout_configured": True,
        "motion_stop_locked_out": False,
    })

    assert detail is not None
    assert "Restore the probe and press Reset on PathPilot" in detail


def test_cnc_cycle_ignores_unchanged_idle_interpreter_error_after_success(client: TestClient, monkeypatch) -> None:
    telemetry = iter((
        {"interp_state": 1, "enabled": True, "interpreter_error": 5},
        {"interp_state": 2, "enabled": True, "interpreter_error": 5},
        {"interp_state": 1, "enabled": True, "interpreter_error": 5},
    ))
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.commit()

    monkeypatch.setattr(service, "run_linuxcnc_program", lambda *args: {"accepted": True, "started": True})
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", lambda *args: next(telemetry))

    assert service._run_cnc_cycle(
        settings,
        "/home/operator/gcode/Gcode/job.nc",
        cycle_label="The assigned program",
        timeout_seconds=10,
    ) is True


def test_cnc_cycle_recovers_from_transient_telemetry_loss_without_restarting_program(client: TestClient, monkeypatch) -> None:
    calls = []
    reports = []
    telemetry = iter((
        {"interp_state": 1, "enabled": True, "interpreter_error": 0},
        service.CncTelemetryError("SSH timed out"),
        {"interp_state": 2, "enabled": True, "interpreter_error": 0},
        {"interp_state": 1, "enabled": True, "interpreter_error": 0},
    ))
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.commit()

    def read_state(*_args):
        item = next(telemetry)
        if isinstance(item, Exception):
            raise item
        return item

    clock = iter(range(100, 140))
    monkeypatch.setattr(service, "run_linuxcnc_program", lambda *args: calls.append(args) or {"accepted": True, "started": True})
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", read_state)
    monkeypatch.setattr(service.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    assert service._run_cnc_cycle(
        settings,
        "/home/operator/gcode/Gcode/job.nc",
        cycle_label="The assigned program",
        timeout_seconds=60,
        status_report=lambda state, detail: reports.append((state, detail)),
    ) is True
    assert len(calls) == 1
    assert reports[0][0] == "telemetry_unavailable"
    assert reports[-1][0] == "telemetry_restored"


def test_run_mode_alarm_prompts_before_retrying_same_program(client: TestClient, monkeypatch) -> None:
    attempts = 0
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_state = "machining"
        settings.run_mode_current_pallet_id = "pallet-in-mill"
        session.commit()

    def run_cycle(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise cnc_linuxcnc.CncProgramFault("Tool breakage check failed")
        return True

    monkeypatch.setattr(service, "_run_cnc_cycle", run_cycle)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            service._run_mode_cnc_cycle,
            client.app.state.session_factory,
            "/home/operator/gcode/Gcode/job.nc",
            cycle_label="The assigned program",
        )
        pending = wait_for_run_state(client, "waiting_confirmation")
        assert pending["run_mode"]["pending_action"] == "retry_cnc_program"
        assert "Tool breakage check failed" in pending["run_mode"]["detail"]
        assert pending["run_mode"]["current_pallet_id"] == "pallet-in-mill"
        confirmed = client.post(
            "/api/run-mode/confirm",
            json={
                "expected_revision": pending["revision"],
                "token": pending["run_mode"]["confirmation_token"],
                "approved": True,
            },
        )
        assert confirmed.status_code == 200
        assert result.result(timeout=3) is True

    board = client.get("/api/board").json()
    assert attempts == 2
    assert board["run_mode"]["enabled"] is True
    assert board["run_mode"]["alert"] is None


def test_operator_can_stop_after_cnc_alarm_without_moving_pallet(client: TestClient) -> None:
    token = "11111111-1111-4111-8111-111111111111"
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_state = "waiting_confirmation"
        settings.run_mode_detail = "Tool breakage check failed."
        settings.run_mode_pending_action = "retry_cnc_program"
        settings.run_mode_confirmation_token = token
        settings.run_mode_current_pallet_id = "pallet-in-mill"
        settings.run_mode_return_slot = 4
        settings.run_mode_alert = "Tool breakage check failed."
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/run-mode/confirm",
        json={"expected_revision": revision, "token": token, "approved": False},
    )

    assert response.status_code == 200
    run = response.json()["run_mode"]
    assert run["enabled"] is False
    assert run["state"] == "stopped"
    assert run["current_pallet_id"] == "pallet-in-mill"
    assert run["return_slot"] == 4
    assert "pallet remains in place" in run["detail"]
    assert run["alert"] == "Tool breakage check failed."


def test_physical_run_mode_requires_live_cnc_before_enabling(client: TestClient, monkeypatch) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.pallet_motion_enabled = True
        settings.robot_host = "mongo"
        settings.cnc_telemetry_enabled = True
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        pallet = Pallet(
            id="batch-preflight-pallet",
            name="Batch Preflight",
            workholding="Fixture",
            weight_kg=1.0,
            content_status="raw_stock",
            program_path="job.nc",
            location="pool",
            pool_slot_number=1,
            queue_position=1,
        )
        session.add(pallet)
        session.commit()
        revision = settings.revision

    monkeypatch.setattr(service, "_assert_motion_ready", lambda *args: None)

    def unavailable(*args):
        raise service.CncTelemetryError("SSH timed out")

    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", unavailable)
    monkeypatch.setattr(service, "_RUN_MODE_PRE_DISPATCH_RECOVERY_ATTEMPTS", 0)
    response = client.post(
        "/api/run-mode/start",
        json={"expected_revision": revision, "safety_confirm": True},
    )

    assert response.status_code == 202
    failed = wait_for_run_state(client, "faulted")
    assert "Live CNC telemetry is unavailable" in failed["run_mode"]["detail"]
    assert failed["run_mode"]["enabled"] is False


def test_run_mode_start_automatically_retries_pre_dispatch_telemetry(client: TestClient, monkeypatch) -> None:
    reads = []
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_start_request_id = "startup-retry-token"
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        session.add(Pallet(
            id="startup-retry-pallet",
            name="Startup Retry",
            workholding="Vise",
            weight_kg=1,
            content_status="raw_stock",
            program_path="job.nc",
            location="pool",
            pool_slot_number=1,
            queue_position=1,
        ))
        session.commit()

    def read_state(*_args):
        reads.append(True)
        if len(reads) == 1:
            raise service.CncTelemetryError("SSH timed out")
        return {"interp_state": 1, "enabled": True, "estop": False}

    monkeypatch.setattr(service, "_assert_motion_ready", lambda *args: None)
    monkeypatch.setattr(service, "_assert_pool_motion_position_configured", lambda *args: None)
    monkeypatch.setattr(service, "_assert_run_mode_files_ready", lambda *args: None)
    monkeypatch.setattr(service, "read_linuxcnc_cycle_state", read_state)
    monkeypatch.setattr(service, "reset_robot_connections", lambda: None)
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    assert service._prepare_run_mode(client.app.state.session_factory, "startup-retry-token") is True
    assert len(reads) == 2
    assert client.get("/api/board").json()["run_mode"]["state"] == "starting"


def test_run_mode_retries_cnc_pre_dispatch_telemetry_without_starting_program(client: TestClient, monkeypatch) -> None:
    attempts = []
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        session.commit()

    def run_cycle(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise service.CncPreDispatchTelemetryError("PathPilot telemetry unavailable")
        return True

    monkeypatch.setattr(service, "_run_cnc_cycle", run_cycle)
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    assert service._run_mode_cnc_cycle(
        client.app.state.session_factory,
        "/home/operator/gcode/Gcode/job.nc",
        cycle_label="The assigned program",
    ) is True
    assert len(attempts) == 2


def test_mill_results_archive_uses_program_name_and_utc_timestamp(client: TestClient, monkeypatch) -> None:
    copied = {}
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.cnc_host = "tormach"
        settings.cnc_ssh_password = "secret"
        settings.mill_file_directory = "/home/operator/gcode"
        settings.mill_results_source_path = "/home/operator/gcode/RESULTS.TXT"
        settings.mill_results_archive_directory = "/home/operator/gcode/Results"
        session.commit()

    monkeypatch.setattr(
        service,
        "remote_file_signature",
        lambda **kwargs: {"size": 20, "mtime": 2, "sha256": "new"},
    )

    def fake_copy(**kwargs):
        copied.update(kwargs)
        return f"{kwargs['destination_directory']}/{kwargs['destination_name']}"

    monkeypatch.setattr(service, "copy_remote_file_as", fake_copy)
    archived = service._archive_mill_results(
        settings,
        "customer/jobs/Op 1 - Top.nc",
        {"size": 10, "mtime": 1, "sha256": "old"},
    )

    assert copied["source"] == "/home/operator/gcode/RESULTS.TXT"
    assert copied["destination_directory"] == "/home/operator/gcode/Results"
    assert copied["destination_name"].startswith("Op_1_-_Top__")
    assert copied["destination_name"].endswith("Z__RESULTS.TXT")
    assert archived == f"/home/operator/gcode/Results/{copied['destination_name']}"


def test_mill_results_paths_allow_pathpilot_results_beside_gcode(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.mill_file_directory = "/home/operator/gcode/Gcode"
        settings.mill_results_source_path = "/home/operator/gcode/RESULTS.TXT"
        settings.mill_results_archive_directory = "/home/operator/gcode/results"

        assert service._mill_results_paths(settings) == (
            "/home/operator/gcode/RESULTS.TXT",
            "/home/operator/gcode/results",
        )
        assert service._mill_results_file_connection(settings)["directory"] == "/home/operator/gcode"


def test_mill_results_archive_skips_unchanged_file(client: TestClient, monkeypatch) -> None:
    signature = {"size": 20, "mtime": 2, "sha256": "same"}
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
    monkeypatch.setattr(service, "remote_file_signature", lambda **kwargs: signature)

    assert service._archive_mill_results(settings, "job.nc", signature) is None


def test_run_mode_machine_cycle_archives_fresh_results(client: TestClient, monkeypatch) -> None:
    signatures = iter((
        {"size": 10, "mtime": 1, "sha256": "before"},
        {"size": 20, "mtime": 2, "sha256": "after"},
    ))
    copied = []
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_password = "secret"
        settings.mill_results_archiving_enabled = True
        pallet = Pallet(
            id="results-cycle-pallet",
            name="Results Cycle",
            workholding="Fixture",
            weight_kg=1.0,
            content_status="raw_stock",
            program_path="parts/finish.nc",
            location="machine",
        )
        session.add(pallet)
        session.commit()

    monkeypatch.setattr(service, "remote_file_signature", lambda **kwargs: next(signatures))
    monkeypatch.setattr(service, "_run_mode_cnc_cycle", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        service,
        "copy_remote_file_as",
        lambda **kwargs: copied.append(kwargs) or f"{kwargs['destination_directory']}/{kwargs['destination_name']}",
    )

    completed = service._run_mode_machine_cycle(client.app.state.session_factory, pallet.id)

    assert completed is True
    assert len(copied) == 1
    assert copied[0]["destination_name"].startswith("finish__")
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        assert settings.run_mode_state == "results_archived"
        assert "finish__" in settings.run_mode_detail


def test_unchanged_results_do_not_create_run_mode_alert(client: TestClient, monkeypatch) -> None:
    signature = {"size": 20, "mtime": 2, "sha256": "same"}
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_password = "secret"
        settings.mill_results_archiving_enabled = True
        pallet = Pallet(
            id="unchanged-results-pallet",
            name="Unchanged Results",
            workholding="Fixture",
            weight_kg=1.0,
            content_status="raw_stock",
            program_path="finish.nc",
            location="machine",
        )
        session.add(pallet)
        session.commit()

    monkeypatch.setattr(service, "remote_file_signature", lambda **kwargs: signature)
    monkeypatch.setattr(service, "_run_mode_cnc_cycle", lambda *args, **kwargs: True)

    assert service._run_mode_machine_cycle(client.app.state.session_factory, pallet.id) is True
    board = client.get("/api/board").json()
    assert board["run_mode"]["enabled"] is True
    assert board["run_mode"]["state"] == "results_unchanged"
    assert board["run_mode"]["alert"] is None


def test_results_archive_failure_alerts_without_stopping_run_mode(client: TestClient, monkeypatch) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_password = "secret"
        settings.mill_results_archiving_enabled = True
        pallet = Pallet(
            id="missing-results-pallet",
            name="Missing Results",
            workholding="Fixture",
            weight_kg=1.0,
            content_status="raw_stock",
            program_path="finish.nc",
            location="machine",
        )
        session.add(pallet)
        session.commit()

    monkeypatch.setattr(service, "remote_file_signature", lambda **kwargs: None)
    monkeypatch.setattr(service, "_run_mode_cnc_cycle", lambda *args, **kwargs: True)

    assert service._run_mode_machine_cycle(client.app.state.session_factory, pallet.id) is True
    board = client.get("/api/board").json()
    assert board["run_mode"]["enabled"] is True
    assert board["run_mode"]["state"] == "results_archive_warning"
    assert "Production continued normally" in board["run_mode"]["alert"]

    dismissed = client.post("/api/run-mode/alert/dismiss", json={}).json()
    assert dismissed["run_mode"]["alert"] is None
    assert dismissed["run_mode"]["enabled"] is True


def test_stale_run_mode_fault_can_be_cleared_without_changing_queue(client: TestClient) -> None:
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": 0,
            "workholding": "Vise",
            "weight_kg": 1,
            "content_status": "raw_stock",
        },
    ).json()
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"], "queue_index": 0},
    ).json()
    expected_queue_position = board["pallets"][0]["queue_position"]
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = False
        settings.run_mode_state = "faulted"
        settings.run_mode_detail = "Old controller communication fault."
        session.commit()
        revision = settings.revision

    cleared = client.post(
        "/api/run-mode/status/clear",
        json={"expected_revision": revision},
    )

    assert cleared.status_code == 200
    result = cleared.json()
    assert result["run_mode"]["state"] == "idle"
    assert result["run_mode"]["detail"] == ""
    assert result["pallets"][0]["queue_position"] == expected_queue_position


def test_active_run_mode_status_cannot_be_cleared(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        settings.run_mode_state = "faulted"
        settings.run_mode_detail = "Current fault."
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/run-mode/status/clear",
        json={"expected_revision": revision},
    )

    assert response.status_code == 409
    assert "Stop Run Mode" in response.json()["detail"]


def test_faulted_run_can_retry_only_robot_unload_for_pallet_in_mill(
    client: TestClient, monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        "app.main.execute_run_mode_recovery",
        lambda session_factory, token, strategy: calls.append((token, strategy)),
    )
    with client.app.state.session_factory() as session:
        session.add(Pallet(
            id="recovery-pallet",
            name="Recovery Pallet",
            workholding="Vise",
            weight_kg=1,
            content_status="raw_stock",
            program_path="job.nc",
            location="machine",
            return_pool_slot_number=2,
        ))
        settings = service.get_settings(session)
        settings.run_mode_enabled = False
        settings.run_mode_state = "faulted"
        settings.run_mode_detail = "Robot telemetry unavailable before unload."
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/run-mode/recover",
        json={"expected_revision": revision, "strategy": "retry_robot_only"},
    )

    assert response.status_code == 202
    run = response.json()["run_mode"]
    assert run["enabled"] is True
    assert run["state"] == "recovery_requested"
    assert run["current_pallet_id"] == "recovery-pallet"
    deadline = time.monotonic() + 1
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls and calls[0][1] == "retry_robot_only"


def test_run_recovery_requires_a_pallet_recorded_in_the_mill(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.run_mode_enabled = False
        settings.run_mode_state = "faulted"
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/run-mode/recover",
        json={"expected_revision": revision, "strategy": "retry_robot_only"},
    )

    assert response.status_code == 409
    assert "pallet currently marked in the mill" in response.json()["detail"]


def test_manual_mill_return_updates_only_the_schedule_record(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        pallet = Pallet(
            id="manual-return-pallet",
            name="Manual Return",
            workholding="Vise",
            weight_kg=1,
            content_status="complete_parts",
            location="machine",
            return_pool_slot_number=4,
        )
        session.add(pallet)
        settings = service.get_settings(session)
        settings.machine_state = "running"
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/pallets/manual-return-pallet/manual-return-to-pool",
        json={"expected_revision": revision},
    )

    assert response.status_code == 200
    pallet = next(item for item in response.json()["pallets"] if item["id"] == "manual-return-pallet")
    assert pallet["location"] == "pool"
    assert pallet["pool_slot_number"] == 4
    assert pallet["return_pool_slot_number"] is None
    assert response.json()["settings"]["machine_state"] == "idle"


def test_manual_mill_return_is_blocked_during_run_mode(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(Pallet(
            id="manual-return-active-run",
            name="Manual Return Active",
            workholding="Vise",
            weight_kg=1,
            content_status="raw_stock",
            location="machine",
            return_pool_slot_number=1,
        ))
        settings = service.get_settings(session)
        settings.run_mode_enabled = True
        session.commit()
        revision = settings.revision

    response = client.post(
        "/api/pallets/manual-return-active-run/manual-return-to-pool",
        json={"expected_revision": revision},
    )

    assert response.status_code == 409
    assert "Stop Run Mode" in response.json()["detail"]


def test_stop_override_cancels_pending_run_start_before_any_motion(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        pallet = Pallet(id="cancel-start-pallet", name="Cancel Start", workholding="Vise", weight_kg=1, content_status="raw_stock", program_path="job.nc", location="pool", pool_slot_number=1, queue_position=1)
        session.add(pallet)
        session.commit()
        board = service.board_snapshot(session)
        token = service.start_run_mode(session, StartRunMode(
            expected_revision=board["revision"], request_id="run-start-cancel-test",
        ))
        settings = service.get_settings(session)
        assert token == "run-start-cancel-test"
        assert settings.run_mode_state == "start_requested"
        revision = settings.revision

    with client.app.state.session_factory() as session:
        service.stop_run_mode(session, revision)
        settings = service.get_settings(session)
        assert settings.run_mode_enabled is False
        assert settings.run_mode_state == "stopping"
        assert settings.run_mode_start_request_id == token

    service.execute_run_mode(client.app.state.session_factory, token)

    result = client.get("/api/board").json()
    assert result["run_mode"]["enabled"] is False
    assert result["run_mode"]["state"] == "stopped"
    with client.app.state.session_factory() as session:
        assert session.query(RobotMotion).count() == 0


def test_stop_request_never_aborts_an_active_cnc_program(client: TestClient, monkeypatch) -> None:
    aborts = []
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.cnc_host = "tormach"
        settings.cnc_ssh_username = "operator"
        settings.cnc_ssh_password = "secret"
        settings.run_mode_enabled = True
        settings.run_mode_state = "machining"
        session.commit()
        revision = settings.revision

    with client.app.state.session_factory() as session:
        service.stop_run_mode(session, revision)

    board = client.get("/api/board").json()
    assert board["run_mode"]["enabled"] is False
    assert "not aborted" in board["run_mode"]["detail"]
    assert aborts == []


def test_duplicate_run_start_request_does_not_start_a_second_worker(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        pallet = Pallet(id="duplicate-start-pallet", name="Duplicate Start", workholding="Vise", weight_kg=1, content_status="raw_stock", program_path="job.nc", location="pool", pool_slot_number=1, queue_position=1)
        session.add(pallet)
        session.commit()
        board = service.board_snapshot(session)
        token = service.start_run_mode(session, StartRunMode(
            expected_revision=board["revision"], request_id="run-start-duplicate-test",
        ))
        revision = service.get_settings(session).revision

    with client.app.state.session_factory() as session:
        duplicate = service.start_run_mode(session, StartRunMode(
            expected_revision=revision, request_id="run-start-duplicate-test",
        ))
        assert duplicate is None
        assert service.get_settings(session).run_mode_start_request_id == token


def test_relaunch_endpoint_queues_helper(client: TestClient, monkeypatch) -> None:
    started = {}

    def fake_queue() -> None:
        started["called"] = True

    monkeypatch.setattr("app.main.queue_backend_relaunch", fake_queue)

    response = client.post("/api/system/relaunch")

    assert response.status_code == 202
    assert response.json()["status"] == "relaunching"
    assert started == {"called": True}


def test_relaunch_is_blocked_during_robot_motion(client: TestClient, monkeypatch) -> None:
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": 0,
            "workholding": "Vise",
            "weight_kg": 1,
            "content_status": "raw_stock",
        },
    ).json()
    pallet = board["pallets"][0]
    with client.app.state.session_factory() as session:
        session.add(RobotMotion(
            id="active-motion",
            pallet_id=pallet["id"],
            operation="pick",
            source_slot=1,
            destination_slot=None,
            program_path="/programs/pick.script",
            status="running",
            retry_count=0,
            observed_busy=False,
            created_at="2026-01-01T00:00:00+00:00",
        ))
        session.commit()
    monkeypatch.setattr(
        "app.main.queue_backend_relaunch",
        lambda: (_ for _ in ()).throw(AssertionError("must not relaunch during motion")),
    )

    response = client.post("/api/system/relaunch")

    assert response.status_code == 409
    assert "active robot movement" in response.json()["detail"]


def test_settings_accept_partial_update_without_resetting_robot_connection(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "mongo"
        session.commit()

    response = client.put("/api/settings", json={"expected_revision": 0, "weight_unit": "kg"})

    assert response.status_code == 200
    saved = response.json()["board"]["settings"]
    assert saved["weight_unit"] == "kg"
    assert saved["robot_connection_mode"] == "physical"
    assert saved["robot_host"] == "mongo"
