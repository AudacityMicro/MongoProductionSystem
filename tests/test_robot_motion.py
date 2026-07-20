import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import service
from app.robot_rtde import RobotTelemetryError
from app.robot_scripts import (
    UNLOADED_TOOL_PAYLOAD_KG,
    build_mill_pallet_motion_script,
    build_pallet_motion_script,
    build_reliability_motion_script,
    with_pallet_payload,
)
from app.schemas import StartMillPalletTransfer, StartPalletMotion


def _create_pallet(client: TestClient, revision: int) -> dict:
    response = client.post(
        "/api/pallets",
        json={
            "expected_revision": revision,
            "workholding": "Vise",
            "weight_kg": 10,
            "content_status": "raw_stock",
        },
    )
    assert response.status_code == 201
    return response.json()


def _motion_snapshot(pose: list[float]) -> dict:
    return {
        "connected": True,
        "tcp_detail_rows": [{"actual_pose": value} for value in pose],
        "tcp_speed_rows": [{"value": 0.04} for _ in range(6)],
        "joint_detail_rows": [{"actual_position": value} for value in (0.1, -1.2, 1.4, -1.6, -1.5, 0.2)],
        "state_rows": [{"label": "Safety mode", "value": 1}, {"label": "Runtime state", "value": 1}],
    }


def _joint_waypoint(name: str = "Safe") -> dict:
    return {"name": name, "joints_rad": [0.1, -1.2, 1.4, -1.6, -1.5, 0.2]}


def test_motion_interlock_uses_tcp_pose_delta_not_velocity_noise(client: TestClient, monkeypatch) -> None:
    samples = iter([
        _motion_snapshot([0.13040, -0.19238, 0.56093, 0.52120, -1.93310, 1.91110]),
        _motion_snapshot([0.13042, -0.19240, 0.56091, 0.52136, -1.93304, 1.91106]),
    ])
    monkeypatch.setattr(service, "robot_io_snapshot", lambda _: next(samples))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    with client.app.state.session_factory() as session:
        moving, _ = service._robot_motion_activity(session)

    assert moving is False


def test_motion_interlock_detects_meaningful_tcp_pose_change(client: TestClient, monkeypatch) -> None:
    samples = iter([
        _motion_snapshot([0.10, -0.19, 0.56, 0.52, -1.93, 1.91]),
        _motion_snapshot([0.104, -0.19, 0.56, 0.52, -1.93, 1.91]),
    ])
    monkeypatch.setattr(service, "robot_io_snapshot", lambda _: next(samples))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    with client.app.state.session_factory() as session:
        moving, _ = service._robot_motion_activity(session)

    assert moving is True


def test_physical_motion_interlock_reads_live_telemetry_directly(client: TestClient, monkeypatch) -> None:
    samples = iter([
        _motion_snapshot([0.10, -0.19, 0.56, 0.52, -1.93, 1.91]),
        _motion_snapshot([0.10, -0.19, 0.56, 0.52, -1.93, 1.91]),
    ])
    direct_reads: list[tuple[str, int, int, float]] = []

    def read_directly(host: str, port: int, poll_hz: int, timeout_seconds: float) -> dict:
        direct_reads.append((host, port, poll_hz, timeout_seconds))
        return next(samples)

    monkeypatch.setattr(service, "read_robot_snapshot", read_directly)
    monkeypatch.setattr(
        service,
        "robot_io_snapshot",
        lambda _: (_ for _ in ()).throw(AssertionError("motion interlocks must not build a debug snapshot")),
    )
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        moving, _ = service._robot_motion_activity(session)

    assert moving is False
    assert len(direct_reads) == 2
    assert all(read[0] == "192.168.0.10" for read in direct_reads)


def test_physical_motion_interlock_retries_one_transient_telemetry_gap(client: TestClient, monkeypatch) -> None:
    stable = _motion_snapshot([0.10, -0.19, 0.56, 0.52, -1.93, 1.91])
    responses: list[dict | Exception] = [RobotTelemetryError("brief gap"), stable, stable]
    calls = 0

    def read_directly(*_args) -> dict:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(service, "read_robot_snapshot", read_directly)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        moving, _ = service._robot_motion_activity(session)

    assert moving is False
    assert calls == 3


