from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import threading
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import __version__
from app.database import (
    create_database_engine,
    create_session_factory,
    run_migrations,
)
from app.schemas import (
    CreatePallet,
    CncTelemetryConnectionTest,
    MovePallet,
    RecoverPalletMotion,
    ConfigureDebugProgram,
    ConfigureDebugMillProgram,
    ConfirmRunModeAction,
    QueuePallet,
    RenameDebugIo,
    ReorderQueue,
    RevisionRequest,
    RobotFileAction,
    SettingsUpdate,
    SetRunModeSafety,
    StartRunMode,
    StartMillPalletTransfer,
    StartPalletMotion,
    ToggleDebugIo,
    RunDebugProgram,
    RunDebugMillProgram,
    RunDebugMillPalletMotion,
    RunDebugPalletMotion,
    UpdatePallet,
)
from app.service import (
    board_snapshot,
    autoschedule_queue_preview,
    cnc_debug_snapshot,
    cnc_io_labels_snapshot,
    test_cnc_telemetry_connection,
    add_fusion_tool_library,
    dashboard_snapshot,
    tools_snapshot,
    create_pallet,
    configure_debug_program,
    configure_debug_mill_program,
    dequeue_pallet,
    delete_pallet,
    duplicate_pallet,
    move_pallet,
    execute_pallet_motion,
    queue_pallet,
    refresh_programs,
    rename_debug_io,
    reorder_queue,
    current_robot_pose,
    robot_io_snapshot,
    robot_file_manager_settings,
    robot_programs_page_settings,
    mill_file_manager_settings,
    mill_programs_page_settings,
    robot_program_files,
    remove_fusion_tool_library,
    run_debug_program,
    run_debug_mill_program,
    mill_program_files,
    run_debug_mill_pallet_motion,
    run_debug_pallet_motion,
    simulate_signal,
    toggle_debug_io,
    update_pallet,
    update_settings,
    start_pallet_motion,
    start_mill_pallet_transfer,
    recover_pallet_motion,
    interrupt_active_pallet_motion,
    interrupt_run_mode,
    execute_run_mode,
    start_run_mode,
    stop_run_mode,
    set_run_mode_safety,
    confirm_run_mode_action,
    rebuild_pallet_motion_scripts,
    rebuild_mill_load_position_program,
)
from app.robot_files import (
    RobotFileAccessError,
    RobotFileConflict,
    copy_robot_file,
    create_robot_directory,
    delete_robot_path,
    download_robot_file,
    list_robot_directory,
    move_robot_file,
    rename_robot_file,
    read_robot_file,
    upload_robot_file,
)
from app.settings import settings


STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = STATIC_DIR.parent.parent
STARTED_AT = datetime.now(timezone.utc).isoformat()


