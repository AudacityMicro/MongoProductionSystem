from pathlib import Path

from fastapi.testclient import TestClient

from app.autoschedule import ScheduleJob, optimize_tool_schedule, simulate_tool_plan


def job(identifier: str, position: int, *tools: int) -> ScheduleJob:
    return ScheduleJob(identifier, identifier, f"{identifier}.nc", frozenset(tools), position)


def test_optimizer_reduces_atc_tool_movements() -> None:
    jobs = (job("A", 0, 1, 3), job("B", 1, 1, 2))
    initial = frozenset({1, 2})

    original = simulate_tool_plan(jobs, initial, 2)
    optimized, method = optimize_tool_schedule(jobs, initial, 2)

    assert original["tool_movements"] == 4
    assert optimized["pallet_ids"] == ["B", "A"]
    assert optimized["tool_movements"] == 2
    assert method == "Exact minimum search"


def test_tool_plan_retains_useful_tools_in_free_atc_positions() -> None:
    jobs = (job("A", 0, 1), job("B", 1, 2))

    plan = simulate_tool_plan(jobs, frozenset({1, 2, 3}), 3)

    assert plan["tool_movements"] == 0
    assert plan["steps"][1]["atc_after"] == ["T1", "T2", "T3"]


def test_autoschedule_preview_and_apply_use_live_atc(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    program_dir = tmp_path / "programs"
    program_dir.mkdir()
    (program_dir / "a.nc").write_text("a", encoding="utf-8")
    (program_dir / "b.nc").write_text("b", encoding="utf-8")
    board = client.get("/api/board").json()
    board = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": str(program_dir),
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
        },
    ).json()["board"]

    metadata = {
        "a.nc": {"program_tools": ["T1", "T3"], "expected_cycle_seconds": 60},
        "b.nc": {"program_tools": ["T1", "T2"], "expected_cycle_seconds": 60},
    }
    monkeypatch.setattr("app.service.program_metadata", lambda path, status: metadata.get(path, {"program_tools": [], "expected_cycle_seconds": None}))
    monkeypatch.setattr(
        "app.service._configured_cnc_telemetry",
        lambda settings: (
            {"atc": {"slots": [{"position": 1, "tool_number": 1}, {"position": 2, "tool_number": 2}]}},
            "Live test ATC.",
        ),
    )

    for program in ("a.nc", "b.nc"):
        board = client.post(
            "/api/pallets",
            json={
                "expected_revision": board["revision"],
                "workholding": program,
                "weight_kg": 1,
                "content_status": "raw_stock",
                "program_path": program,
            },
        ).json()
        pallet = next(item for item in board["pallets"] if item["program_path"] == program)
        board = client.post(
            f"/api/pallets/{pallet['id']}/queue",
            json={"expected_revision": board["revision"]},
        ).json()

    preview = client.post(
        "/api/queue/autoschedule/preview",
        json={"expected_revision": board["revision"]},
    )

    assert preview.status_code == 200
    plan = preview.json()
    assert plan["atc"]["initial_tools"] == ["T1", "T2"]
    assert plan["savings"]["tool_movements"] == 2
    assert plan["automation"]["commands_generated"] is False
    assert plan["can_apply"] is True

    applied = client.put(
        "/api/queue",
        json={
            "expected_revision": plan["revision"],
            "pallet_ids": plan["optimized"]["pallet_ids"],
        },
    )
    assert applied.status_code == 200
    queued = sorted(
        (item for item in applied.json()["pallets"] if item["queue_position"] is not None),
        key=lambda item: item["queue_position"],
    )
    assert [item["program_path"] for item in queued] == ["b.nc", "a.nc"]
