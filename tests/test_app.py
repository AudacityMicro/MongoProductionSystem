from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_pages(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.3.0"}
    assert "Pallet schedule" in client.get("/").text
    assert "Scheduling settings" in client.get("/settings").text


def test_initial_board(client: TestClient) -> None:
    board = client.get("/api/board").json()

    assert board["revision"] == 0
    assert board["pallets"] == []
    assert board["settings"]["weight_unit"] == "lb"
    assert board["settings"]["pool_slot_count"] == 16
    assert board["settings"]["debug_menu_enabled"] is False
    assert board["settings"]["machine_state"] == "idle"
    assert board["settings"]["program_extensions"] == [
        ".nc",
        ".tap",
        ".gcode",
        ".cnc",
        ".urp",
    ]


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
