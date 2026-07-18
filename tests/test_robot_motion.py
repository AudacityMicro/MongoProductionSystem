from fastapi.testclient import TestClient

from app import service
from app.robot_scripts import build_mill_pallet_motion_script, build_pallet_motion_script
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
        "state_rows": [{"label": "Safety mode", "value": 1}, {"label": "Runtime state", "value": 1}],
    }


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
                "safe_pre_waypoint": {"name": "Safe Pre", "x_mm": 0, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
                "safe_post_waypoint": {"name": "Safe Post", "x_mm": 50, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
                "travel_waypoints": [{"name": "Home", "x_mm": 100, "y_mm": 200, "z_mm": 300, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}],
            },
        },
    )
    assert configured.status_code == 200

    def fake_sync(**kwargs):
        assert len(kwargs["files"]) == 38
        assert "pick_pool_001.script" in kwargs["files"]
        assert "load_mill.script" in kwargs["files"]
        assert "unload_mill.script" in kwargs["files"]
        assert "movel(p[0.000000,0.120000,0.000000" in kwargs["files"]["pick_pool_001.script"]
        assert "movel(p[0.500000,-0.300000,0.500000" in kwargs["files"]["load_mill.script"]
        return {name: f"/programs/mongo-production-system/{name}" for name in kwargs["files"]}

    monkeypatch.setattr(service, "sync_generated_scripts", fake_sync)
    rebuilt = client.post("/api/robot-motions/rebuild-scripts")

    assert rebuilt.status_code == 200
    payload = rebuilt.json()
    assert len(payload["files"]) == 38
    mapping = payload["board"]["settings"]["pallet_motion_programs"][0]
    assert mapping["pick_program"].endswith("pick_pool_001.script")
    assert mapping["put_program"].endswith("put_pool_001.script")
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
                "safe_pre_waypoint": {"name": "Safe Pre", "x_mm": 0, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
                "safe_post_waypoint": {"name": "Safe Post", "x_mm": 50, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
                "travel_waypoints": [{"name": "Home", "x_mm": 100, "y_mm": 200, "z_mm": 300, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}],
            },
        },
    )
    assert stale.status_code == 200
    assert stale.json()["board"]["settings"]["motion_scripts_need_rebuild"] is True


def test_generated_script_uses_waypoints_gripper_and_latched_result() -> None:
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
            "safe_pre_waypoint": {"name": "Safe Pre", "x_mm": -100, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "safe_post_waypoint": {"name": "Safe Post", "x_mm": 100, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "travel_waypoints": [{"name": "Home", "x_mm": 0, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}],
        },
    )

    assert "movej(p[0.000000,0.000000,0.500000" in script
    assert "movej(p[-0.100000,0.000000,0.500000" in script
    assert "movel(p[0.100000,0.300000,0.300000" in script
    assert "movel(p[0.100000,0.200000,0.400000" in script
    assert script.count("movej(p[-0.100000,0.000000,0.500000") == 2
    assert "set_configurable_digital_out(4, True)" in script
    assert "set_standard_digital_out(0, True)" not in script
    assert "set_standard_digital_out(1, True)" not in script
    assert "# Pallet approach position | X=100.000 mm, Y=300.000 mm, Z=300.000 mm" in script
    assert script.count("# ") >= script.count("movej(") + script.count("movel(")


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
            "safe_pre_waypoint": {"name": "Safe Pre", "x_mm": -100, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "safe_post_waypoint": {"name": "Safe Post", "x_mm": 100, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "travel_waypoints": [],
        },
    )

    assert "digital_out" not in script
    assert "movel(p[0.100000,0.300000,0.300000" in script


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
            "safe_pre_waypoint": {"name": "Safe", "x_mm": -100, "y_mm": 0, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
            "travel_waypoints": [],
        },
    )

    above = script.index("movel(p[0.100000,0.200000,0.400000")
    setdown = script.index("movel(p[0.100000,0.200000,0.300000")
    withdraw = script.index("movel(p[0.100000,0.300000,0.300000")
    assert above < setdown < withdraw


