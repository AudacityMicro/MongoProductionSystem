from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_pages(client: TestClient) -> None:
    response = client.get("/api/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["version"] == "0.3.0"
    assert isinstance(payload["process_id"], int)
    assert payload["started_at"]
    assert "Pallet schedule" in client.get("/").text
    assert "Robot readable I/O" in client.get("/debugging").text
    settings_page = client.get("/settings").text
    assert "Scheduling settings" in settings_page
    assert "Close and relaunch" in settings_page


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


def test_debug_robot_io_snapshot_defaults_to_unavailable(client: TestClient) -> None:
    snapshot = client.get("/api/debug/robot-io").json()

    assert snapshot["connected"] is False
    assert snapshot["source"] == "unavailable"
    assert snapshot["machine_state"] == "idle"
    assert snapshot["summary"]["queue_count"] == 0
    assert snapshot["summary"]["pool_open_positions"] == 16
    assert snapshot["source"] == "simulated"
    assert snapshot["robot"]["mode"] == "simulated"
    assert snapshot["digital_input_groups"][0]["rows"][0]["writable"] is True


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


def test_relaunch_endpoint_queues_helper(client: TestClient, monkeypatch) -> None:
    started = {}

    def fake_queue() -> None:
        started["called"] = True

    monkeypatch.setattr("app.main.queue_backend_relaunch", fake_queue)

    response = client.post("/api/system/relaunch")

    assert response.status_code == 202
    assert response.json()["status"] == "relaunching"
    assert started == {"called": True}
