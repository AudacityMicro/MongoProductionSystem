from dataclasses import dataclass
import os
from pathlib import Path


def _load_local_env() -> None:
    """Load the project-local .env without overwriting process variables."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key.startswith("MPS_"):
            os.environ.setdefault(key, value)


_load_local_env()


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
