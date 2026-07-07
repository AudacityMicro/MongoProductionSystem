from fastapi.testclient import TestClient

from app.pallet_names import PALLET_NAMES


def pallet_payload(revision: int) -> dict:
    return {
        "expected_revision": revision,
        "workholding": "6-inch vise",
        "weight_kg": 32.5,
        "content_status": "raw_stock",
        "program_path": None,
    }


def create_pallet(client: TestClient, revision: int) -> dict:
    response = client.post("/api/pallets", json=pallet_payload(revision))
    assert response.status_code == 201
    return response.json()


def test_create_edit_duplicate_and_delete(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    assert pallet["name"] == "ABBA"
    assert pallet["location"] == "pool"
    assert pallet["pool_slot_number"] == 1

    edited = pallet_payload(board["revision"])
    edited["content_status"] = "complete_parts"
    response = client.put(f"/api/pallets/{pallet['id']}", json=edited)
    assert response.status_code == 200
    board = response.json()
    assert board["pallets"][0]["name"] == "ABBA"

    response = client.post(
        f"/api/pallets/{pallet['id']}/duplicate",
        json={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    board = response.json()
    duplicate = next(item for item in board["pallets"] if item["id"] != pallet["id"])
    assert duplicate["name"] == "Adele"
    assert duplicate["content_status"] == "complete_parts"
    assert duplicate["location"] == "pool"
    assert duplicate["pool_slot_number"] == 2

    response = client.delete(
        f"/api/pallets/{duplicate['id']}",
        params={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    assert len(response.json()["pallets"]) == 1


def test_automatic_names_are_unique_and_revision_conflicts(client: TestClient) -> None:
    board = create_pallet(client, 0)

    second = client.post("/api/pallets", json=pallet_payload(board["revision"]))
    assert second.status_code == 201
    assert {item["name"] for item in second.json()["pallets"]} == {"ABBA", "Adele"}

    stale = client.post("/api/pallets", json=pallet_payload(0))
    assert stale.status_code == 409


def test_artist_name_catalog_and_manual_name_rejection(
    client: TestClient,
) -> None:
    assert len(PALLET_NAMES) >= 250
    assert len({name.casefold() for name in PALLET_NAMES}) == len(PALLET_NAMES)

    payload = pallet_payload(0)
    payload["name"] = "Custom name"
    response = client.post("/api/pallets", json=payload)
    assert response.status_code == 422


def test_weight_must_be_finite_and_positive(client: TestClient) -> None:
    payload = pallet_payload(0)
    payload["weight_kg"] = 0
    assert client.post("/api/pallets", json=payload).status_code == 422


def test_queue_machine_pool_and_storage_invariants(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    first, second = sorted(board["pallets"], key=lambda item: item["name"])

    settings_response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
        },
    )
    board = settings_response.json()["board"]

    board = client.post(
        f"/api/pallets/{first['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{second['id']}/queue",
        json={
            "expected_revision": board["revision"],
            "queue_index": 0,
        },
    ).json()
    queue = sorted(
        (item for item in board["pallets"] if item["queue_position"] is not None),
        key=lambda item: item["queue_position"],
    )
    assert [item["name"] for item in queue] == ["Adele", "ABBA"]
    assert all(item["location"] == "pool" for item in queue)
    assert {item["pool_slot_number"] for item in queue} == {1, 2}

    board = client.post(
        f"/api/pallets/{first['id']}/move",
        json={"expected_revision": board["revision"], "destination": "machine"},
    ).json()
    moved_to_machine = next(
        item for item in board["pallets"] if item["id"] == first["id"]
    )
    assert moved_to_machine["location"] == "machine"
    assert moved_to_machine["queue_position"] is None
    occupied = client.post(
        f"/api/pallets/{second['id']}/move",
        json={"expected_revision": board["revision"], "destination": "machine"},
    )
    assert occupied.status_code == 409

    board = client.post(
        f"/api/pallets/{second['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "storage",
        },
    ).json()
    stored = next(item for item in board["pallets"] if item["id"] == second["id"])
    assert stored["location"] == "storage"
    assert stored["pool_slot_number"] is None
    assert stored["queue_position"] is None

    board = client.post(
        f"/api/pallets/{first['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "pool",
            "pool_slot_number": 16,
        },
    ).json()
    reduce_below_occupied = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "kg",
            "pool_slot_count": 15,
        },
    )
    assert reduce_below_occupied.status_code == 409

    occupied_position = client.post(
        f"/api/pallets/{second['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "pool",
            "pool_slot_number": 16,
        },
    )
    assert occupied_position.status_code == 409


def test_queue_reorder_requires_all_queue_members(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()

    response = client.put(
        "/api/queue",
        json={"expected_revision": board["revision"], "pallet_ids": []},
    )
    assert response.status_code == 422


def test_queue_can_be_reordered(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    by_name = {item["name"]: item for item in board["pallets"]}
    for name in ("ABBA", "Adele"):
        board = client.post(
            f"/api/pallets/{by_name[name]['id']}/queue",
            json={
                "expected_revision": board["revision"],
            },
        ).json()

    response = client.put(
        "/api/queue",
        json={
            "expected_revision": board["revision"],
            "pallet_ids": [
                by_name["Adele"]["id"],
                by_name["ABBA"]["id"],
            ],
        },
    )

    assert response.status_code == 200
    queue = sorted(
        (
            item
            for item in response.json()["pallets"]
            if item["queue_position"] is not None
        ),
        key=lambda item: item["queue_position"],
    )
    assert [item["name"] for item in queue] == ["Adele", "ABBA"]


def test_only_pool_pallets_can_be_queued_and_dequeue_compacts(
    client: TestClient,
) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    by_name = {item["name"]: item for item in board["pallets"]}

    board = client.post(
        f"/api/pallets/{by_name['ABBA']['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{by_name['Adele']['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()

    response = client.delete(
        f"/api/pallets/{by_name['ABBA']['id']}/queue",
        params={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    board = response.json()
    remaining = next(
        item for item in board["pallets"] if item["name"] == "Adele"
    )
    assert remaining["queue_position"] == 0
    assert remaining["location"] == "pool"

    board = client.post(
        f"/api/pallets/{by_name['ABBA']['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "storage",
        },
    ).json()
    response = client.post(
        f"/api/pallets/{by_name['ABBA']['id']}/queue",
        json={"expected_revision": board["revision"]},
    )
    assert response.status_code == 409


def test_machine_pallet_can_return_to_pool_and_queue(
    client: TestClient,
) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "machine",
        },
    ).json()

    response = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"]},
    )

    assert response.status_code == 200
    returned = response.json()["pallets"][0]
    assert returned["location"] == "pool"
    assert returned["pool_slot_number"] == 1
    assert returned["queue_position"] == 0
