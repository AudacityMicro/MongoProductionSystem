from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.cameras import CameraManager, CameraStorageError, camera_manager, parse_camera_devices, phase_for_run_state


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


def test_camera_mode_probe_is_non_invasive_without_enabled_hardware() -> None:
    manager = CameraManager()
    settings = SimpleNamespace(
        camera_devices_json='[{"id":"front","name":"Front","device_index":0,"enabled":false}]',
        camera_idle_id="front",
        camera_loading_id="front",
        camera_machining_id="front",
        camera_recording_enabled=False,
        camera_recording_path="data/camera-recordings",
        camera_recording_retention_days=7,
        camera_width=320,
        camera_height=240,
        camera_fps=30,
        camera_segment_seconds=300,
        run_mode_state="idle",
    )
    result = manager.probe_supported_modes(settings)
    assert result["cameras"] == []
    assert result["supported_resolutions"] == []
    assert result["message"] == "No enabled cameras are configured."
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


def test_camera_recording_folder_can_be_purged_without_removing_folder(tmp_path) -> None:
    manager = CameraManager()
    recording_path = tmp_path / "recordings"
    nested = recording_path / "nested"
    nested.mkdir(parents=True)
    (recording_path / "clip.mp4").write_bytes(b"video")
    (nested / "metadata.txt").write_bytes(b"details")
    settings = SimpleNamespace(
        camera_devices_json="[]",
        camera_idle_id="",
        camera_loading_id="",
        camera_machining_id="",
        camera_recording_enabled=True,
        camera_recording_path=str(recording_path),
        camera_recording_retention_days=7,
        camera_width=1920,
        camera_height=1080,
        camera_fps=30,
        camera_segment_seconds=300,
        run_mode_state="idle",
    )

    result = manager.purge_recording_folder(settings)

    assert result["deleted_files"] == 2
    assert result["deleted_directories"] == 1
    assert result["freed_bytes"] == 12
    assert recording_path.exists()
    assert list(recording_path.iterdir()) == []
    assert manager.recording_enabled is True


def test_camera_recording_purge_refuses_home_folder() -> None:
    manager = CameraManager()
    settings = SimpleNamespace(
        camera_devices_json="[]",
        camera_idle_id="",
        camera_loading_id="",
        camera_machining_id="",
        camera_recording_enabled=False,
        camera_recording_path=str(Path.home()),
        camera_recording_retention_days=7,
        camera_width=1920,
        camera_height=1080,
        camera_fps=30,
        camera_segment_seconds=300,
        run_mode_state="idle",
    )

    try:
        manager.purge_recording_folder(settings)
    except CameraStorageError as exc:
        assert "too broad" in str(exc)
    else:
        raise AssertionError("Expected the home-folder purge guard to reject the request.")


def test_camera_recording_folder_endpoints_require_confirmation(client: TestClient, monkeypatch) -> None:
    manager = camera_manager()
    monkeypatch.setattr(
        manager,
        "open_recording_folder",
        lambda settings: {"status": "opened", "path": "C:\\recordings"},
    )
    monkeypatch.setattr(
        manager,
        "purge_recording_folder",
        lambda settings: {
            "status": "purged",
            "path": "C:\\recordings",
            "deleted_files": 2,
            "deleted_directories": 0,
            "freed_bytes": 42,
        },
    )

    open_response = client.post("/api/cameras/recordings/open")
    refused_response = client.post("/api/cameras/recordings/purge", json={"confirmed": False})
    purge_response = client.post("/api/cameras/recordings/purge", json={"confirmed": True})

    assert open_response.status_code == 200
    assert open_response.json()["status"] == "opened"
    assert refused_response.status_code == 400
    assert purge_response.status_code == 200
    assert purge_response.json()["deleted_files"] == 2
