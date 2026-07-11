from fastapi.testclient import TestClient


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
