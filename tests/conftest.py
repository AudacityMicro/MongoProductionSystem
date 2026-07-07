from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    with TestClient(create_app(database_url)) as test_client:
        yield test_client


@pytest.fixture
def board(client: TestClient) -> dict:
    return client.get("/api/board").json()

