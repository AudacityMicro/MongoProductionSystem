"""USB camera capture, MJPEG streaming, and rolling local recording."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

try:  # pragma: no cover - exercised on the Windows host with the dependency installed
    import cv2
except ImportError:  # pragma: no cover - keeps the API usable before optional install completes
    cv2 = None


PHASES = ("idle", "loading", "machining")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_CAMERA_MODES = (
    {"width": 320, "height": 240, "label": "320 × 240 (QVGA)"},
    {"width": 640, "height": 480, "label": "640 × 480 (VGA)"},
    {"width": 800, "height": 600, "label": "800 × 600 (SVGA)"},
    {"width": 1280, "height": 720, "label": "1280 × 720 (HD / 720p)"},
    {"width": 1280, "height": 960, "label": "1280 × 960"},
    {"width": 1920, "height": 1080, "label": "1920 × 1080 (Full HD / 1080p)"},
    {"width": 2560, "height": 1440, "label": "2560 × 1440 (QHD)"},
    {"width": 3840, "height": 2160, "label": "3840 × 2160 (4K UHD)"},
)


class CameraStorageError(RuntimeError):
    """Raised when a camera recording-folder action cannot be completed safely."""


def resolve_recording_path(value: str | None) -> Path:
    path = Path(value or "data/camera-recordings").expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _validate_purge_path(path: Path) -> None:
    protected = {Path(path.anchor).resolve(), PROJECT_ROOT.resolve(), Path.home().resolve()}
    if path.resolve() in protected:
        raise CameraStorageError("The configured recording folder is too broad to purge safely.")


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(path)], start_new_session=True)


def _folder_usage(path: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                file_count += 1
                byte_count += child.stat().st_size
        except OSError:
            continue
    return file_count, byte_count


def phase_for_run_state(run_mode_state: str | None) -> str:
    state = (run_mode_state or "").strip().lower()
    if state in {"machining", "manual_robot_pause"}:
        return "machining"
    if state in {"loading", "unloading"}:
        return "loading"
    return "idle"


def parse_camera_devices(value: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, list):
        return []
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        camera_id = str(raw.get("id") or f"camera-{index}").strip()[:100]
        if not camera_id or camera_id in seen:
            continue
        try:
            device_index = int(raw.get("device_index", index))
        except (TypeError, ValueError):
            device_index = index
        if device_index < 0 or device_index > 64:
            continue
        seen.add(camera_id)
        devices.append(
            {
                "id": camera_id,
                "name": str(raw.get("name") or f"Camera {device_index + 1}").strip()[:100],
                "device_index": device_index,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return devices


def discover_cameras(max_index: int = 12) -> list[dict[str, Any]]:
    if cv2 is None:
        return []
    found: list[dict[str, Any]] = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if capture.isOpened():
                found.append({"id": f"camera-{index}", "name": f"Camera {index + 1}", "device_index": index, "enabled": True})
        finally:
            capture.release()
    return found


class _CameraWorker:
    def __init__(self, config: dict[str, Any], manager: "CameraManager") -> None:
        self.config = config
        self.manager = manager
        self.stop_event = threading.Event()
        self.frame_condition = threading.Condition()
        self.thread = threading.Thread(target=self._run, name=f"camera-{config['id']}", daemon=True)
        self.jpeg: bytes | None = None
        self.sequence = 0
        self.status = "starting"
        self.error = ""
        self.last_frame_at: str | None = None
        self.recording_file: str | None = None
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None
        self._writer: Any = None
        self._recording_lock = threading.RLock()
        self._segment_started = 0.0
        self._segment_phase = ""

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.frame_condition:
            self.frame_condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self._close_writer()

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.config,
            "status": self.status,
            "error": self.error or None,
            "last_frame_at": self.last_frame_at,
            "recording_file": self.recording_file,
            "requested_width": self.manager.width,
            "requested_height": self.manager.height,
            "requested_fps": self.manager.fps,
            "actual_width": self.actual_width,
            "actual_height": self.actual_height,
            "actual_fps": self.actual_fps,
            "mode_applied": (
                self.actual_width == self.manager.width
                and self.actual_height == self.manager.height
                if self.actual_width is not None and self.actual_height is not None
                else None
            ),
            "stream_url": f"/api/cameras/{self.config['id']}/stream",
        }

    def stream(self) -> Iterator[bytes]:
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
        last_sequence = -1
        while not self.stop_event.is_set():
            with self.frame_condition:
                self.frame_condition.wait_for(
                    lambda: self.stop_event.is_set() or self.sequence != last_sequence,
                    timeout=1.0,
                )
                if self.stop_event.is_set():
                    return
                jpeg = self.jpeg
                last_sequence = self.sequence
            if jpeg:
                yield boundary + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"

    def _open_capture(self):
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed. Install the project dependencies first.")
        capture = cv2.VideoCapture(self.config["device_index"], cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"USB camera index {self.config['device_index']} could not be opened.")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.manager.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.manager.height)
        capture.set(cv2.CAP_PROP_FPS, self.manager.fps)
        self._update_actual_mode(capture)
        return capture

    def _update_actual_mode(self, capture: Any) -> None:
        """Read the mode negotiated by the camera driver after requested settings."""
        if cv2 is None:
            return
        try:
            width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
        except (TypeError, ValueError):
            return
        if width > 0 and height > 0:
            self.actual_width = int(round(width))
            self.actual_height = int(round(height))
        if fps > 0:
            self.actual_fps = round(fps, 2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            capture = None
            try:
                capture = self._open_capture()
                self.status = "online"
                self.error = ""
                while not self.stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        self.status = "offline"
                        self.error = "Camera stopped returning frames."
                        break
                    # Some DirectShow drivers report the negotiated mode only after
                    # the first frame has been acquired.
                    self._update_actual_mode(capture)
                    self._publish(frame)
            except Exception as exc:  # camera failures must not stop the production backend
                self.status = "offline" if cv2 is not None else "unavailable"
                self.error = str(exc)
            finally:
                if capture is not None:
                    capture.release()
                self._close_writer()
            if not self.stop_event.is_set():
                self.stop_event.wait(2.0)

    def _publish(self, frame: Any) -> None:
        if cv2 is None:
            return
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            with self.frame_condition:
                self.jpeg = encoded.tobytes()
                self.sequence += 1
                self.last_frame_at = datetime.now(timezone.utc).isoformat()
                self.frame_condition.notify_all()
        self._record(frame)

    def _record(self, frame: Any) -> None:
        with self._recording_lock:
            if not self.manager.recording_enabled:
                self._close_writer()
                return
            self.manager.recording_path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(self.manager.recording_path)
            if usage.free / max(usage.total, 1) < 0.10:
                self._close_writer()
                self.status = "online-low-disk"
                self.error = "Recording paused because free disk space is below 10%."
                return
            phase = self.manager.phase
            now = time.monotonic()
            if self._writer is None or phase != self._segment_phase or now - self._segment_started >= self.manager.segment_seconds:
                self._close_writer()
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                path = self.manager.recording_path / f"{self.config['id']}_{phase}_{stamp}.mp4"
                writer = cv2.VideoWriter(
                    str(path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    self.manager.fps,
                    (self.manager.width, self.manager.height),
                )
                if not writer.isOpened():
                    writer.release()
                    self.error = "The configured MP4 recording codec could not be opened."
                    return
                self._writer = writer
                self._segment_started = now
                self._segment_phase = phase
                self.recording_file = str(path)
            if frame.shape[1] != self.manager.width or frame.shape[0] != self.manager.height:
                frame = cv2.resize(frame, (self.manager.width, self.manager.height))
            self._writer.write(frame)

    def _close_writer(self) -> None:
        with self._recording_lock:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            self.recording_file = None
            self._segment_phase = ""


class CameraManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.workers: dict[str, _CameraWorker] = {}
        self.configs: list[dict[str, Any]] = []
        self.phase = "idle"
        self.assignment: dict[str, str] = {phase: "" for phase in PHASES}
        self.recording_enabled = False
        self.recording_path = PROJECT_ROOT / "data" / "camera-recordings"
        self.retention_days = 7
        self.width = 1920
        self.height = 1080
        self.fps = 30
        self.segment_seconds = 300
        self._signature: tuple[Any, ...] | None = None
        self._last_cleanup = 0.0

    def apply(self, settings: Any) -> None:
        configs = parse_camera_devices(settings.camera_devices_json)
        path = resolve_recording_path(settings.camera_recording_path)
        signature = (
            tuple((item["id"], item["name"], item["device_index"], item["enabled"]) for item in configs),
            settings.camera_recording_enabled,
            str(path),
            settings.camera_width,
            settings.camera_height,
            settings.camera_fps,
            settings.camera_segment_seconds,
        )
        with self.lock:
            self.phase = phase_for_run_state(settings.run_mode_state)
            self.assignment = {
                "idle": settings.camera_idle_id,
                "loading": settings.camera_loading_id,
                "machining": settings.camera_machining_id,
            }
            self.recording_enabled = bool(settings.camera_recording_enabled)
            self.recording_path = path
            self.retention_days = settings.camera_recording_retention_days
            self.width = settings.camera_width
            self.height = settings.camera_height
            self.fps = settings.camera_fps
            self.segment_seconds = settings.camera_segment_seconds
            self.configs = configs
            if signature != self._signature:
                self._signature = signature
                wanted = {item["id"]: item for item in configs if item["enabled"]}
                for camera_id in list(self.workers):
                    if camera_id not in wanted:
                        self.workers.pop(camera_id).stop()
                for camera_id, config in wanted.items():
                    worker = self.workers.get(camera_id)
                    if worker is None:
                        worker = _CameraWorker(config, self)
                        self.workers[camera_id] = worker
                        worker.start()
                    else:
                        worker.config = config
            if time.monotonic() - self._last_cleanup > 60:
                self._last_cleanup = time.monotonic()
                self._cleanup_old_recordings()

    def stop(self) -> None:
        with self.lock:
            workers = list(self.workers.values())
            self.workers.clear()
        for worker in workers:
            worker.stop()

    def snapshot(self, settings: Any) -> dict[str, Any]:
        self.apply(settings)
        with self.lock:
            by_id = {worker.config["id"]: worker for worker in self.workers.values()}
            assigned_id = self.assignment.get(self.phase, "")
            assigned = by_id.get(assigned_id)
            online = [worker for worker in self.workers.values() if worker.status.startswith("online")]
            selected = assigned if assigned and assigned.status.startswith("online") else (online[0] if online else assigned)
            return {
                "phase": self.phase,
                "assigned_camera_id": assigned_id or None,
                "active_camera_id": selected.config["id"] if selected else None,
                "fallback": bool(selected and (not assigned or selected.config["id"] != assigned.config["id"])),
                "recording_enabled": self.recording_enabled,
                "recording_path": str(self.recording_path),
                "retention_days": self.retention_days,
                "cameras": [worker.snapshot() for worker in self.workers.values()],
                "configured_cameras": self.configs,
            }

    def stream(self, camera_id: str) -> Iterator[bytes]:
        with self.lock:
            worker = self.workers.get(camera_id)
        if worker is None:
            raise KeyError(camera_id)
        return worker.stream()

    def probe_supported_modes(self, settings: Any) -> dict[str, Any]:
        """Report the modes currently negotiated by the live camera workers.

        Repeated DirectShow open/set/release cycles can terminate some webcam
        drivers, so capability probing is intentionally non-invasive. The
        live worker is the authoritative source for the mode actually used by
        the preview.
        """
        result: dict[str, Any] = {
            "common_modes": [dict(mode) for mode in COMMON_CAMERA_MODES],
            "cameras": [],
            "supported_resolutions": [],
        }
        if cv2 is None:
            result["error"] = "OpenCV is not installed."
            return result
        snapshot = self.snapshot(settings)
        cameras = snapshot["cameras"]
        if not cameras:
            result["message"] = "No enabled cameras are configured."
            return result
        for camera in cameras:
            actual = None
            if camera["actual_width"] and camera["actual_height"]:
                actual = next(
                    (
                        dict(mode)
                        for mode in COMMON_CAMERA_MODES
                        if (mode["width"], mode["height"])
                        == (camera["actual_width"], camera["actual_height"])
                    ),
                    {"width": camera["actual_width"], "height": camera["actual_height"], "label": "Negotiated mode"},
                )
            result["cameras"].append({
                "id": camera["id"],
                "name": camera["name"],
                "status": camera["status"],
                "actual_mode": actual,
                "mode_applied": camera["mode_applied"],
            })
        result["message"] = "Read the mode currently negotiated by each live preview. The common resolution list was left unchanged."
        return result

    def open_recording_folder(self, settings: Any) -> dict[str, Any]:
        path = resolve_recording_path(settings.camera_recording_path)
        path.mkdir(parents=True, exist_ok=True)
        try:
            _open_folder(path)
        except OSError as exc:
            raise CameraStorageError(f"The video folder could not be opened: {exc}") from exc
        return {"status": "opened", "path": str(path)}

    def purge_recording_folder(self, settings: Any) -> dict[str, Any]:
        requested_path = resolve_recording_path(settings.camera_recording_path)
        _validate_purge_path(requested_path)
        self.apply(settings)
        with self.lock:
            path = self.recording_path.resolve()
            path.mkdir(parents=True, exist_ok=True)
            recording_was_enabled = self.recording_enabled
            self.recording_enabled = False
            workers = list(self.workers.values())
            try:
                for worker in workers:
                    worker._close_writer()
                deleted_files, freed_bytes = _folder_usage(path)
                deleted_directories = 0
                for child in list(path.iterdir()):
                    if child.is_symlink() or child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                        deleted_directories += 1
            except OSError as exc:
                raise CameraStorageError(f"The video folder could not be purged: {exc}") from exc
            finally:
                self.recording_enabled = recording_was_enabled
        return {
            "status": "purged",
            "path": str(path),
            "deleted_files": deleted_files,
            "deleted_directories": deleted_directories,
            "freed_bytes": freed_bytes,
        }

    def _cleanup_old_recordings(self) -> None:
        if not self.recording_path.exists():
            return
        cutoff = time.time() - self.retention_days * 86400
        for path in self.recording_path.glob("*.mp4"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


_MANAGER = CameraManager()


def camera_manager() -> CameraManager:
    return _MANAGER
