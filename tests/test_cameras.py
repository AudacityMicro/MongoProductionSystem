from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.cameras import CameraManager, parse_camera_devices, phase_for_run_state


def test_camera_phase_mapping() -> None:
    assert phase_for_run_state("machining") == "machining"
    assert phase_for_run_state("loading") == "loading"
    assert phase_for_run_state("unloading") == "loading"
    assert phase_for_run_state("idle") == "idle"
    assert phase_for_run_state("error") == "idle"


def test_camera_device_config_is_normalized() -> None:
    result = parse_camera_devices(
        [
            {"id": " front ", "name": " Front camera ", "device_index": "2", "enabled": 1},
            {"id": "front", "name": "Duplicate", "device_index": 3},
            {"id": "bad", "device_index": -1},
        ]
    )
    assert result == [
        {"id": "front", "name": "Front camera", "device_index": 2, "enabled": True},
    ]


def test_camera_manager_reports_configured_phase_without_hardware() -> None:
    manager = CameraManager()
    settings = SimpleNamespace(
        camera_devices_json='[{"id":"front","name":"Front","device_index":0,"enabled":false}]',
        camera_idle_id="front",
        camera_loading_id="front",
        camera_machining_id="front",
        camera_recording_enabled=False,
        camera_recording_path="data/camera-recordings",
        camera_recording_retention_days=7,
        camera_width=1920,
        camera_height=1080,
        camera_fps=30,
        camera_segment_seconds=300,
        run_mode_state="machining",
    )
    snapshot = manager.snapshot(settings)
    assert snapshot["phase"] == "machining"
    assert snapshot["assigned_camera_id"] == "front"
    assert snapshot["active_camera_id"] is None
    assert snapshot["fallback"] is False
    assert snapshot["configured_cameras"][0]["name"] == "Front"
    manager.stop()


def test_camera_settings_persist(client: TestClient) -> None:
    board = client.get("/api/settings").json()
    response = client.put(
        "/api/settings",
        json={
            "expected_revision": board["revision"],
            "camera_devices": [{"id": "front", "name": "Front", "device_index": 0, "enabled": False}],
            "camera_idle_id": "front",
            "camera_loading_id": "front",
            "camera_machining_id": "front",
            "camera_recording_enabled": True,
        },
    )
    assert response.status_code == 200
    saved = client.get("/api/settings").json()
    assert saved["settings"]["camera_devices"][0]["name"] == "Front"
    assert saved["settings"]["camera_recording_enabled"] is True
    assert client.get("/api/cameras").json()["assigned_camera_id"] == "front"
