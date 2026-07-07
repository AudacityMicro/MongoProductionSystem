from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "mongo-production.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("MPS_HOST", "0.0.0.0")
    port: int = int(os.getenv("MPS_PORT", "8000"))
    log_level: str = os.getenv("MPS_LOG_LEVEL", "info")
    database_url: str = os.getenv(
        "MPS_DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )


settings = Settings()
