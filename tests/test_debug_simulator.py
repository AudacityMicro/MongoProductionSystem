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
