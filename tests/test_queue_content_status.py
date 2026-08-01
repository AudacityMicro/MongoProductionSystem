from fastapi.testclient import TestClient


def test_queue_can_convert_completed_pallet_to_raw_stock(client: TestClient) -> None:
    created = client.post(
        "/api/pallets",
        json={
            "expected_revision": 0,
            "workholding": "Vise",
            "weight_kg": 10,
            "content_status": "complete_parts",
            "program_path": None,
        },
    )
    assert created.status_code == 201
    board = created.json()
    pallet = board["pallets"][0]

    queued = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"], "convert_completed_to_raw": True},
    )

    assert queued.status_code == 200
    result = queued.json()["pallets"][0]
    assert result["content_status"] == "raw_stock"
    assert result["queue_position"] == 0
