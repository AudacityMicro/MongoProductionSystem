from fastapi.testclient import TestClient
import time

from app.main import create_app
from app.service import build_mill_load_position_program
from app import cnc_linuxcnc


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
    assert "Pallet schedule" in client.get("/").text
    assert "Debugging" in client.get("/debugging").text
    assert "Mongo controller" in client.get("/debugging").text
    assert "Tormach 1500MX / PathPilot" in client.get("/debugging").text
    settings_page = client.get("/settings").text
    assert "System settings" in settings_page
    assert "Close and relaunch" in settings_page
    assert "Workholding library" in settings_page
    assert 'id="workholding-options"' in client.get("/").text


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


def test_pathpilot_program_run_uses_pathpilot_cycle_start_signature(monkeypatch) -> None:
    captured = {}

    def fake_remote(host, port, username, password, timeout, remote_script, marker):
        captured["script"] = remote_script
        captured["marker"] = marker
        return {"accepted": True}

    monkeypatch.setattr(cnc_linuxcnc, "_read_remote_payload", fake_remote)
    result = cnc_linuxcnc.run_linuxcnc_program("mill", 22, "operator", "secret", 10, "/home/operator/gcode/Gcode/job.nc")

    assert result == {"accepted": True}
    assert captured["script"].index("command.program_close()") < captured["script"].index("command.program_open(filename)")
    assert 'active_axes = int(getattr(status, "axes", 0) or 0)' in captured["script"]
    assert 'axis_mask = int(getattr(status, "axis_mask", 0) or 0)' in captured["script"]
    assert 'command.auto(linuxcnc.AUTO_RUN, 1, linuxcnc.PREP_NONE, True, False)' in captured["script"]
    assert captured["marker"] == "MONGO_CNC_RUN="


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


def test_debug_robot_io_snapshot_defaults_to_simulated(client: TestClient) -> None:
    snapshot = client.get("/api/debug/robot-io").json()

    assert snapshot["connected"] is True
    assert snapshot["machine_state"] == "idle"
    assert snapshot["summary"]["queue_count"] == 0
    assert snapshot["summary"]["pool_open_positions"] == 16
    assert snapshot["source"] == "simulated"
    assert snapshot["robot"]["mode"] == "simulated"
    assert snapshot["digital_input_groups"][0]["rows"][0]["writable"] is False


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
    with TestClient(create_app(database_url)) as first_client:
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

    with TestClient(create_app(database_url)) as second_client:
        board = second_client.get("/api/board").json()
        assert board["revision"] == 1
        assert [item["name"] for item in board["pallets"]] == ["ABBA"]
        assert board["pallets"][0]["pool_slot_number"] == 1


def test_simulated_run_mode_processes_queue_with_step_confirmations(client: TestClient, tmp_path) -> None:
    (tmp_path / "job.nc").write_text("G0 X0\nM30\n", encoding="ascii")
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


def test_relaunch_endpoint_queues_helper(client: TestClient, monkeypatch) -> None:
    started = {}

    def fake_queue() -> None:
        started["called"] = True

    monkeypatch.setattr("app.main.queue_backend_relaunch", fake_queue)

    response = client.post("/api/system/relaunch")

    assert response.status_code == 202
    assert response.json()["status"] == "relaunching"
    assert started == {"called": True}
