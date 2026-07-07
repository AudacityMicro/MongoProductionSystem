from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import FileResponse
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
    MovePallet,
    QueuePallet,
    ReorderQueue,
    RevisionRequest,
    SettingsUpdate,
    UpdatePallet,
)
from app.service import (
    board_snapshot,
    create_pallet,
    dequeue_pallet,
    delete_pallet,
    duplicate_pallet,
    move_pallet,
    queue_pallet,
    refresh_programs,
    reorder_queue,
    simulate_signal,
    update_pallet,
    update_settings,
)
from app.settings import settings


STATIC_DIR = Path(__file__).parent / "static"


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or settings.database_url

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        run_migrations(url)
        engine = create_database_engine(url)
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
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

    def get_session(request: Request) -> Generator[Session, None, None]:
        with request.app.state.session_factory() as session:
            yield session

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/settings", include_in_schema=False)
    def settings_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "settings.html")

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/api/board")
    def get_board(session: Session = Depends(get_session)) -> dict:
        return board_snapshot(session)

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

    return application


app = create_app()
