from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TIMEZONE = "Asia/Karachi"
SCHEMA_VERSION = "1.0"


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    artifact_dir: Path = PROJECT_ROOT / "artifacts"
    max_upload_bytes: int = 12 * 1024 * 1024
    fx_stale_days: int = 3
    quote_stale_days: int = 30

    def __post_init__(self):
        object.__setattr__(self, "data_dir", resolve_path(self.data_dir))
        object.__setattr__(self, "runtime_dir", resolve_path(self.runtime_dir))
        object.__setattr__(self, "artifact_dir", resolve_path(self.artifact_dir))
        if self.max_upload_bytes <= 0 or min(self.fx_stale_days, self.quote_stale_days) < 0:
            raise ValueError("Upload limit must be positive and freshness limits nonnegative")

    @classmethod
    def from_env(cls):
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            data_dir=resolve_path(os.getenv("RESTOCKIQ_DATA_DIR", "data")),
            runtime_dir=resolve_path(os.getenv("RESTOCKIQ_RUNTIME_DIR", "runtime")),
            artifact_dir=resolve_path(os.getenv("RESTOCKIQ_ARTIFACT_DIR", "artifacts")),
            max_upload_bytes=int(os.getenv("RESTOCKIQ_MAX_UPLOAD_BYTES", 12 * 1024 * 1024)),
            fx_stale_days=int(os.getenv("RESTOCKIQ_FX_STALE_DAYS", 3)),
            quote_stale_days=int(os.getenv("RESTOCKIQ_QUOTE_STALE_DAYS", 30)),
        )
