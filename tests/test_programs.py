from pathlib import Path

from fastapi.testclient import TestClient

from app.program_metadata import parse_program_metadata
from app.robot_files import RobotFileAccessError
from app.service import program_metadata


def mps_program(*tools: int, cycle_seconds: float = 60) -> str:
    return (
        "%\n"
        "(MPS-METADATA-V1)\n"
        f"(MPS-TOOLS:{','.join(str(tool) for tool in tools)})\n"
        f"(MPS-CYCLE-SECONDS:{cycle_seconds})\n"
        "(MPS-CYCLE-BASIS:FUSION-CUTTING-ESTIMATE)\n"
        "M30\n"
    )


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
    program.write_text(mps_program(1, 20, 105, cycle_seconds=91.2), encoding="utf-8")
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
    assert board["pallets"][0]["program_tools"] == ["T1", "T20", "T105"]
    assert board["pallets"][0]["expected_cycle_seconds"] == 92
    assert board["pallets"][0]["program_metadata_state"] == "parsed"

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


def test_program_refresh_reloads_metadata_for_existing_assignments(client: TestClient, tmp_path: Path) -> None:
    program_dir = tmp_path / "programs"
    gcode_dir = program_dir / "Gcode"
    gcode_dir.mkdir(parents=True)
    program = gcode_dir / "part.nc"
    program.write_text(mps_program(1, 2, cycle_seconds=60), encoding="utf-8")
    board = configure_folder(client, program_dir, 0)
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": board["revision"],
            "workholding": "Vise",
            "weight_kg": 5,
            "content_status": "raw_stock",
            "program_path": "part.nc",
        },
    ).json()
    assert board["pallets"][0]["program_tools"] == ["T1", "T2"]

    program.write_text(mps_program(2, 9, cycle_seconds=125), encoding="utf-8")
    refreshed = client.post(
        "/api/programs/refresh",
        json={"expected_revision": board["revision"]},
    ).json()["board"]

    assert refreshed["pallets"][0]["program_tools"] == ["T2", "T9"]
    assert refreshed["pallets"][0]["expected_cycle_seconds"] == 125


def test_failed_remote_refresh_does_not_clear_assignments(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    program_dir = tmp_path / "programs"
    gcode_dir = program_dir / "Gcode"
    gcode_dir.mkdir(parents=True)
    (gcode_dir / "part.nc").write_text(mps_program(1), encoding="utf-8")
    board = configure_folder(client, program_dir, 0)
    board = client.post(
        "/api/pallets",
        json={
            "expected_revision": board["revision"],
            "workholding": "Vise",
            "weight_kg": 5,
            "content_status": "raw_stock",
            "program_path": "part.nc",
        },
    ).json()
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "mill_programs_page_enabled": True,
            "cnc_host": "pathpilot.local",
            "cnc_ssh_username": "operator",
            "cnc_ssh_password": "secret",
        },
    ).json()["board"]

    def fail_listing(**_kwargs):
        raise RobotFileAccessError("controller offline")

    monkeypatch.setattr("app.service.list_robot_program_files", fail_listing)
    response = client.post(
        "/api/programs/refresh",
        json={"expected_revision": board["revision"]},
    )

    assert response.status_code == 502
    current = client.get("/api/board").json()
    assert current["pallets"][0]["program_path"] == "part.nc"
    assert current["pallets"][0]["program_tools"] == ["T1"]


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


def test_program_metadata_parser_validates_and_sorts_header_values() -> None:
    parsed = parse_program_metadata(mps_program(105, 1, 20, 20, cycle_seconds=91.2))

    assert parsed["program_tools"] == ["T1", "T20", "T105"]
    assert parsed["expected_cycle_seconds"] == 92
    assert parsed["program_metadata_state"] == "parsed"
    assert parsed["program_cycle_basis"] == "FUSION-CUTTING-ESTIMATE"

    # Older output passed through PathPilot's comment filter, which removed
    # header colons. Keep assigned programs usable while newly posted files
    # use the canonical header above.
    legacy = parse_program_metadata(
        "(MPS-METADATA-V1)\n(MPS-TOOLS1,20)\n"
        "(MPS-CYCLE-SECONDS91.2)\n(MPS-CYCLE-BASISFUSION-CUTTING-ESTIMATE)\n"
    )
    assert legacy["program_tools"] == ["T1", "T20"]
    assert legacy["expected_cycle_seconds"] == 92

    missing = parse_program_metadata("%\nM30\n")
    assert missing["program_tools"] == []
    assert missing["expected_cycle_seconds"] is None
    assert missing["program_metadata_state"] == "unavailable"


def test_parsed_program_metadata_is_hidden_after_completion() -> None:
    completed = program_metadata(
        "jobs/part-a.nc", "complete_parts", ["T1", "T20"], 92,
        "parsed", "Metadata read.", "FUSION-CUTTING-ESTIMATE",
    )

    assert completed["program_tools"] == []
    assert completed["expected_cycle_seconds"] is None
    assert completed["program_metadata_state"] == "parsed"
