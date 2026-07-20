from fastapi.testclient import TestClient
from pathlib import PurePosixPath


def create_pallet(client: TestClient, revision: int) -> dict:
    response = client.post(
        "/api/pallets",
        json={
            "expected_revision": revision,
            "workholding": "Debug fixture",
            "weight_kg": 12,
            "content_status": "raw_stock",
            "program_path": None,
        },
    )
    assert response.status_code == 201
    return response.json()


def enable_debug(client: TestClient, board: dict) -> dict:
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "debug_menu_enabled": True,
            "manual_io_control_enabled": True,
            "robot_connection_mode": board["settings"]["robot_connection_mode"],
            "robot_host": board["settings"]["robot_host"],
            "robot_port": board["settings"]["robot_port"],
            "robot_poll_hz": board["settings"]["robot_poll_hz"],
            "robot_timeout_seconds": board["settings"]["robot_timeout_seconds"],
        },
    )
    assert response.status_code == 200
    return response.json()["board"]


def test_debug_signals_require_debug_mode(client: TestClient) -> None:
    board = client.get("/api/board").json()

    response = client.post(
        "/api/debug/signals/error",
        json={"expected_revision": board["revision"]},
    )

    assert response.status_code == 403


def test_debug_machine_signal_lifecycle(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = enable_debug(client, board)
    pallet_id = board["pallets"][0]["id"]
    board = client.post(
        f"/api/pallets/{pallet_id}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "machine",
        },
    ).json()
    assert board["settings"]["machine_state"] == "running"

    board = client.post(
        "/api/debug/signals/error",
        json={"expected_revision": board["revision"]},
    ).json()
    assert board["settings"]["machine_state"] == "error"
    assert board["pallets"][0]["location"] == "machine"

    board = client.post(
        "/api/debug/signals/complete",
        json={"expected_revision": board["revision"]},
    ).json()
    pallet = board["pallets"][0]
    assert board["settings"]["machine_state"] == "idle"
    assert pallet["location"] == "pool"
    assert pallet["pool_slot_number"] == 1
    assert pallet["content_status"] == "complete_parts"

    board = client.post(
        f"/api/pallets/{pallet_id}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "machine",
        },
    ).json()
    board = client.post(
        "/api/debug/signals/out_of_spec",
        json={"expected_revision": board["revision"]},
    ).json()
    pallet = board["pallets"][0]
    assert board["settings"]["machine_state"] == "idle"
    assert pallet["location"] == "pool"
    assert pallet["content_status"] == "defective_parts"


def test_debug_completion_requires_machine_pallet(client: TestClient) -> None:
    board = enable_debug(client, client.get("/api/board").json())

    response = client.post(
        "/api/debug/signals/complete",
        json={"expected_revision": board["revision"]},
    )

    assert response.status_code == 409


def test_debug_completion_rejects_full_pool(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = enable_debug(client, board)
    first_id = board["pallets"][0]["id"]
    board = client.post(
        f"/api/pallets/{first_id}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "machine",
        },
    ).json()
    board = create_pallet(client, board["revision"])
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 1,
            "debug_menu_enabled": True,
            "robot_connection_mode": "simulated",
            "robot_host": "",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.0,
        },
    ).json()["board"]

    response = client.post(
        "/api/debug/signals/complete",
        json={"expected_revision": board["revision"]},
    )

    assert response.status_code == 409
    unchanged = client.get("/api/board").json()
    machine = next(item for item in unchanged["pallets"] if item["id"] == first_id)
    assert machine["location"] == "machine"
    assert machine["content_status"] == "raw_stock"


def test_debug_robot_io_snapshot_reflects_simulated_state(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = enable_debug(client, board)
    pallet = board["pallets"][0]

    board = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={
            "expected_revision": board["revision"],
            "queue_index": 0,
        },
    ).json()

    snapshot = client.get("/api/debug/robot-io").json()

    assert snapshot["connected"] is True
    assert snapshot["source"] == "simulated"
    assert snapshot["summary"]["queue_count"] == 1
    assert snapshot["summary"]["pool_count"] == 1
    assert snapshot["summary"]["machine_pallet"] is None
    row = snapshot["digital_input_groups"][0]["rows"][4]
    assert row["channel"] == "DI4"
    assert row["label"] == "DI4"
    assert row["custom_label"] is None
    assert row["label_key"] == "input:standard:4"
    assert row["index"] == 4
    assert row["bit"] == 4
    assert row["value"] is False
    assert row["writable"] is True
    assert row["direction"] == "input"
    assert row["bank"] == "standard"


