import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_fusion_tool_library_and_atc_matching(client: TestClient, tmp_path: Path) -> None:
    library = tmp_path / "mill.tools"
    library.write_text(
        json.dumps(
            {
                "data": [
                    {"post-process": {"number": 20}, "description": "1/2 inch end mill"},
                    {"post-process": {"number": 1}, "comment": "Probe"},
                    {"post-process": {"number": 0}, "description": "Ignored"},
                ]
            }
        ),
        encoding="utf-8",
    )
    board = client.get("/api/board").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "fusion_tool_library_path": str(library),
        },
    )
    assert response.status_code == 200

    tools = client.get("/api/tools")

    assert tools.status_code == 200
    payload = tools.json()
    assert payload["warning"] is None
    assert payload["library"] == [
        {"number": 1, "tool": "T1", "description": "Probe"},
        {"number": 20, "tool": "T20", "description": "1/2 inch end mill"},
    ]
    assert payload["atc_tools"] == []
    assert payload["atc_source"] == "Mill telemetry is not connected yet."


def test_multiple_fusion_libraries_can_be_uploaded_and_removed(client: TestClient) -> None:
    first = json.dumps({"data": [{"post-process": {"number": 1}, "description": "Probe"}]}).encode()
    second = json.dumps({"data": [{"post-process": {"number": 20}, "description": "End mill"}]}).encode()

    upload = client.post(
        "/api/tool-libraries/upload",
        files=[
            ("files", ("first.tools", first, "application/json")),
            ("files", ("second.tools", second, "application/json")),
        ],
    )

    assert upload.status_code == 200
    libraries = upload.json()["libraries"]
    assert len(libraries) == 2
    assert [tool["tool"] for tool in client.get("/api/tools").json()["library"]] == ["T1", "T20"]

    removed = client.delete("/api/tool-libraries", params={"path": libraries[0]})

    assert removed.status_code == 200
    assert [tool["tool"] for tool in removed.json()["tools"]["library"]] == ["T20"]


def test_pathpilot_carousel_assignments_are_reported_as_physical_positions(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.service.read_linuxcnc_snapshot",
        lambda *args: {
            "atc": {
                "slots": [
                    {"position": 1, "tool_number": 431, "diameter": 0.5, "length_offset": 4.112892, "current": False},
                    {"position": 2, "tool_number": None, "diameter": None, "length_offset": None, "current": True},
                ]
            }
        },
    )
    board = client.get("/api/board").json()
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "cnc_telemetry_enabled": True,
            "cnc_host": "tormach",
            "cnc_ssh_username": "operator",
        },
    )
    assert saved.status_code == 200

    payload = client.get("/api/tools").json()

    assert payload["atc_source"] == "Live PathPilot zbot carousel assignments."
    assert payload["atc_tools"] == [
        {
            "position": 1,
            "number": 431,
            "tool": "T431",
            "description": "PathPilot tool 431",
            "diameter": 0.5,
            "length_offset": 4.112892,
            "current": False,
        }
    ]
    assert payload["atc_slots"][1]["tool"] is None
    assert payload["atc_slots"][1]["current"] is True
    assert client.get("/api/dashboard").json()["atc_tools"] == ["T431"]


def test_tool_colors_use_atc_membership_then_pathpilot_length(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    library = tmp_path / "colors.tools"
    library.write_text(
        json.dumps(
            {
                "data": [
                    {"post-process": {"number": 10}, "description": "Loaded"},
                    {"post-process": {"number": 20}, "description": "Measured"},
                    {"post-process": {"number": 30}, "description": "Zero"},
                    {"post-process": {"number": 40}, "description": "Missing"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.service.read_linuxcnc_snapshot",
        lambda *args: {
            "atc": {
                "slots": [
                    {"position": 1, "tool_number": 10, "diameter": None, "length_offset": 0.0, "current": True}
                ]
            },
            "tool_table": [
                {"tool_number": 10, "length_offset": 0.0},
                {"tool_number": 20, "length_offset": 2.5},
                {"tool_number": 30, "length_offset": 0.0},
            ],
        },
    )
    board = client.get("/api/board").json()
    saved = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": board["settings"]["source_folder"],
            "program_extensions": board["settings"]["program_extensions"],
            "weight_unit": board["settings"]["weight_unit"],
            "pool_slot_count": board["settings"]["pool_slot_count"],
            "fusion_tool_library_path": str(library),
            "cnc_telemetry_enabled": True,
            "cnc_host": "tormach",
            "cnc_ssh_username": "operator",
        },
    )
    assert saved.status_code == 200

    states = client.get("/api/tools").json()["tool_states"]

    assert states == {
        "10": {"status": "atc", "length_offset": 0.0},
        "20": {"status": "measured", "length_offset": 2.5},
        "30": {"status": "zero", "length_offset": 0.0},
        "40": {"status": "zero", "length_offset": None},
    }
