import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable(name: str) -> str | None:
    configured = os.getenv(f"{name.upper()}_PATH")
    if configured:
        path = Path(configured).expanduser().resolve()
        return str(path) if path.is_file() else None
    suffix = ".exe" if os.name == "nt" else ""
    local = ROOT / ".tools" / f"{name}{suffix}"
    return str(local) if local.is_file() else shutil.which(name)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ffmpeg: str | None
    ffprobe: str | None
    deno: str | None
    max_download_bytes: int = 2 * 1024**3
    job_timeout: int = 3600

    @classmethod
    def load(cls):
        return cls(
            Path(os.getenv("DOWNLOADER_DATA_DIR", str(ROOT / "data"))).resolve(),
            executable("ffmpeg"),
            executable("ffprobe"),
            executable("deno"),
            int(os.getenv("MAX_DOWNLOAD_BYTES", str(2 * 1024**3))),
            int(os.getenv("JOB_TIMEOUT_SECONDS", "3600")),
        )