def test_generated_mill_load_and_unload_scripts_use_the_mill_clearance_pose() -> None:
    generation = {
        "approach_y_clearance_mm": 100,
        "mill_approach_x_clearance_mm": 120,
        "lift_z_clearance_mm": 100,
        "max_travel_speed_rad_s": 0.6,
        "pickup_setdown_speed_m_s": 0.08,
        "grip_output": None,
        "grip_closed_value": True,
        "door_open_action": {"output": {"bank": "configurable", "index": 0}, "active_value": True, "pulse": True},
        "door_close_action": {"output": {"bank": "configurable", "index": 1}, "active_value": True, "pulse": True},
        "erowa_unlock_action": {"output": {"bank": "configurable", "index": 2}, "active_value": False, "pulse": False},
        "erowa_lock_action": {"output": {"bank": "configurable", "index": 2}, "active_value": True, "pulse": False},
        "mill_actuation_wait_seconds": 1.5,
        "safe_pre_waypoint": {"name": "Cell safe", "x_mm": 0, "y_mm": 0, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0},
        "travel_waypoints": [],
    }
    mill_pose = {"x_mm": 400, "y_mm": -300, "z_mm": 500, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}
    clearance = {"x_mm": 200, "y_mm": -300, "z_mm": 600, "rx_rad": 3.14, "ry_rad": 0, "rz_rad": 0}

    load = build_mill_pallet_motion_script(
        function_name="mps_load_mill", operation="load", mill_pose=mill_pose, entry_exit_pose=clearance, generation=generation,
    )
    unload = build_mill_pallet_motion_script(
        function_name="mps_unload_mill", operation="unload", mill_pose=mill_pose, entry_exit_pose=clearance, generation=generation,
    )

    linear_entry = "movel(p[0.200000,-0.300000,0.600000"
    assert load.count(linear_entry) == 2
    assert unload.count(linear_entry) == 2
    assert load.index("movel(p[0.400000,-0.300000,0.500000") < load.index("movel(p[0.520000,-0.300000,0.500000")
    assert unload.index("movel(p[0.520000,-0.300000,0.500000") < unload.index("movel(p[0.400000,-0.300000,0.500000")
    load_withdraw = "movel(p[0.520000,-0.300000,0.500000"
    unload_lift = "movel(p[0.400000,-0.300000,0.600000"
    unload_withdraw = "movel(p[0.520000,-0.300000,0.600000"
    assert load.index(load_withdraw) < load.rindex(linear_entry)
    assert unload.index(unload_lift) < unload.index(unload_withdraw) < unload.rindex(linear_entry)
    assert "# Withdraw fork in positive X | X=520.000 mm" in load
    assert "# Withdraw lifted pallet in positive X | X=520.000 mm" in unload
    assert "# Linear retract to mill entry/exit" in load
    assert "# Linear return to Cell safe" in load
    assert load.index("# Open mill door") < load.index("# Linear mill entry")
    assert load.index("# Unlock Erowa system") < load.index("# Mill pallet position from above")
    assert load.index("# Lock Erowa system") < load.index("# Close mill door")
    assert load.index("# Close mill door") < load.index("# Linear return to Cell safe")
    assert "set_configurable_digital_out(0, True)" in load
    assert "set_configurable_digital_out(0, False)" in load
    assert "set_configurable_digital_out(2, False)" in load
    assert "set_configurable_digital_out(2, True)" in load
    assert "# Mill pallet setdown position | X=400.000 mm, Y=-300.000 mm, Z=500.000 mm" in load
    assert load.count("# ") >= load.count("movej(") + load.count("movel(")


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
                "safe_pre_waypoint": {
                    "name": "Shared Safe Waypoint",
                    "x_mm": 0,
                    "y_mm": 0,
                    "z_mm": 500,
                    "rx_rad": 0,
                    "ry_rad": 0,
                    "rz_rad": 0,
                }
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
