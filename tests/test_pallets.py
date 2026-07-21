from fastapi.testclient import TestClient

from app import service
from app.models import Pallet
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
    assert pallet["name"] in PALLET_NAMES
    assert pallet["location"] == "pool"
    assert pallet["pool_slot_number"] == 1

    edited = pallet_payload(board["revision"])
    edited["content_status"] = "complete_parts"
    response = client.put(f"/api/pallets/{pallet['id']}", json=edited)
    assert response.status_code == 200
    board = response.json()
    assert board["pallets"][0]["name"] == pallet["name"]

    response = client.post(
        f"/api/pallets/{pallet['id']}/duplicate",
        json={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    board = response.json()
    duplicate = next(item for item in board["pallets"] if item["id"] != pallet["id"])
    assert duplicate["name"] in PALLET_NAMES
    assert duplicate["name"] != pallet["name"]
    assert duplicate["content_status"] == "complete_parts"
    assert duplicate["location"] == "pool"
    assert duplicate["pool_slot_number"] == 2

    response = client.delete(
        f"/api/pallets/{duplicate['id']}",
        params={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    assert len(response.json()["pallets"]) == 1


def test_simulated_robot_pick_and_put_away_preserves_queue(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()

    picked = client.post(
        "/api/robot-motions",
        json={
            "expected_revision": board["revision"],
            "operation": "pick",
            "pool_slot_number": 1,
            "pallet_id": pallet["id"],
        },
    )

    assert picked.status_code == 202
    board = picked.json()
    held = next(item for item in board["pallets"] if item["id"] == pallet["id"])
    assert held["location"] == "robot_held"
    assert held["pool_slot_number"] is None
    assert held["return_pool_slot_number"] == 1
    assert held["queue_position"] == 0
    assert board["robot_motion"]["history"][0]["status"] == "succeeded"

    put = client.post(
        "/api/robot-motions",
        json={
            "expected_revision": board["revision"],
            "operation": "put",
            "pool_slot_number": 4,
        },
    )

    assert put.status_code == 202
    returned = next(item for item in put.json()["pallets"] if item["id"] == pallet["id"])
    assert returned["location"] == "pool"
    assert returned["pool_slot_number"] == 4
    assert returned["return_pool_slot_number"] is None
    assert returned["queue_position"] == 0


def test_reserved_return_position_is_not_assigned_to_a_new_pallet(client: TestClient) -> None:
    board = create_pallet(client, 0)
    first = board["pallets"][0]
    held = client.post(
        "/api/robot-motions",
        json={
            "expected_revision": board["revision"],
            "operation": "pick",
            "pool_slot_number": 1,
            "pallet_id": first["id"],
        },
    ).json()

    created = create_pallet(client, held["revision"])
    by_id = {item["id"]: item for item in created["pallets"]}

    assert by_id[first["id"]]["return_pool_slot_number"] == 1
    second = next(item for item in created["pallets"] if item["id"] != first["id"])
    assert second["pool_slot_number"] == 2


def test_automatic_put_away_returns_robot_held_pallet_to_reserved_position(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    held = client.post(
        "/api/robot-motions",
        json={
            "expected_revision": board["revision"],
            "operation": "pick",
            "pool_slot_number": 1,
            "pallet_id": pallet["id"],
        },
    ).json()

    response = client.post(
        f"/api/pallets/{pallet['id']}/put-away",
        json={"expected_revision": held["revision"]},
    )

    assert response.status_code == 202
    returned = next(item for item in response.json()["pallets"] if item["id"] == pallet["id"])
    assert returned["location"] == "pool"
    assert returned["pool_slot_number"] == 1
    assert returned["return_pool_slot_number"] is None


def test_removing_a_pallet_from_queue_keeps_its_pool_location(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    first, second = sorted(board["pallets"], key=lambda item: item["pool_slot_number"])
    board = client.post(
        f"/api/pallets/{first['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{second['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()

    response = client.delete(
        f"/api/pallets/{first['id']}/queue",
        params={"expected_revision": board["revision"]},
    )

    assert response.status_code == 200
    pallets = {item["id"]: item for item in response.json()["pallets"]}
    assert pallets[first["id"]]["location"] == "pool"
    assert pallets[first["id"]]["pool_slot_number"] == 1
    assert pallets[first["id"]]["queue_position"] is None
    assert pallets[second["id"]]["queue_position"] == 0


def test_physical_robot_motion_requires_explicit_enable(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
        },
    )
    assert saved.status_code == 200

    blocked = client.post(
        "/api/robot-motions",
        json={
            "expected_revision": saved.json()["board"]["revision"],
            "operation": "pick",
            "pool_slot_number": 1,
            "pallet_id": pallet["id"],
        },
    )
    assert blocked.status_code == 403


def test_automatic_names_are_random_unique_and_revision_conflicts(client: TestClient, monkeypatch) -> None:
    selections: list[tuple[str, ...]] = []

    def choose_last(names: list[str]) -> str:
        selections.append(tuple(names))
        return names[-1]

    monkeypatch.setattr(service.random, "choice", choose_last)
    board = create_pallet(client, 0)

    second = client.post("/api/pallets", json=pallet_payload(board["revision"]))
    assert second.status_code == 201
    names = [item["name"] for item in second.json()["pallets"]]
    assert len(names) == len(set(names)) == 2
    assert names[0] == PALLET_NAMES[-1]
    assert names[1] == PALLET_NAMES[-2]
    assert len(selections) == 2

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
    assert [item["id"] for item in queue] == [second["id"], first["id"]]
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


def test_on_deck_and_dripping_are_single_pallet_stations(client: TestClient) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    first, second = sorted(board["pallets"], key=lambda item: item["name"])

    board = client.post(
        f"/api/pallets/{first['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{first['id']}/move",
        json={"expected_revision": board["revision"], "destination": "on_deck"},
    ).json()
    staged = next(item for item in board["pallets"] if item["id"] == first["id"])
    assert staged["location"] == "on_deck"
    assert staged["queue_position"] == 0

    occupied = client.post(
        f"/api/pallets/{second['id']}/move",
        json={"expected_revision": board["revision"], "destination": "on_deck"},
    )
    assert occupied.status_code == 409

    board = client.post(
        f"/api/pallets/{first['id']}/move",
        json={"expected_revision": board["revision"], "destination": "dripping"},
    ).json()
    dripping = next(item for item in board["pallets"] if item["id"] == first["id"])
    assert dripping["location"] == "dripping"
    assert dripping["queue_position"] is None


def test_disabled_optional_stations_are_removed_from_workflow(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": settings["revision"],
            "source_folder": settings["settings"]["source_folder"],
            "program_extensions": settings["settings"]["program_extensions"],
            "weight_unit": settings["settings"]["weight_unit"],
            "pool_slot_count": settings["settings"]["pool_slot_count"],
            "on_deck_enabled": False,
            "dripping_enabled": False,
        },
    )
    assert response.status_code == 200
    board = response.json()["board"]
    assert board["settings"]["on_deck_enabled"] is False
    assert board["settings"]["dripping_enabled"] is False

    board = create_pallet(client, board["revision"])
    pallet = board["pallets"][0]
    for destination, label in (("on_deck", "On deck"), ("dripping", "Dripping")):
        moved = client.post(
            f"/api/pallets/{pallet['id']}/move",
            json={"expected_revision": board["revision"], "destination": destination},
        )
        assert moved.status_code == 409
        assert label in moved.json()["detail"]


def test_occupied_optional_station_cannot_be_disabled(client: TestClient) -> None:
    board = create_pallet(client, 0)
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/move",
        json={"expected_revision": board["revision"], "destination": "on_deck"},
    ).json()
    settings = client.get("/api/settings").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": settings["revision"],
            "source_folder": settings["settings"]["source_folder"],
            "program_extensions": settings["settings"]["program_extensions"],
            "weight_unit": settings["settings"]["weight_unit"],
            "pool_slot_count": settings["settings"]["pool_slot_count"],
            "on_deck_enabled": False,
            "dripping_enabled": True,
        },
    )
    assert response.status_code == 409
    assert "Move the pallet out of On deck" in response.json()["detail"]


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
    first, second = board["pallets"]
    for pallet in (first, second):
        board = client.post(
            f"/api/pallets/{pallet['id']}/queue",
            json={
                "expected_revision": board["revision"],
            },
        ).json()

    response = client.put(
        "/api/queue",
        json={
            "expected_revision": board["revision"],
            "pallet_ids": [
                second["id"],
                first["id"],
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
    assert [item["id"] for item in queue] == [second["id"], first["id"]]


def test_only_pool_pallets_can_be_queued_and_dequeue_compacts(
    client: TestClient,
) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    first, second = board["pallets"]

    board = client.post(
        f"/api/pallets/{first['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{second['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()

    response = client.delete(
        f"/api/pallets/{first['id']}/queue",
        params={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    board = response.json()
    remaining = next(
        item for item in board["pallets"] if item["id"] == second["id"]
    )
    assert remaining["queue_position"] == 0
    assert remaining["location"] == "pool"

    board = client.post(
        f"/api/pallets/{first['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "storage",
        },
    ).json()
    response = client.post(
        f"/api/pallets/{first['id']}/queue",
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


def test_run_mode_allows_managing_non_machine_pallets_only(
    client: TestClient,
) -> None:
    board = create_pallet(client, 0)
    board = create_pallet(client, board["revision"])
    pool_pallet, machine_pallet = board["pallets"]

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        stored_machine_pallet = session.get(Pallet, machine_pallet["id"])
        assert stored_machine_pallet is not None
        stored_machine_pallet.location = "machine"
        stored_machine_pallet.pool_slot_number = None
        settings.run_mode_enabled = True
        session.commit()

    pool_update = pallet_payload(board["revision"])
    pool_update["workholding"] = "Updated vise"
    response = client.put(f"/api/pallets/{pool_pallet['id']}", json=pool_update)
    assert response.status_code == 200
    updated_board = response.json()
    updated_pool_pallet = next(
        item for item in updated_board["pallets"] if item["id"] == pool_pallet["id"]
    )
    assert updated_pool_pallet["workholding"] == "Updated vise"

    board = create_pallet(client, updated_board["revision"])
    added_pallet = next(
        item
        for item in board["pallets"]
        if item["id"] not in {pool_pallet["id"], machine_pallet["id"]}
    )
    board = client.post(
        f"/api/pallets/{pool_pallet['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{added_pallet['id']}/queue",
        json={"expected_revision": board["revision"]},
    ).json()
    board = client.put(
        "/api/queue",
        json={
            "expected_revision": board["revision"],
            "pallet_ids": [added_pallet["id"], pool_pallet["id"]],
        },
    ).json()
    board = client.delete(
        f"/api/pallets/{pool_pallet['id']}/queue",
        params={"expected_revision": board["revision"]},
    ).json()
    board = client.post(
        f"/api/pallets/{pool_pallet['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "storage",
        },
    ).json()
    board = client.post(
        f"/api/pallets/{added_pallet['id']}/duplicate",
        json={"expected_revision": board["revision"]},
    ).json()
    duplicate = next(
        item
        for item in board["pallets"]
        if item["id"] not in {pool_pallet["id"], machine_pallet["id"], added_pallet["id"]}
    )
    board = client.delete(
        f"/api/pallets/{duplicate['id']}",
        params={"expected_revision": board["revision"]},
    ).json()
    assert next(
        item for item in board["pallets"] if item["id"] == pool_pallet["id"]
    )["location"] == "storage"
    assert next(
        item for item in board["pallets"] if item["id"] == added_pallet["id"]
    )["queue_position"] == 0

    response = client.put(
        f"/api/pallets/{machine_pallet['id']}",
        json=pallet_payload(board["revision"]),
    )
    assert response.status_code == 409
    assert "mill" in response.json()["detail"].lower()

    response = client.post(
        f"/api/pallets/{machine_pallet['id']}/move",
        json={
            "expected_revision": board["revision"],
            "destination": "pool",
        },
    )
    assert response.status_code == 409
