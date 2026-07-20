from pathlib import Path

from fastapi.testclient import TestClient

from app.service import program_metadata


def configure_folder(client: TestClient, folder: Path, revision: int) -> dict:
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": revision,
            "source_folder": str(folder),
            "program_extensions": ["NC", ".tap"],
            "weight_unit": "kg",
            "pool_slot_count": 16,
        },
    )
    assert response.status_code == 200
    return response.json()["board"]


def test_program_scan_assignment_and_missing_file_reconciliation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    program_dir = tmp_path / "programs"
    nested = program_dir / "Gcode" / "jobs"
    nested.mkdir(parents=True)
    program = nested / "part-a.nc"
    program.write_text("placeholder", encoding="utf-8")
    (program_dir / "ignore.txt").write_text("ignored", encoding="utf-8")
    examples = program_dir / "examples"
    examples.mkdir()
    (examples / "example.nc").write_text("must not be assignable", encoding="utf-8")

    board = configure_folder(client, program_dir, 0)
    assert board["programs"] == ["jobs/part-a.nc"]
    assert board["settings"]["program_extensions"] == [".nc", ".tap"]

    response = client.post(
        "/api/pallets",
        json={
            "expected_revision": board["revision"],
            "workholding": "Fixture plate",
            "weight_kg": 20,
            "content_status": "raw_stock",
            "program_path": "jobs/part-a.nc",
        },
    )
    board = response.json()
    assert board["pallets"][0]["program_path"] == "jobs/part-a.nc"

    # Editing a pallet must retain the selected program just as creation does.
    response = client.put(
        f"/api/pallets/{board['pallets'][0]['id']}",
        json={
            "expected_revision": board["revision"],
            "workholding": "Updated fixture plate",
            "weight_kg": 20,
            "content_status": "raw_stock",
            "program_path": "jobs/part-a.nc",
        },
    )
    assert response.status_code == 200
    board = response.json()
    assert board["pallets"][0]["program_path"] == "jobs/part-a.nc"
    assert board["pallets"][0]["workholding"] == "Updated fixture plate"

    program.unlink()
    response = client.post(
        "/api/programs/refresh",
        json={"expected_revision": board["revision"]},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["cleared_assignments"] == [board["pallets"][0]["name"]]
    assert result["board"]["pallets"][0]["program_path"] is None


def test_program_path_must_be_discovered(client: TestClient, tmp_path: Path) -> None:
    board = configure_folder(client, tmp_path, 0)
    response = client.post(
        "/api/pallets",
        json={
            "expected_revision": board["revision"],
            "workholding": "Vise",
            "weight_kg": 20,
            "content_status": "empty",
            "program_path": "../outside.nc",
        },
    )
    assert response.status_code == 422


def test_unrelated_settings_save_preserves_program_assignments(client: TestClient, tmp_path: Path) -> None:
    program_dir = tmp_path / "programs"
    gcode_dir = program_dir / "Gcode"
    gcode_dir.mkdir(parents=True)
    (gcode_dir / "part.nc").write_text("M30\n", encoding="ascii")
    board = configure_folder(client, program_dir, 0)
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": board["revision"],
            "workholding": "Vise",
            "weight_kg": 20,
            "content_status": "raw_stock",
            "program_path": "part.nc",
        },
    ).json()

    # Simulate a network folder disappearing while an unrelated setting is saved.
    (gcode_dir / "part.nc").unlink()
    response = client.put(
        "/api/settings",
        json={"expected_revision": board["revision"], "weight_unit": "lb"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["cleared_assignments"] == []
    assert result["board"]["pallets"][0]["program_path"] == "part.nc"


def test_dummy_program_metadata_is_stable_and_hidden_after_completion() -> None:
    active = program_metadata("jobs/part-a.nc", "raw_stock")

    assert active["program_tools"]
    assert all(tool.startswith("T") for tool in active["program_tools"])
    assert active["expected_cycle_seconds"] is not None
    assert active == program_metadata("jobs/part-a.nc", "raw_stock")
    assert program_metadata("jobs/part-a.nc", "complete_parts") == {
        "program_tools": [],
        "expected_cycle_seconds": None,
    }