def test_toggle_debug_io_updates_simulated_masks(client: TestClient) -> None:
    board = enable_debug(client, client.get("/api/board").json())

    response = client.post(
        "/api/debug/io/toggle",
        json={
            "expected_revision": board["revision"],
            "direction": "input",
            "bank": "standard",
            "index": 4,
        },
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["source"] == "simulated"
    assert snapshot["digital_input_groups"][0]["rows"][4]["value"] is True
    assert snapshot["revision"] == board["revision"] + 1


def test_rename_debug_io_updates_label(client: TestClient) -> None:
    board = enable_debug(client, client.get("/api/board").json())

    response = client.post(
        "/api/debug/io/label",
        json={
            "expected_revision": board["revision"],
            "direction": "input",
            "bank": "standard",
            "index": 4,
            "label": "Door Closed",
        },
    )

    assert response.status_code == 200
    snapshot = response.json()
    row = snapshot["digital_input_groups"][0]["rows"][4]
    assert row["channel"] == "DI4"
    assert row["label"] == "Door Closed"
    assert row["custom_label"] == "Door Closed"
    assert row["label_key"] == "input:standard:4"


def test_debug_program_button_configuration_persists(client: TestClient) -> None:
    board = client.get("/api/board").json()

    response = client.post(
        "/api/debug/programs/configure",
        json={
            "expected_revision": board["revision"],
            "index": 1,
            "display_name": "Inspect part",
            "filename": "/programs/inspect.urp",
            "color": "cyan",
        },
    )

    assert response.status_code == 200
    button = response.json()["program_controls"]["buttons"][1]
    assert button["display_name"] == "Inspect part"
    assert button["filename"] == "/programs/inspect.urp"
    assert button["color"] == "cyan"


def test_debug_program_button_rejects_mill_program_extension(client: TestClient) -> None:
    board = client.get("/api/debug/robot-io").json()

    response = client.post(
        "/api/debug/programs/configure",
        json={
            "expected_revision": board["revision"],
            "index": 0,
            "display_name": "Mill cycle",
            "filename": "/programs/mill_cycle.nc",
            "color": "green",
        },
    )

    assert response.status_code == 422
    assert "Robot program extensions" in response.json()["detail"]


def test_debug_program_run_uses_dashboard(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    board = client.post(
        "/api/debug/programs/configure",
        json={
            "expected_revision": board["revision"],
            "index": 0,
            "display_name": "Cycle start",
            "filename": "/programs/cycle.urp",
            "color": "green",
        },
    ).json()
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.0,
        },
    ).json()["board"]
    called = {}
    monkeypatch.setattr(
        "app.service.run_robot_program",
        lambda *args: called.update(args=args),
    )
    monkeypatch.setattr("app.service.read_robot_snapshot", lambda *args: {"robot": {}})

    response = client.post(
        "/api/debug/programs/run",
        json={"expected_revision": board["revision"], "index": 0},
    )

    assert response.status_code == 200
    assert called["args"] == ("192.168.0.10", "/programs/cycle.urp", 1.0)


def test_robot_program_files_use_configured_sftp(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".urp", ".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.5,
            "robot_file_access_enabled": True,
            "robot_file_host": "",
            "robot_file_port": 22,
            "robot_file_username": "robot",
            "robot_file_password": "secret",
            "robot_file_directory": "/programs",
            "robot_program_extensions": [".urp"],
        },
    ).json()["board"]
    called = {}
    monkeypatch.setattr(
        "app.service.list_robot_program_files",
        lambda **kwargs: called.update(kwargs) or ["/programs/open.urp"],
    )

    response = client.get("/api/debug/programs/files")

    assert response.status_code == 200
    assert response.json() == {"files": ["/programs/open.urp"]}
    assert called["host"] == "192.168.0.10"
    assert called["port"] == 22
    assert called["username"] == "robot"
    assert called["directory"] == "/programs"
    assert called["extensions"] == {".urp"}

    response = client.get("/api/debug/programs/files?include_all=true")

    assert response.status_code == 200
    assert called["extensions"] is None


