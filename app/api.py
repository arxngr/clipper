from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
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
    owners: dict[str, str] = {}
    owners_lock = RLock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = service or build_service(settings)
        try:
            yield
        finally:
            app.state.jobs.close()

    app = FastAPI(title="Clipper", version="0.1.0", lifespan=lifespan)
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
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
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

    def find_job(job_id: str, request: Request):
        owner = request.cookies.get("clipper_session")
        with owners_lock:
            if not owner or owners.get(job_id) != owner:
                raise HTTPException(404, "Download not found.")
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
    def create_job(body: DownloadBody, request: Request, response: Response):
        ready()
        owner = request.cookies.get("clipper_session") or token_urlsafe(32)
        job = app.state.jobs.create(
            DownloadRequest(
                url=body.url,
                format=body.format,
                quality=body.quality,
                bitrate=body.bitrate,
                ranges=tuple(TimeRange(**clip.model_dump()) for clip in body.ranges),
            )
        )
        with owners_lock:
            owners[job["id"]] = owner
        response.set_cookie(
            "clipper_session", owner, httponly=True, samesite="strict",
            secure=request.url.scheme == "https", path="/api",
        )
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        return find_job(job_id, request)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        find_job(job_id, request)
        return app.state.jobs.cancel(job_id)

    @app.get("/api/jobs/{job_id}/file")
    def get_file(job_id: str, request: Request):
        job = find_job(job_id, request)
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