def test_debug_snapshot_distinguishes_dashboard_reachability_from_telemetry(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(service, "_cached_robot_telemetry", lambda _settings: (None, "state stream silent"))
    monkeypatch.setattr(
        service,
        "robot_dashboard_health",
        lambda *_args: {"reachable": True, "response": "Robotmode: IDLE", "error": None},
    )
    monkeypatch.setattr(
        service,
        "trigger_network_diagnostic_on_robot_loss",
        lambda: (_ for _ in ()).throw(AssertionError("network test should not run when Dashboard is reachable")),
    )

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        snapshot = service.robot_io_snapshot(session)

    assert snapshot["connected"] is True
    assert snapshot["telemetry_connected"] is False
    assert snapshot["connection_state"] == "degraded"
    assert "Controller reachable" in snapshot["connection_label"]


def test_generated_motion_must_finish_at_safe_waypoint(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "pallet_motion_generation",
        lambda _: {"safe_pre_waypoint": _joint_waypoint()},
    )
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        error = service._motion_final_pose_error(
            {"_joint_detail_rows": [{"actual_position": value} for value in (0.1, -1.2, 1.4, -1.6, -1.5, 0.3)]},
            settings,
            "/programs/pick_pool_001.script",
        )

    assert error == "The generated script stopped 0.100 rad away from its configured joint-space safe waypoint."


def test_pick_moves_to_robot_held_when_lift_clearance_is_reached(client: TestClient) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]
    motion = SimpleNamespace(
        operation="pick",
        program_path="/programs/mongo-production-system/pick_pool_001.script",
        pallet_id=pallet["id"],
        source_slot=1,
    )

    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.pool_location_positions = json.dumps([{"slot": 1, "x_mm": 100, "y_mm": 200, "z_mm": 300}])
        settings.pallet_motion_generation = json.dumps({"lift_z_clearance_mm": 75})

        assert service._mark_pick_as_held_after_lift(
            session,
            motion,
            {"_tcp_pose": (0.1, 0.2, 0.34, 0.0, 0.0, 0.0)},
            settings,
        ) is False
        assert service._mark_pick_as_held_after_lift(
            session,
            motion,
            {"_tcp_pose": (0.1, 0.2, 0.37, 0.0, 0.0, 0.0)},
            settings,
        ) is True

    updated = client.get("/api/board").json()
    moved = next(item for item in updated["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "robot_held"
    assert moved["pool_slot_number"] is None


def test_motion_safety_fault_is_not_a_success() -> None:
    assert service._motion_safety_error({"Safety mode": "protective_stop"}) == (
        "Robot safety mode changed during movement (protective_stop)."
    )
    assert service._motion_safety_error({"Safety mode": 1}) is None


def test_physical_executor_waits_for_busy_then_success(client: TestClient, monkeypatch) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]
    configured = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "pallet_motion_enabled": True,
            "pallet_motion_programs": [
                {"slot": 1, "pick_program": "/programs/pick_1.urp", "put_program": "/programs/put_1.urp"}
            ],
        },
    )
    assert configured.status_code == 200
    board = configured.json()["board"]
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    monkeypatch.setattr(service, "_assert_pool_motion_position_configured", lambda *_: None)
    monkeypatch.setattr(service, "run_robot_program", lambda *_: "Starting program")
    activity = iter([(False, {}), (True, {}), (False, {}), (False, {}), (False, {}), (False, {})])
    monkeypatch.setattr(service, "_robot_motion_activity", lambda *_: next(activity))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    with client.app.state.session_factory() as session:
        motion_id = service.start_pallet_motion(
            session,
            StartPalletMotion(
                expected_revision=board["revision"],
                operation="pick",
                pool_slot_number=1,
                pallet_id=pallet["id"],
            ),
        )

    assert motion_id
    service.execute_pallet_motion(client.app.state.session_factory, motion_id)
    result = client.get("/api/board").json()
    moved = next(item for item in result["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "robot_held"
    assert result["robot_motion"]["history"][0]["observed_busy"] is True
    assert result["robot_motion"]["history"][0]["status"] == "succeeded"


def test_simulated_mill_transfer_moves_pool_pallet_to_machine_then_back(client: TestClient) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]

    with client.app.state.session_factory() as session:
        motion_id = service.start_mill_pallet_transfer(
            session,
            StartMillPalletTransfer(expected_revision=board["revision"], operation="load", pallet_id=pallet["id"]),
        )
    assert motion_id is None
    loaded = client.get("/api/board").json()
    moved = next(item for item in loaded["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "machine"
    assert moved["pool_slot_number"] is None

    with client.app.state.session_factory() as session:
        motion_id = service.start_mill_pallet_transfer(
            session,
            StartMillPalletTransfer(expected_revision=loaded["revision"], operation="unload", pool_slot_number=1),
        )
    assert motion_id is None
    unloaded = client.get("/api/board").json()
    moved = next(item for item in unloaded["pallets"] if item["id"] == pallet["id"])
    assert moved["location"] == "pool"
    assert moved["pool_slot_number"] == 1


def test_simulated_mill_transfer_loads_a_robot_held_pallet(client: TestClient) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]

    with client.app.state.session_factory() as session:
        motion_id = service.start_pallet_motion(
            session,
            StartPalletMotion(
                expected_revision=board["revision"],
                operation="pick",
                pool_slot_number=1,
                pallet_id=pallet["id"],
            ),
        )
    assert motion_id is None
    held = client.get("/api/board").json()
    assert next(item for item in held["pallets"] if item["id"] == pallet["id"])["location"] == "robot_held"

    with client.app.state.session_factory() as session:
        motion_id = service.start_mill_pallet_transfer(
            session,
            StartMillPalletTransfer(
                expected_revision=held["revision"],
                operation="load",
                pallet_id=pallet["id"],
            ),
        )
    assert motion_id is None
    loaded = client.get("/api/board").json()
    assert next(item for item in loaded["pallets"] if item["id"] == pallet["id"])["location"] == "machine"


def test_manual_physical_held_load_positions_mill_before_robot_load(
    client: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "192.168.0.10"
        settings.pallet_motion_enabled = True
        pallet_row = session.get(service.Pallet, pallet["id"])
        pallet_row.location = "robot_held"
        pallet_row.pool_slot_number = None
        session.commit()
        revision = settings.revision

    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    with client.app.state.session_factory() as session:
        motion_id = service.start_mill_pallet_transfer(
            session,
            StartMillPalletTransfer(
                expected_revision=revision,
                operation="load",
                pallet_id=pallet["id"],
            ),
        )
        motion = session.get(service.RobotMotion, motion_id)
        assert service.MILL_LOAD_POSITION_PROGRAM_NAME in motion.program_path
        assert "pick_pool" not in motion.program_path

    (tmp_path / "load_mill.script").write_text("def mps_load_mill():\nend\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(service, "generated_script_directory", lambda *_: tmp_path)
    monkeypatch.setattr(service, "_run_manual_mill_load_position_cycle", lambda *_: calls.append("mill_position") or True)
    monkeypatch.setattr(service, "run_robot_script", lambda *_: calls.append("robot_load"))
    monkeypatch.setattr(service, "_motion_final_pose_error", lambda *_: None)
    activity = iter([
        (True, {"Safety mode": 1, "Runtime state": 2}),
        (False, {"Safety mode": 1, "Runtime state": 1}),
        (False, {"Safety mode": 1, "Runtime state": 1}),
        (False, {"Safety mode": 1, "Runtime state": 1}),
        (False, {"Safety mode": 1, "Runtime state": 1}),
    ])
    monkeypatch.setattr(service, "_robot_motion_activity", lambda *_: next(activity))
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)

    service.execute_pallet_motion(client.app.state.session_factory, motion_id)

    assert calls == ["mill_position", "robot_load"]
    loaded = client.get("/api/board").json()
    assert next(item for item in loaded["pallets"] if item["id"] == pallet["id"])["location"] == "machine"


def test_rebuild_generates_and_syncs_pool_scripts(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/settings").json()
    configured = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "robot_file_access_enabled": True,
            "robot_file_username": "root",
            "robot_file_password": "easybot",
            "robot_mill_load_unload": {"name": "Mill station", "x_mm": 400, "y_mm": -300, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "robot_mill_safe_entry_exit": {"name": "Mill clearance", "x_mm": 200, "y_mm": -300, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "pallet_motion_generation": {
                "approach_y_clearance_mm": 120,
                "lift_z_clearance_mm": 80,
                "max_travel_speed_rad_s": 0.8,
                "pickup_setdown_speed_m_s": 0.1,
                "rx_rad": 3.14,
                "ry_rad": 0,
                "rz_rad": 0,
                    "grip_output": {"bank": "configurable", "index": 4},
                    "grip_closed_value": True,
                    "mill_pre_entry_waypoint": {"name": "Mill pre-entry", "x_mm": 100, "y_mm": -300, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
                    "safe_pre_waypoint": _joint_waypoint("Safe Pre"),
                "safe_post_waypoint": _joint_waypoint("Safe Post"),
                "travel_waypoints": [_joint_waypoint("Home")],
            },
        },
    )
    assert configured.status_code == 200

    def fake_sync(**kwargs):
        assert len(kwargs["files"]) == 55
        assert "pick_pool_001.script" in kwargs["files"]
        assert "reliability_pool_001.script" in kwargs["files"]
        assert "load_mill.script" in kwargs["files"]
        assert "unload_mill.script" in kwargs["files"]
        assert "mongo_supervisor.script" in kwargs["files"]
        supervisor = kwargs["files"]["mongo_supervisor.script"]
        assert supervisor.startswith("def mongo_supervisor():\n")
        assert supervisor.rstrip().endswith("end")
        assert "socket_open(\"DESKTOP-KF5I73N.lan\", 50010" in supervisor
        assert "socket_read_binary_integer(9, \"mongo\")" in supervisor
        assert "socket_send_int(" in supervisor
        assert "socket_read_ascii_float" not in supervisor
        assert "to_str(" not in supervisor
        assert " if mongo_latched else " not in supervisor
        assert kwargs["files"]["pick_pool_001.script"].startswith("def mps_pick_pool_001():\n")
        assert kwargs["files"]["pick_pool_001.script"].rstrip().endswith("end")
        assert "movel(p[0.000000,0.120000,0.000000" in kwargs["files"]["pick_pool_001.script"]
        assert "movel(p[0.500000,-0.300000,0.500000" in kwargs["files"]["load_mill.script"]
        return {name: f"/programs/mongo-production-system/{name}" for name in kwargs["files"]}

    monkeypatch.setattr(service, "sync_generated_scripts", fake_sync)
    rebuilt = client.post("/api/robot-motions/rebuild-scripts")

    assert rebuilt.status_code == 200
    payload = rebuilt.json()
    assert len(payload["files"]) == 55
    mapping = payload["board"]["settings"]["pallet_motion_programs"][0]
    assert mapping["pick_program"].endswith("pick_pool_001.script")
    assert mapping["put_program"].endswith("put_pool_001.script")
    assert mapping["reliability_program"].endswith("reliability_pool_001.script")
    assert payload["board"]["settings"]["motion_scripts_need_rebuild"] is False

    stale = client.put(
        "/api/settings",
        json={
            "expected_revision": payload["board"]["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "pallet_motion_generation": {
                "approach_y_clearance_mm": 121,
                "lift_z_clearance_mm": 80,
                "max_travel_speed_rad_s": 0.8,
                "pickup_setdown_speed_m_s": 0.1,
                "rx_rad": 3.14,
                "ry_rad": 0,
                "rz_rad": 0,
                "grip_output": {"bank": "configurable", "index": 4},
                "grip_closed_value": True,
                "safe_pre_waypoint": _joint_waypoint("Safe Pre"),
                "safe_post_waypoint": _joint_waypoint("Safe Post"),
                "travel_waypoints": [_joint_waypoint("Home")],
            },
        },
    )
    assert stale.status_code == 200
    assert stale.json()["board"]["settings"]["motion_scripts_need_rebuild"] is True


def test_generated_script_uses_shared_safe_pose_gripper_and_latched_result() -> None:
    script = build_pallet_motion_script(
        function_name="mps_pick_pool_001",
        operation="pick",
        position={"x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": {"bank": "configurable", "index": 4},
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint("Safe Pre"),
            "safe_post_waypoint": _joint_waypoint("Safe Post"),
            "travel_waypoints": [_joint_waypoint("Home")],
        },
    )

    assert "movej([0.100000,-1.200000,1.400000,-1.600000,-1.500000,0.200000]" in script
    assert "movel(p[0.100000,0.300000,0.300000" in script
    assert "movel(p[0.100000,0.200000,0.400000" in script
    assert script.count("movej([0.100000,-1.200000,1.400000,-1.600000,-1.500000,0.200000]") == 2
    assert "Travel waypoint: Home" not in script
    assert "movel(p[-0.100000,0.000000,0.500000" not in script
    assert "set_configurable_digital_out(4, True)" in script
    assert "set_standard_digital_out(0, True)" not in script
    assert "set_standard_digital_out(1, True)" not in script
    assert "# Pallet approach position | X=100.000 mm, Y=300.000 mm, Z=300.000 mm" in script
    assert script.count("# ") >= script.count("movej(") + script.count("movel(")


def test_generated_pool_script_routes_through_assigned_intermediate_safe_poses() -> None:
    script = build_pallet_motion_script(
        function_name="mps_pick_pool_001",
        operation="pick",
        position={"slot": 1, "x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": None,
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint("Shared safe"),
            "intermediate_safe_poses": [
                {"name": "Rack entry", "joints_rad": [0.31, -1.1, 1.3, -1.5, -1.4, 0.4], "pool_slots": [1, 3]},
                {"name": "Other rack", "joints_rad": [0.61, -1.0, 1.2, -1.4, -1.3, 0.5], "pool_slots": [2]},
            ],
        },
    )

    entering = script.index("# Intermediate safe pose: Rack entry")
    approach = script.index("# Pallet approach position")
    retract = script.index("# Retract lifted pallet by Y approach clearance")
    returning = script.index("# Return via intermediate safe pose: Rack entry")
    safe_return = script.rindex("# Return to Shared safe")
    assert "Other rack" not in script
    assert "movej([0.310000,-1.100000,1.300000,-1.500000,-1.400000,0.400000]" in script
    assert entering < approach < retract < returning < safe_return


def test_generated_script_allows_a_passive_fork_without_gripper_output() -> None:
    script = build_pallet_motion_script(
        function_name="mps_pick_pool_001",
        operation="pick",
        position={"x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": None,
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint("Safe Pre"),
            "safe_post_waypoint": _joint_waypoint("Safe Post"),
            "travel_waypoints": [],
        },
    )

    assert "digital_out" not in script
    assert "movel(p[0.100000,0.300000,0.300000" in script


def test_reliability_script_visits_only_outer_staging_between_pick_and_same_slot_put() -> None:
    generation = {
        "approach_y_clearance_mm": 100,
        "lift_z_clearance_mm": 100,
        "max_travel_speed_rad_s": 0.6,
        "pickup_setdown_speed_m_s": 0.08,
        "rx_rad": 3.14,
        "ry_rad": 0,
        "rz_rad": 0,
        "grip_output": None,
        "grip_closed_value": True,
        "safe_pre_waypoint": _joint_waypoint("Shared safe"),
        "intermediate_safe_poses": [],
        "door_open_action": {"output": {"bank": "standard", "index": 4}, "active_value": True},
        "door_close_action": {"output": {"bank": "standard", "index": 4}, "active_value": False},
        "erowa_unlock_action": {"output": {"bank": "standard", "index": 6}, "active_value": True},
        "erowa_lock_action": {"output": {"bank": "standard", "index": 6}, "active_value": False},
    }
    script = build_reliability_motion_script(
        function_name="mps_reliability_pool_001",
        position={"slot": 1, "x_mm": 100, "y_mm": 200, "z_mm": 300},
        staging_pose={"name": "Outer staging", "x_mm": 500, "y_mm": -100, "z_mm": 600, "rx_rad": 0.5, "ry_rad": -1.9, "rz_rad": 1.9},
        generation=generation,
    )

    pickup = script.rindex("  mps_pick_pool_001()")
    staging = script.index("# Reliability test outer mill staging: Outer staging")
    put_away = script.rindex("  mps_put_pool_001()")
    assert pickup < staging < put_away
    assert "movel(p[0.500000,-0.100000,0.600000" in script
    assert "Open mill door" not in script
    assert "Unlock Erowa" not in script
    assert "Lock Erowa" not in script
    assert "set_standard_digital_out(4" not in script
    assert "set_standard_digital_out(6" not in script


def test_simulated_reliability_test_follows_frozen_queue_without_moving_board_pallets(client: TestClient) -> None:
    board = _create_pallet(client, 0)
    first = board["pallets"][0]
    board = _create_pallet(client, board["revision"])
    second = next(item for item in board["pallets"] if item["id"] != first["id"])
    board = client.post(
        f"/api/pallets/{second['id']}/queue",
        json={"expected_revision": board["revision"], "queue_index": 0},
    ).json()
    board = client.post(
        f"/api/pallets/{first['id']}/queue",
        json={"expected_revision": board["revision"], "queue_index": 1},
    ).json()
    original_locations = {
        item["id"]: (item["location"], item["pool_slot_number"])
        for item in board["pallets"]
    }

    with client.app.state.session_factory() as session:
        run_id = service.start_robot_reliability_test(session, board["revision"])
    service.execute_robot_reliability_test(client.app.state.session_factory, run_id)

    with client.app.state.session_factory() as session:
        status = service.robot_reliability_status(session)
    assert status["latest"]["status"] == "completed"
    assert [item["pallet_id"] for item in status["latest"]["queue_snapshot"]] == [second["id"], first["id"]]
    assert status["latest"]["completed_pallets"] == 2
    final = client.get("/api/board").json()
    assert {
        item["id"]: (item["location"], item["pool_slot_number"])
        for item in final["pallets"]
    } == original_locations


def test_reliability_cancel_before_first_cycle_stops_without_board_changes(client: TestClient) -> None:
    board = _create_pallet(client, 0)
    pallet = board["pallets"][0]
    board = client.post(
        f"/api/pallets/{pallet['id']}/queue",
        json={"expected_revision": board["revision"], "queue_index": 0},
    ).json()
    with client.app.state.session_factory() as session:
        run_id = service.start_robot_reliability_test(session, board["revision"])
    with client.app.state.session_factory() as session:
        service.cancel_robot_reliability_test(session)
    service.execute_robot_reliability_test(client.app.state.session_factory, run_id)

    with client.app.state.session_factory() as session:
        latest = service.robot_reliability_status(session)["latest"]
    assert latest["status"] == "cancelled"
    assert latest["completed_pallets"] == 0
    final_pallet = client.get("/api/board").json()["pallets"][0]
    assert final_pallet["location"] == "pool"


def test_generated_put_script_approaches_from_above_then_withdraws_in_y() -> None:
    script = build_pallet_motion_script(
        function_name="mps_put_pool_001",
        operation="put",
        position={"x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": None,
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint(),
            "travel_waypoints": [],
        },
    )

    outside_high_pose = "movel(p[0.100000,0.300000,0.400000"
    outside_high_entry = script.index(outside_high_pose)
    above = script.index("movel(p[0.100000,0.200000,0.400000")
    setdown = script.index("movel(p[0.100000,0.200000,0.300000")
    withdraw = script.index("movel(p[0.100000,0.300000,0.300000")
    safe_return = script.rindex("movej([0.100000,-1.200000,1.400000,-1.600000,-1.500000,0.200000]")
    assert outside_high_entry < above < setdown < withdraw < safe_return
    assert script.count(outside_high_pose) == 1
    assert "# Retract fork by Y approach clearance | X=100.000 mm, Y=300.000 mm, Z=300.000 mm" in script


def test_generated_pick_enters_directly_at_the_y_approach_height() -> None:
    script = build_pallet_motion_script(
        function_name="mps_pick_pool_001",
        operation="pick",
        position={"x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": None,
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint(),
        },
    )

    approach = script.index("# Pallet approach position")
    pickup = script.index("# Pallet pickup position")
    assert "# Pallet outside/high clearance" not in script
    assert approach < pickup


def test_generated_pick_retracts_at_lift_height_before_returning_safe() -> None:
    script = build_pallet_motion_script(
        function_name="mps_pick_pool_001",
        operation="pick",
        position={"x_mm": 100, "y_mm": 200, "z_mm": 300},
        generation={
            "approach_y_clearance_mm": 100,
            "lift_z_clearance_mm": 100,
            "max_travel_speed_rad_s": 0.6,
            "pickup_setdown_speed_m_s": 0.08,
            "rx_rad": 3.14,
            "ry_rad": 0,
            "rz_rad": 0,
            "grip_output": None,
            "grip_closed_value": True,
            "safe_pre_waypoint": _joint_waypoint(),
            "travel_waypoints": [],
        },
    )

    pickup = script.index("movel(p[0.100000,0.200000,0.300000")
    lift = script.index("movel(p[0.100000,0.200000,0.400000", pickup)
    retract = script.index("movel(p[0.100000,0.300000,0.400000", lift)
    safe_return = script.rindex("movej([0.100000,-1.200000,1.400000,-1.600000,-1.500000,0.200000]")
    assert pickup < lift < retract < safe_return
    assert "# Retract lifted pallet by Y approach clearance | X=100.000 mm, Y=300.000 mm, Z=400.000 mm" in script


def test_generated_pool_scripts_apply_payload_only_while_the_pallet_is_held() -> None:
    generation = {
        "approach_y_clearance_mm": 100,
        "lift_z_clearance_mm": 100,
        "max_travel_speed_rad_s": 0.6,
        "pickup_setdown_speed_m_s": 0.08,
        "rx_rad": 3.14,
        "ry_rad": 0,
        "rz_rad": 0,
        "grip_output": None,
        "grip_closed_value": True,
        "safe_pre_waypoint": _joint_waypoint(),
    }
    position = {"x_mm": 100, "y_mm": 200, "z_mm": 300}
    pick = build_pallet_motion_script(
        function_name="mps_pick_pool_001", operation="pick", position=position, generation=generation,
    )
    put = build_pallet_motion_script(
        function_name="mps_put_pool_001", operation="put", position=position, generation=generation,
    )

    unloaded = f"set_payload({UNLOADED_TOOL_PAYLOAD_KG:.6f})"
    assert pick.index(unloaded) < pick.index("# Pallet pickup position")
    assert pick.index("set_payload(mongo_pallet_payload_kg)") > pick.index("# Pallet pickup position")
    assert put.index("set_payload(mongo_pallet_payload_kg)") < put.index("# Pallet setdown position")
    assert put.index(unloaded) > put.index("# Pallet setdown position")

    dispatched = with_pallet_payload(pick, 12.3456789)
    assert "global mongo_pallet_payload_kg = 12.345679" in dispatched
    assert "set_payload(mongo_pallet_payload_kg)" in dispatched


def test_generated_mill_load_and_unload_scripts_use_the_mill_clearance_pose() -> None:
    generation = {
        "approach_y_clearance_mm": 100,
        "mill_approach_x_clearance_mm": 120,
        "lift_z_clearance_mm": 100,
        "mill_lift_z_clearance_mm": 55,
        "max_travel_speed_rad_s": 0.6,
        "pickup_setdown_speed_m_s": 0.08,
        "grip_output": None,
        "grip_closed_value": True,
        "door_open_action": {"output": {"bank": "configurable", "index": 0}, "active_value": True, "pulse": True},
        "door_close_action": {"output": {"bank": "configurable", "index": 1}, "active_value": True, "pulse": True},
        "erowa_unlock_action": {"output": {"bank": "configurable", "index": 2}, "active_value": False, "pulse": False},
        "erowa_lock_action": {"output": {"bank": "configurable", "index": 2}, "active_value": True, "pulse": False},
        "mill_actuation_wait_seconds": 1.5,
        "safe_pre_waypoint": _joint_waypoint("Cell safe"),
        "travel_waypoints": [],
    }
    mill_pose = {"x_mm": 400, "y_mm": -300, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}
    pre_entry = {"name": "Mill pre-entry", "x_mm": 100, "y_mm": -300, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}
    clearance = {"x_mm": 200, "y_mm": -300, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}

    load = build_mill_pallet_motion_script(
        function_name="mps_load_mill", operation="load", mill_pose=mill_pose, pre_entry_pose=pre_entry, entry_exit_pose=clearance, generation=generation,
    )
    unload = build_mill_pallet_motion_script(
        function_name="mps_unload_mill", operation="unload", mill_pose=mill_pose, pre_entry_pose=pre_entry, entry_exit_pose=clearance, generation=generation,
    )

    linear_entry = "movel(p[0.200000,-0.300000,0.600000"
    linear_pre_entry = "movel(p[0.100000,-0.300000,0.600000"
    assert load.count(linear_pre_entry) == 2
    assert unload.count(linear_pre_entry) == 2
    assert load.index(linear_pre_entry) < load.index(linear_entry)
    assert load.count(linear_entry) == 2
    assert unload.count(linear_entry) == 2
    assert load.index("movel(p[0.400000,-0.300000,0.500000") < load.index("movel(p[0.520000,-0.300000,0.500000")
    assert unload.index("movel(p[0.520000,-0.300000,0.500000") < unload.index("movel(p[0.400000,-0.300000,0.500000")
    load_withdraw = "movel(p[0.520000,-0.300000,0.500000"
    unload_lift = "movel(p[0.400000,-0.300000,0.555000"
    unload_withdraw = "movel(p[0.520000,-0.300000,0.555000"
    assert load.index(load_withdraw) < load.rindex(linear_entry)
    assert unload.index(unload_lift) < unload.index(unload_withdraw) < unload.rindex(linear_entry)
    assert "# Withdraw fork in positive X | X=520.000 mm" in load
    assert "# Withdraw lifted pallet in positive X | X=520.000 mm" in unload
    assert "# Linear retract to mill entry/exit" in load
    assert "# Return to Cell safe" in load
    assert load.index("# Open mill door") < load.index("# Linear mill entry")
    assert load.index("# Unlock Erowa system") < load.index("# Mill pallet position from above")
    assert load.index("# Lock Erowa system") < load.index("# Close mill door")
    assert load.index("# Close mill door") < load.index("# Return to Cell safe")
    assert "set_configurable_digital_out(0, True)" in load
    assert "set_configurable_digital_out(0, False)" in load
    assert "set_configurable_digital_out(2, False)" in load
    assert "set_configurable_digital_out(2, True)" in load
    assert "# Mill pallet setdown position | X=400.000 mm, Y=-300.000 mm, Z=500.000 mm" in load
    assert load.count("# ") >= load.count("movej(") + load.count("movel(")
    assert load.index("set_payload(mongo_pallet_payload_kg)") < load.index("# Linear mill pre-entry")
    assert load.index(f"set_payload({UNLOADED_TOOL_PAYLOAD_KG:.6f})", 1) > load.index("# Mill pallet setdown position")
    assert unload.index(f"set_payload({UNLOADED_TOOL_PAYLOAD_KG:.6f})") < unload.index("# Mill pallet pickup position")
    assert unload.index("set_payload(mongo_pallet_payload_kg)") > unload.index("# Mill pallet pickup position")


def test_debug_pallet_motion_dispatches_selected_slot_without_board_changes(client: TestClient, monkeypatch) -> None:
    board = _create_pallet(client, 0)
    configured = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "debug_menu_enabled": False,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "pallet_motion_enabled": True,
            "pallet_motion_programs": [
                {"slot": 3, "pick_program": "/programs/pick_3.urp", "put_program": "/programs/put_3.urp"}
            ],
        },
    )
    assert configured.status_code == 200
    current = configured.json()["board"]
    called = {}
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    monkeypatch.setattr(service, "_assert_pool_motion_position_configured", lambda *_: None)
    monkeypatch.setattr(service, "run_robot_program", lambda *args: called.update(args=args))

    response = client.post(
        "/api/debug/pallet-motion",
        json={"expected_revision": current["revision"], "operation": "pick", "pool_slot_number": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    assert called["args"] == ("192.168.0.10", "/programs/pick_3.urp", 1.0)
    unchanged = client.get("/api/board").json()
    assert unchanged["pallets"][0]["location"] == "pool"
    assert unchanged["robot_motion"]["active"] is None


def test_debug_pallet_motion_rejects_unconfigured_zero_position(client: TestClient, monkeypatch) -> None:
    board = client.get("/api/board").json()
    with client.app.state.session_factory() as session:
        settings = service.get_settings(session)
        settings.robot_connection_mode = "physical"
        settings.robot_host = "127.0.0.1"
        settings.pallet_motion_enabled = True
        settings.pool_slot_count = 1
        settings.pool_location_positions = json.dumps([{"slot": 1, "x_mm": 0, "y_mm": 0, "z_mm": 0}])
        settings.pallet_motion_programs = json.dumps([
            {"slot": 1, "pick_program": "/programs/pick.script", "put_program": "/programs/put.script"}
        ])
        session.commit()
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)

    response = client.post(
        "/api/debug/pallet-motion",
        json={"expected_revision": board["revision"], "operation": "pick", "pool_slot_number": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Teach a valid robot position for Pool 01 before commanding movement."


def test_debug_pick_rejects_a_stale_generated_script(client: TestClient, monkeypatch, tmp_path) -> None:
    board = _create_pallet(client, 0)
    configured = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "pallet_motion_enabled": True,
            "pallet_motion_programs": [
                {"slot": 1, "pick_program": "/programs/mongo-production-system/pick_pool_001.script", "put_program": "/programs/mongo-production-system/put_pool_001.script"}
            ],
            "pallet_motion_generation": {
                    "safe_pre_waypoint": _joint_waypoint()
            },
        },
    )
    assert configured.status_code == 200
    (tmp_path / "pick_pool_001.script").write_text("def stale_program():\nend\n", encoding="utf-8")
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    monkeypatch.setattr(service, "_assert_pool_motion_position_configured", lambda *_: None)
    monkeypatch.setattr(service, "generated_script_directory", lambda *_: tmp_path)
    monkeypatch.setattr(service, "run_robot_script", lambda *_: (_ for _ in ()).throw(AssertionError("must not run stale script")))

    response = client.post(
        "/api/debug/pallet-motion",
        json={"expected_revision": configured.json()["board"]["revision"], "operation": "pick", "pool_slot_number": 1},
    )

    assert response.status_code == 409
    assert "do not match the saved safety and transition settings" in response.json()["detail"]


def test_debug_mill_pallet_motion_dispatches_without_board_changes(client: TestClient, monkeypatch, tmp_path) -> None:
    board = _create_pallet(client, 0)
    configured = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "source_folder": "",
            "program_extensions": [".nc"],
            "weight_unit": "lb",
            "pool_slot_count": 16,
            "robot_connection_mode": "physical",
            "robot_host": "192.168.0.10",
            "pallet_motion_enabled": True,
            "pallet_motion_generation": {
                    "safe_pre_waypoint": _joint_waypoint("Shared Safe Joint Pose")
            },
        },
    )
    assert configured.status_code == 200
    current = configured.json()["board"]
    (tmp_path / "load_mill.script").write_text("def mps_load_mill():\nend\n", encoding="utf-8")
    called = {}
    monkeypatch.setattr(service, "_assert_motion_ready", lambda *_: None)
    monkeypatch.setattr(service, "generated_script_directory", lambda *_: tmp_path)
    monkeypatch.setattr(service, "_mill_motion_script_content", lambda *_: "def mps_load_mill():\nend\n")
    monkeypatch.setattr(service, "run_robot_script", lambda *args: called.update(args=args))

    response = client.post(
        "/api/debug/mill-pallet-motion",
        json={"expected_revision": current["revision"], "operation": "load"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    assert response.json()["program_path"] == "/programs/mongo-production-system/load_mill.script"
    assert called["args"] == ("192.168.0.10", "def mps_load_mill():\nend\n", 1.0)
    unchanged = client.get("/api/board").json()
    assert unchanged["pallets"][0]["location"] == "pool"
    assert unchanged["robot_motion"]["active"] is None