def test_robot_programs_page_lists_files_when_enabled(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    assert client.get("/robot-programs").status_code == 200
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.0,
            "robot_file_access_enabled": True,
            "robot_file_username": "root",
            "robot_file_password": "easybot",
            "robot_programs_page_enabled": True,
            "robot_editor_command": "code",
        },
    ).json()["board"]
    monkeypatch.setattr(
        "app.main.list_robot_directory",
        lambda **kwargs: {"root": "/programs", "path": "/programs", "parent": None, "entries": [{"name": "job.urp", "path": "/programs/job.urp", "kind": "file", "size": 12, "modified_at": "2026-01-01T00:00:00+00:00"}]},
    )

    response = client.get("/api/robot-files")

    assert client.get("/robot-programs").status_code == 200
    assert response.status_code == 200
    assert response.json()["entries"][0]["name"] == "job.urp"
    assert board["settings"]["robot_programs_page_enabled"] is True

    from app.robot_files import RobotFileConflict

    monkeypatch.setattr(
        "app.main.copy_robot_file",
        lambda **kwargs: (_ for _ in ()).throw(RobotFileConflict(PurePosixPath("/programs/job.urp"))),
    )
    conflict = client.post(
        "/api/robot-files/action",
        json={"action": "copy", "path": "/programs/job.urp", "destination_directory": "/programs"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["destination"] == "/programs/job.urp"


def test_mill_programs_page_lists_pathpilot_files(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": board["settings"]["robot_connection_mode"],
            "robot_host": board["settings"]["robot_host"],
            "robot_port": board["settings"]["robot_port"],
            "robot_poll_hz": board["settings"]["robot_poll_hz"],
            "robot_timeout_seconds": board["settings"]["robot_timeout_seconds"],
            "cnc_host": "tormach",
            "cnc_ssh_port": 22,
            "cnc_ssh_username": "operator",
            "cnc_ssh_password": "secret",
            "cnc_timeout_seconds": 2,
            "mill_file_directory": "/home/operator/gcode",
            "mill_program_extensions": [".nc", ".tap"],
            "mill_programs_page_enabled": True,
            "mill_programs_filter_enabled": True,
            "mill_editor_command": "code",
        },
    ).json()["board"]
    called = {}

    def list_files(**kwargs):
        called.update(kwargs)
        return {"root": "/home/operator/gcode", "path": "/home/operator/gcode", "parent": None, "entries": []}

    monkeypatch.setattr("app.main.list_robot_directory", list_files)

    response = client.get("/api/mill-files")

    assert client.get("/mill-programs").status_code == 200
    assert response.status_code == 200
    assert called["host"] == "tormach"
    assert called["directory"] == "/home/operator/gcode"
    assert called["extensions"] == {".nc", ".tap"}
    assert board["settings"]["mill_programs_page_enabled"] is True


def test_toggle_physical_output_requires_manual_io_unlock(client: TestClient) -> None:
    board = enable_debug(client, client.get("/api/board").json())
    result = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "debug_menu_enabled": True,
            "manual_io_control_enabled": False,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.0,
        },
    )
    board = result.json()["board"]

    response = client.post(
        "/api/debug/io/toggle",
        json={
            "expected_revision": board["revision"],
            "direction": "output",
            "bank": "standard",
            "index": 0,
        },
    )

    assert response.status_code == 403


def test_toggle_physical_output_writes_when_unlocked(client: TestClient, monkeypatch) -> None:
    board = enable_debug(client, client.get("/api/board").json())
    result = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "debug_menu_enabled": True,
            "manual_io_control_enabled": True,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_port": 30004,
            "robot_poll_hz": 10,
            "robot_timeout_seconds": 1.0,
        },
    )
    board = result.json()["board"]
    called = {}
    monkeypatch.setattr(
        "app.service.toggle_robot_digital_output",
        lambda *args: called.update(args=args),
    )

    response = client.post(
        "/api/debug/io/toggle",
        json={
            "expected_revision": board["revision"],
            "direction": "output",
            "bank": "configurable",
            "index": 3,
        },
    )

    assert response.status_code == 200
    assert called["args"] == ("192.168.0.10", 30004, 1.0, "configurable", 3)