def queue_backend_relaunch() -> None:
    helper = PROJECT_ROOT / "restart_backend.ps1"
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
        ],
        cwd=PROJECT_ROOT,
        # A separate process group survives the current Uvicorn worker. Using
        # DETACHED_PROCESS here prevented PowerShell from executing on Windows.
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def resolve_editor_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=False)
    if not parts:
        raise OSError("Configure an editor command in Settings first.")
    if parts[0].casefold() == "code":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code" / "Code.exe"
        if candidate.is_file():
            return [str(candidate), *parts[1:]]
    if shutil.which(parts[0]):
        return parts
    raise OSError(f"Editor command was not found: {parts[0]}")


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or settings.database_url

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        run_migrations(url)
        engine = create_database_engine(url)
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        with application.state.session_factory() as session:
            interrupt_active_pallet_motion(session)
            interrupt_run_mode(session)
        yield
        engine.dispose()

    application = FastAPI(
        title="Mongo Production System API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.middleware("http")
    async def prevent_stale_frontend_assets(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/settings", "/debugging", "/robot-programs", "/mill-programs", "/dashboard", "/tools"} or path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    def get_session(request: Request) -> Generator[Session, None, None]:
        with request.app.state.session_factory() as session:
            yield session

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/settings", include_in_schema=False)
    def settings_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "settings.html")

    @application.get("/debugging", include_in_schema=False)
    def debugging_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "debugging.html")

    @application.get("/dashboard", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

    @application.get("/tools", include_in_schema=False)
    def tools_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "tools.html")

    @application.get("/robot-programs", include_in_schema=False)
    def robot_programs_page(session: Session = Depends(get_session)) -> FileResponse:
        robot_programs_page_settings(session)
        return FileResponse(STATIC_DIR / "robot-programs.html")

    @application.get("/mill-programs", include_in_schema=False)
    def mill_programs_page(session: Session = Depends(get_session)) -> FileResponse:
        mill_programs_page_settings(session)
        return FileResponse(STATIC_DIR / "mill-programs.html")

    @application.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "process_id": os.getpid(),
            "started_at": STARTED_AT,
        }

    @application.get("/api/board")
    def get_board(session: Session = Depends(get_session)) -> dict:
        return board_snapshot(session)

    @application.post("/api/run-mode/start", status_code=status.HTTP_202_ACCEPTED)
    def start_production_run_mode(
        payload: StartRunMode,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict:
        start_run_mode(session, payload)
        threading.Thread(
            target=execute_run_mode,
            args=(request.app.state.session_factory,),
            daemon=True,
            name="production-run-mode",
        ).start()
        return board_snapshot(session)

    @application.post("/api/run-mode/stop")
    def stop_production_run_mode(
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        stop_run_mode(session, payload.expected_revision)
        return board_snapshot(session)

    @application.post("/api/run-mode/safety")
    def update_run_mode_safety(
        payload: SetRunModeSafety,
        session: Session = Depends(get_session),
    ) -> dict:
        set_run_mode_safety(session, payload)
        return board_snapshot(session)

    @application.post("/api/run-mode/confirm")
    def confirm_production_run_mode_action(
        payload: ConfirmRunModeAction,
        session: Session = Depends(get_session),
    ) -> dict:
        confirm_run_mode_action(session, payload)
        return board_snapshot(session)

    @application.get("/api/dashboard")
    def get_dashboard(session: Session = Depends(get_session)) -> dict:
        return dashboard_snapshot(session)

    @application.get("/api/tools")
    def get_tools(session: Session = Depends(get_session)) -> dict:
        return tools_snapshot(session)

    @application.post("/api/tool-libraries/upload")
    def upload_fusion_tool_libraries(
        files: list[UploadFile] = File(...),
        session: Session = Depends(get_session),
    ) -> dict:
        library_directory = PROJECT_ROOT / "runtime" / "fusion-tool-libraries"
        library_directory.mkdir(parents=True, exist_ok=True)
        added: list[str] = []
        try:
            for file in files:
                original_name = Path(file.filename or "").name
                if not original_name or Path(original_name).suffix.lower() not in {".json", ".tools"}:
                    raise HTTPException(status_code=422, detail="Upload Fusion tool library files ending in .json or .tools.")
                content = file.file.read(10_000_001)
                if len(content) > 10_000_000:
                    raise HTTPException(status_code=422, detail="Fusion tool library uploads are limited to 10 MB each.")
                target = library_directory / f"{uuid4().hex}_{original_name}"
                target.write_bytes(content)
                add_fusion_tool_library(session, str(target))
                added.append(str(target))
            return {"libraries": added, "tools": tools_snapshot(session)}
        finally:
            for file in files:
                file.file.close()

    @application.delete("/api/tool-libraries")
    def delete_fusion_tool_library(
        path: str = Query(min_length=1, max_length=1000),
        session: Session = Depends(get_session),
    ) -> dict:
        library_directory = (PROJECT_ROOT / "runtime" / "fusion-tool-libraries").resolve()
        target = Path(path).resolve()
        if library_directory not in target.parents:
            raise HTTPException(status_code=422, detail="Only uploaded Fusion tool libraries can be removed here.")
        remove_fusion_tool_library(session, str(target))
        target.unlink(missing_ok=True)
        return {"tools": tools_snapshot(session)}

    def robot_file_connection(session: Session) -> tuple[dict, object]:
        robot_settings = robot_file_manager_settings(session)
        return (
            {
                "host": robot_settings.robot_file_host or robot_settings.robot_host.strip(),
                "port": robot_settings.robot_file_port,
                "username": robot_settings.robot_file_username,
                "password": robot_settings.robot_file_password,
                "directory": robot_settings.robot_file_directory,
                "timeout_seconds": robot_settings.robot_timeout_seconds,
            },
            robot_settings,
        )

    def robot_file_error(error: RobotFileAccessError) -> HTTPException:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error))

    def mill_file_connection(session: Session) -> tuple[dict, object]:
        mill_settings = mill_file_manager_settings(session)
        return (
            {
                "host": mill_settings.cnc_host.strip(),
                "port": mill_settings.cnc_ssh_port,
                "username": mill_settings.cnc_ssh_username,
                "password": mill_settings.cnc_ssh_password,
                "directory": mill_settings.mill_file_directory,
                "timeout_seconds": mill_settings.cnc_timeout_seconds,
            },
            mill_settings,
        )

    def mill_file_error(error: RobotFileAccessError) -> HTTPException:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error))

    @application.post("/api/pallets", status_code=status.HTTP_201_CREATED)
    def add_pallet(
        payload: CreatePallet,
        session: Session = Depends(get_session),
    ) -> dict:
        create_pallet(session, payload)
        return board_snapshot(session)

    @application.put("/api/pallets/{pallet_id}")
    def edit_pallet(
        pallet_id: str,
        payload: UpdatePallet,
        session: Session = Depends(get_session),
    ) -> dict:
        update_pallet(session, pallet_id, payload)
        return board_snapshot(session)

    @application.post("/api/pallets/{pallet_id}/duplicate")
    def copy_pallet(
        pallet_id: str,
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        duplicate_pallet(session, pallet_id, payload.expected_revision)
        return board_snapshot(session)

    @application.delete("/api/pallets/{pallet_id}")
    def remove_pallet(
        pallet_id: str,
        expected_revision: int = Query(ge=0),
        session: Session = Depends(get_session),
    ) -> dict:
        delete_pallet(session, pallet_id, expected_revision)
        return board_snapshot(session)

    @application.post("/api/pallets/{pallet_id}/move")
    def relocate_pallet(
        pallet_id: str,
        payload: MovePallet,
        session: Session = Depends(get_session),
    ) -> dict:
        move_pallet(session, pallet_id, payload)
        return board_snapshot(session)

    @application.post("/api/robot-motions", status_code=status.HTTP_202_ACCEPTED)
    def start_robot_pallet_motion(
        payload: StartPalletMotion,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict:
        motion_id = start_pallet_motion(session, payload)
        if motion_id:
            threading.Thread(
                target=execute_pallet_motion,
                args=(request.app.state.session_factory, motion_id),
                daemon=True,
                name=f"pallet-motion-{motion_id[:8]}",
            ).start()
        return board_snapshot(session)

    @application.post("/api/robot-motions/mill-transfer", status_code=status.HTTP_202_ACCEPTED)
    def start_mill_pallet_transfer_motion(
        payload: StartMillPalletTransfer,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict:
        motion_id = start_mill_pallet_transfer(session, payload)
        if motion_id:
            threading.Thread(
                target=execute_pallet_motion,
                args=(request.app.state.session_factory, motion_id),
                daemon=True,
                name=f"mill-transfer-{motion_id[:8]}",
            ).start()
        return board_snapshot(session)

    @application.post("/api/robot-motions/{motion_id}/recover")
    def recover_robot_pallet_motion(
        motion_id: str,
        payload: RecoverPalletMotion,
        session: Session = Depends(get_session),
    ) -> dict:
        recover_pallet_motion(session, motion_id, payload)
        return board_snapshot(session)

    @application.post("/api/robot-motions/rebuild-scripts")
    def rebuild_robot_pallet_motion_scripts(
        session: Session = Depends(get_session),
    ) -> dict:
        result = rebuild_pallet_motion_scripts(session)
        return {"board": board_snapshot(session), **result}

    @application.post("/api/mill-programs/rebuild-load-position")
    def rebuild_mill_load_position(
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        return rebuild_mill_load_position_program(session, payload.expected_revision)

    @application.post("/api/pallets/{pallet_id}/queue")
    def add_to_queue(
        pallet_id: str,
        payload: QueuePallet,
        session: Session = Depends(get_session),
    ) -> dict:
        queue_pallet(session, pallet_id, payload)
        return board_snapshot(session)

    @application.delete("/api/pallets/{pallet_id}/queue")
    def remove_from_queue(
        pallet_id: str,
        expected_revision: int = Query(ge=0),
        session: Session = Depends(get_session),
    ) -> dict:
        dequeue_pallet(session, pallet_id, expected_revision)
        return board_snapshot(session)

    @application.put("/api/queue")
    def set_queue_order(
        payload: ReorderQueue,
        session: Session = Depends(get_session),
    ) -> dict:
        reorder_queue(session, payload)
        return board_snapshot(session)

    @application.post("/api/queue/autoschedule/preview")
    def preview_queue_autoschedule(
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        return autoschedule_queue_preview(session, payload.expected_revision)

    @application.get("/api/settings")
    def get_application_settings(
        session: Session = Depends(get_session),
    ) -> dict:
        return board_snapshot(session)

    @application.put("/api/settings")
    def save_application_settings(
        payload: SettingsUpdate,
        session: Session = Depends(get_session),
    ) -> dict:
        cleared = update_settings(session, payload)
        return {"board": board_snapshot(session), "cleared_assignments": cleared}

    @application.post("/api/programs/refresh")
    def scan_programs(
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        cleared = refresh_programs(session, payload.expected_revision)
        return {"board": board_snapshot(session), "cleared_assignments": cleared}

    @application.post("/api/debug/signals/{signal}")
    def send_debug_signal(
        signal: str,
        payload: RevisionRequest,
        session: Session = Depends(get_session),
    ) -> dict:
        simulate_signal(session, signal, payload.expected_revision)
        return board_snapshot(session)

    @application.post("/api/debug/pallet-motion")
    def run_debug_pallet_motion_test(
        payload: RunDebugPalletMotion,
        session: Session = Depends(get_session),
    ) -> dict:
        return run_debug_pallet_motion(session, payload)

    @application.post("/api/debug/mill-pallet-motion")
    def run_debug_mill_pallet_motion_test(
        payload: RunDebugMillPalletMotion,
        session: Session = Depends(get_session),
    ) -> dict:
        return run_debug_mill_pallet_motion(session, payload)

    @application.get("/api/debug/robot-io")
    def get_robot_io(session: Session = Depends(get_session)) -> dict:
        return robot_io_snapshot(session)

    @application.get("/api/debug/robot-pose")
    def get_current_robot_pose(session: Session = Depends(get_session)) -> dict:
        return current_robot_pose(session)

    @application.get("/api/debug/cnc")
    def get_cnc_debug(session: Session = Depends(get_session)) -> dict:
        return cnc_debug_snapshot(session)

    @application.get("/api/debug/cnc/io-labels")
    def get_cnc_io_labels(session: Session = Depends(get_session)) -> dict:
        return cnc_io_labels_snapshot(session)

    @application.post("/api/debug/cnc/test")
    def test_cnc_debug_connection(payload: CncTelemetryConnectionTest) -> dict:
        return test_cnc_telemetry_connection(
            payload.host,
            payload.port,
            payload.username,
            payload.password,
            payload.timeout_seconds,
        )

    @application.post("/api/debug/io/toggle")
    def toggle_debug_io_value(
        payload: ToggleDebugIo,
        session: Session = Depends(get_session),
    ) -> dict:
        toggle_debug_io(session, payload)
        return robot_io_snapshot(session)

    @application.post("/api/debug/io/label")
    def rename_debug_io_value(
        payload: RenameDebugIo,
        session: Session = Depends(get_session),
    ) -> dict:
        rename_debug_io(session, payload)
        return robot_io_snapshot(session)

    @application.post("/api/debug/programs/configure")
    def configure_debug_program_button(
        payload: ConfigureDebugProgram,
        session: Session = Depends(get_session),
    ) -> dict:
        configure_debug_program(session, payload)
        return robot_io_snapshot(session)

    @application.post("/api/debug/mill-programs/configure")
    def configure_debug_mill_program_button(
        payload: ConfigureDebugMillProgram,
        session: Session = Depends(get_session),
    ) -> dict:
        configure_debug_mill_program(session, payload)
        return cnc_debug_snapshot(session)

    @application.get("/api/debug/programs/files")
    def get_debug_program_files(
        include_all: bool = False,
        session: Session = Depends(get_session),
    ) -> dict:
        return {"files": robot_program_files(session, include_all=include_all)}

    @application.get("/api/debug/mill-programs/files")
    def get_debug_mill_program_files(session: Session = Depends(get_session)) -> dict:
        return {"files": mill_program_files(session)}

    @application.get("/api/robot-files")
    def get_robot_files(
        path: str | None = Query(default=None, max_length=1000),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, robot_settings = robot_file_connection(session)
        try:
            return list_robot_directory(
                path=path,
                extensions=set(json.loads(robot_settings.robot_program_extensions))
                if robot_settings.robot_programs_filter_enabled else None,
                **connection,
            )
        except RobotFileAccessError as error:
            raise robot_file_error(error) from error

    @application.get("/api/robot-files/preview")
    def preview_robot_file(
        path: str = Query(min_length=1, max_length=1000),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, _ = robot_file_connection(session)
        try:
            return read_robot_file(path=path, **connection)
        except RobotFileAccessError as error:
            raise robot_file_error(error) from error

    @application.get("/api/robot-files/download")
    def download_robot_program_file(
        path: str = Query(min_length=1, max_length=1000),
        session: Session = Depends(get_session),
    ) -> StreamingResponse:
        connection, _ = robot_file_connection(session)
        try:
            name, content = download_robot_file(path=path, **connection)
        except RobotFileAccessError as error:
            raise robot_file_error(error) from error
        return StreamingResponse(
            iter([content]),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @application.post("/api/robot-files/upload")
    def upload_robot_program_file(
        file: UploadFile = File(...),
        destination_directory: str = Form(default=""),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, _ = robot_file_connection(session)
        try:
            path = upload_robot_file(
                destination=destination_directory,
                filename=file.filename or "",
                content=file.file,
                **connection,
            )
            return {"path": path}
        except RobotFileAccessError as error:
            raise robot_file_error(error) from error
        finally:
            file.file.close()

    @application.post("/api/robot-files/action")
    def manage_robot_program_file(
        payload: RobotFileAction,
        session: Session = Depends(get_session),
    ) -> dict:
        connection, robot_settings = robot_file_connection(session)
        try:
            if payload.action == "copy":
                path = copy_robot_file(source=payload.path, destination_directory=payload.destination_directory, conflict_strategy=payload.conflict_strategy, **connection)
                return {"path": path, "skipped": path is None}
            if payload.action == "move":
                path = move_robot_file(source=payload.path, destination_directory=payload.destination_directory, conflict_strategy=payload.conflict_strategy, **connection)
                return {"path": path, "skipped": path is None}
            if payload.action == "rename":
                return {"path": rename_robot_file(path=payload.path, name=payload.name, **connection)}
            if payload.action == "delete":
                delete_robot_path(path=payload.path, **connection)
                return {"deleted": payload.path}
            if payload.action == "create_folder":
                return {"path": create_robot_directory(parent=payload.destination_directory, name=payload.folder_name, **connection)}
            name, content = download_robot_file(path=payload.path, **connection)
            command = resolve_editor_command(robot_settings.robot_editor_command)
            editor_directory = PROJECT_ROOT / "runtime" / "robot-editor"
            editor_directory.mkdir(parents=True, exist_ok=True)
            local_path = editor_directory / name
            local_path.write_bytes(content)
            subprocess.Popen(command + [str(local_path)], cwd=PROJECT_ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
            return {"path": payload.path, "local_path": str(local_path)}
        except RobotFileConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"message": str(error), "destination": error.destination}) from error
        except RobotFileAccessError as error:
            raise robot_file_error(error) from error
        except OSError as error:
            raise HTTPException(status_code=422, detail=f"Could not open the editor: {error}") from error

    @application.get("/api/mill-files")
    def get_mill_files(
        path: str | None = Query(default=None, max_length=1000),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, mill_settings = mill_file_connection(session)
        try:
            return list_robot_directory(
                path=path,
                extensions=set(json.loads(mill_settings.mill_program_extensions))
                if mill_settings.mill_programs_filter_enabled else None,
                **connection,
            )
        except RobotFileAccessError as error:
            raise mill_file_error(error) from error

    @application.get("/api/mill-files/preview")
    def preview_mill_file(
        path: str = Query(min_length=1, max_length=1000),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, _ = mill_file_connection(session)
        try:
            return read_robot_file(path=path, **connection)
        except RobotFileAccessError as error:
            raise mill_file_error(error) from error

    @application.get("/api/mill-files/download")
    def download_mill_program_file(
        path: str = Query(min_length=1, max_length=1000),
        session: Session = Depends(get_session),
    ) -> StreamingResponse:
        connection, _ = mill_file_connection(session)
        try:
            name, content = download_robot_file(path=path, **connection)
        except RobotFileAccessError as error:
            raise mill_file_error(error) from error
        return StreamingResponse(
            iter([content]),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @application.post("/api/mill-files/upload")
    def upload_mill_program_file(
        file: UploadFile = File(...),
        destination_directory: str = Form(default=""),
        session: Session = Depends(get_session),
    ) -> dict:
        connection, _ = mill_file_connection(session)
        try:
            path = upload_robot_file(
                destination=destination_directory,
                filename=file.filename or "",
                content=file.file,
                **connection,
            )
            return {"path": path}
        except RobotFileAccessError as error:
            raise mill_file_error(error) from error
        finally:
            file.file.close()

    @application.post("/api/mill-files/action")
    def manage_mill_program_file(
        payload: RobotFileAction,
        session: Session = Depends(get_session),
    ) -> dict:
        connection, mill_settings = mill_file_connection(session)
        try:
            if payload.action == "copy":
                path = copy_robot_file(source=payload.path, destination_directory=payload.destination_directory, conflict_strategy=payload.conflict_strategy, **connection)
                return {"path": path, "skipped": path is None}
            if payload.action == "move":
                path = move_robot_file(source=payload.path, destination_directory=payload.destination_directory, conflict_strategy=payload.conflict_strategy, **connection)
                return {"path": path, "skipped": path is None}
            if payload.action == "rename":
                return {"path": rename_robot_file(path=payload.path, name=payload.name, **connection)}
            if payload.action == "delete":
                delete_robot_path(path=payload.path, **connection)
                return {"deleted": payload.path}
            if payload.action == "create_folder":
                return {"path": create_robot_directory(parent=payload.destination_directory, name=payload.folder_name, **connection)}
            name, content = download_robot_file(path=payload.path, **connection)
            command = resolve_editor_command(mill_settings.mill_editor_command)
            editor_directory = PROJECT_ROOT / "runtime" / "mill-editor"
            editor_directory.mkdir(parents=True, exist_ok=True)
            local_path = editor_directory / name
            local_path.write_bytes(content)
            subprocess.Popen(command + [str(local_path)], cwd=PROJECT_ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
            return {"path": payload.path, "local_path": str(local_path)}
        except RobotFileConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"message": str(error), "destination": error.destination}) from error
        except RobotFileAccessError as error:
            raise mill_file_error(error) from error
        except OSError as error:
            raise HTTPException(status_code=422, detail=f"Could not open the editor: {error}") from error

    @application.post("/api/debug/programs/run")
    def run_debug_program_button(
        payload: RunDebugProgram,
        session: Session = Depends(get_session),
    ) -> dict:
        run_debug_program(session, payload)
        return robot_io_snapshot(session)

    @application.post("/api/debug/mill-programs/run")
    def run_debug_mill_program_button(
        payload: RunDebugMillProgram,
        session: Session = Depends(get_session),
    ) -> dict:
        run_debug_mill_program(session, payload)
        return cnc_debug_snapshot(session)

    @application.post("/api/system/relaunch", status_code=status.HTTP_202_ACCEPTED)
    def relaunch_system() -> dict[str, str]:
        queue_backend_relaunch()
        return {
            "status": "relaunching",
            "message": "Backend relaunch has been queued.",
            "version": __version__,
        }

    return application


app = create_app()
