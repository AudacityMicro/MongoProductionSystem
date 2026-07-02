from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("MPS_HOST", "0.0.0.0")
    port: int = int(os.getenv("MPS_PORT", "8000"))
    log_level: str = os.getenv("MPS_LOG_LEVEL", "info")


settings = Settings()

