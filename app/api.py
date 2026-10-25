from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.application.jobs import JobService
from app.config import Settings
from app.domain.models import DownloadError, DownloadRequest, OutputFormat, TimeRange
from app.infrastructure.extractors import FallbackExtractor, NativeSocialExtractor
from app.infrastructure.media import FFmpegProcessor
from app.infrastructure.network import PublicHttpClient
from app.infrastructure.process import ProcessRunner
from app.infrastructure.repository import InMemoryJobRepository
from app.infrastructure.ytdlp import SourceDownloader, YtDlpAdapter

WEB = Path(__file__).parent / "web"


class InspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=8, max_length=4096)


class RangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class DownloadBody(InspectBody):
    format: OutputFormat = OutputFormat.MP4
    ranges: list[RangeBody] = Field(default_factory=list, max_length=20)
    quality: int = 1080
    bitrate: int = 192


def build_service(settings: Settings) -> JobService:
    runner = ProcessRunner()
    http = PublicHttpClient(settings.max_download_bytes, settings.job_timeout)
    fallback = YtDlpAdapter(runner, settings)
    return JobService(
        InMemoryJobRepository(),
        FallbackExtractor(NativeSocialExtractor(http), fallback),
        SourceDownloader(http, fallback),
        FFmpegProcessor(runner, settings),
        settings.data_dir,
        max_artifact_bytes=settings.max_download_bytes,
    )


def create_app(
    settings: Settings | None = None, service: JobService | None = None
) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = service or build_service(settings)
        try:
            yield
        finally:
            app.state.jobs.close()

    app = FastAPI(title="Video Downloader", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and urlsplit(origin).netloc != request.headers.get("host")
            ):
                return JSONResponse(
                    {"detail": "Open the app directly to use this action."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(DownloadError)
    async def download_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    def ready():
        if not settings.ffmpeg or not settings.ffprobe:
            raise HTTPException(
                503, "FFmpeg is not set up. Run: python scripts/setup_tools.py"
            )

    def find_job(job_id: str):
        job = app.state.jobs.repository.get(job_id)
        if not job:
            raise HTTPException(404, "Download not found.")
        return job

    @app.get("/api/health")
    def health():
        return {
            "ready": bool(settings.ffmpeg and settings.ffprobe),
            "tools": {
                "ffmpeg": bool(settings.ffmpeg),
                "ffprobe": bool(settings.ffprobe),
                "javascript_runtime": bool(settings.deno),
            },
            "max_download_bytes": settings.max_download_bytes,
        }

    @app.post("/api/inspect")
    def inspect(body: InspectBody):
        ready()
        return app.state.jobs.inspect(body.url).public()

    @app.post("/api/jobs", status_code=202)
    def create_job(body: DownloadBody):
        ready()
        return app.state.jobs.create(
            DownloadRequest(
                url=body.url,
                format=body.format,
                quality=body.quality,
                bitrate=body.bitrate,
                ranges=tuple(TimeRange(**clip.model_dump()) for clip in body.ranges),
            )
        )

    @app.get("/api/jobs")
    def list_jobs():
        return app.state.jobs.repository.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return find_job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        find_job(job_id)
        return app.state.jobs.cancel(job_id)

    @app.get("/api/jobs/{job_id}/file")
    def get_file(job_id: str):
        job = find_job(job_id)
        path = app.state.jobs.artifact_path(job)
        media_type = {
            ".mp3": "audio/mpeg",
            ".mp4": "video/mp4",
            ".zip": "application/zip",
        }[path.suffix]
        return FileResponse(path, filename=job["filename"], media_type=media_type)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
