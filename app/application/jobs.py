import logging
import re
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app.application.ports import Downloader, Extractor, JobRepository, MediaProcessor
from app.domain.models import (
    Cancelled,
    DownloadError,
    DownloadRequest,
    TERMINAL_STATUSES,
)
from app.domain.urls import validate_url

logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        extractor: Extractor,
        downloader: Downloader,
        processor: MediaProcessor,
        data_dir: Path,
        max_artifact_bytes: int = 2 * 1024**3,
    ):
        self.repository = repository
        self.extractor = extractor
        self.downloader = downloader
        self.processor = processor
        self.max_artifact_bytes = max_artifact_bytes
        self.temp_dir = (data_dir / "tmp").resolve()
        self.output_dir = (data_dir / "downloads").resolve()
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="download"
        )
        self._lock = threading.RLock()
        self._cancelled: dict[str, threading.Event] = {}
        # Only remove directories with application-generated IDs under our temp root.
        for path in self.temp_dir.iterdir():
            if path.is_dir() and re.fullmatch(r"[a-f0-9]{32}", path.name):
                self._cleanup(path)

    def inspect(self, url: str):
        return self.extractor.inspect(validate_url(url), threading.Event())

    def create(self, request: DownloadRequest) -> dict:
        validate_url(request.url)
        with self._lock:
            if len(self._cancelled) >= 20:
                raise DownloadError("The queue is full. Wait for a download to finish.")
            job_id = uuid4().hex
            event = threading.Event()
            self._cancelled[job_id] = event
            job = self.repository.create(job_id, request)
            self._executor.submit(self._run, job_id, request, event)
            return job

    def cancel(self, job_id: str) -> dict | None:
        with self._lock:
            job = self.repository.get(job_id)
            if job and job["status"] not in TERMINAL_STATUSES:
                event = self._cancelled.get(job_id)
                if event:
                    event.set()
                self.repository.update(
                    job_id, status="cancelled", stage="Cancelled", progress=None
                )
            return self.repository.get(job_id)

    def artifact_path(self, job: dict) -> Path:
        if job["status"] != "completed" or not job["filename"]:
            raise DownloadError("This download is not ready yet.")
        path = self.output_dir / job["id"] / job["filename"]
        if not path.is_file():
            raise DownloadError(
                "This file is no longer on disk. Please download it again."
            )
        return path

    def close(self):
        with self._lock:
            for event in self._cancelled.values():
                event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _cleanup(self, directory: Path):
        if directory.resolve().parent == self.temp_dir:
            shutil.rmtree(directory, ignore_errors=True)

    def _run(self, job_id: str, request: DownloadRequest, cancelled: threading.Event):
        directory = self.temp_dir / job_id
        last_update = 0.0

        def update(**changes):
            with self._lock:
                if cancelled.is_set():
                    raise Cancelled("Cancelled.")
                self.repository.update(job_id, **changes)

        def progress(stage: str, percent: float | None):
            nonlocal last_update
            now = time.monotonic()
            if cancelled.is_set():
                raise Cancelled("Cancelled.")
            if now - last_update >= 0.35:
                update(
                    stage=stage,
                    progress=round(max(0, min(percent, 100)), 1)
                    if percent is not None
                    else None,
                )
                last_update = now

        try:
            update(status="resolving", stage="Reading video details", progress=None)
            source = self.extractor.inspect(request.url, cancelled)
            request.duration(source.duration)
            update(
                title=source.title,
                platform=source.platform,
                duration=request.duration(source.duration),
            )
            directory.mkdir()
            update(status="downloading", stage="Downloading source", progress=None)
            downloaded = self.downloader.download(
                source, request, directory, cancelled, progress
            )
            update(status="processing", stage="Preparing your file", progress=0)
            results = []
            count = len(request.clips)
            for index, clip in enumerate(request.clips):
                single = replace(request, ranges=(clip,))
                filename = self._filename(
                    source.title, single, index + 1 if count > 1 else None
                )

                def clip_progress(stage, percent, index=index):
                    progress(
                        f"Clip {index + 1} of {count} · {stage}",
                        (index * 100 + percent) / count
                        if percent is not None
                        else None,
                    )

                update(
                    stage=f"Creating clip {index + 1} of {count}",
                    progress=index / count * 100,
                )
                results.append(
                    self.processor.process(
                        downloaded,
                        single,
                        directory / filename,
                        cancelled,
                        clip_progress,
                    )
                )
                if (
                    sum(result.path.stat().st_size for result in results)
                    > self.max_artifact_bytes
                ):
                    raise DownloadError(
                        "The combined clips exceed the configured size limit."
                    )
            if count > 1:
                update(stage="Packaging clips", progress=99)
                filename = f"clips_{self._safe_title(source.title)}.zip"
                artifact = directory / filename
                with zipfile.ZipFile(
                    artifact, "w", compression=zipfile.ZIP_STORED
                ) as archive:
                    for result in results:
                        if cancelled.is_set():
                            raise Cancelled("Cancelled.")
                        archive.write(result.path, arcname=result.path.name)
            else:
                artifact = results[0].path
                filename = artifact.name
            if artifact.stat().st_size > self.max_artifact_bytes:
                raise DownloadError("The output exceeds the configured size limit.")
            with self._lock:
                if cancelled.is_set():
                    raise Cancelled("Cancelled.")
                destination = self.output_dir / job_id
                destination.mkdir()
                final = destination / filename
                artifact.replace(final)
                self.repository.update(
                    job_id,
                    status="completed",
                    stage="Ready to download",
                    progress=100,
                    filename=filename,
                    size=final.stat().st_size,
                    duration=sum(result.duration for result in results),
                    clip_count=count,
                )
        except Cancelled:
            self.repository.update(
                job_id, status="cancelled", stage="Cancelled", progress=None
            )
        except DownloadError as error:
            with self._lock:
                if not cancelled.is_set():
                    self.repository.update(
                        job_id,
                        status="failed",
                        stage="Download failed",
                        progress=None,
                        error=str(error),
                    )
        except Exception:
            logger.error("Unexpected failure in job %s", job_id)
            with self._lock:
                if not cancelled.is_set():
                    self.repository.update(
                        job_id,
                        status="failed",
                        stage="Download failed",
                        progress=None,
                        error="Something went wrong while preparing this file. Please try again.",
                    )
        finally:
            self._cleanup(directory)
            with self._lock:
                self._cancelled.pop(job_id, None)

    @staticmethod
    def _safe_title(title: str):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip(" .")[:90] or "video"

    @classmethod
    def _filename(cls, title: str, request: DownloadRequest, index: int | None = None):
        title = cls._safe_title(title)
        clip = request.clips[0]
        suffix = (
            f"_{clip.start:g}-{clip.end:g}"
            if clip.end is not None
            else f"_{clip.start:g}-end"
            if clip.start
            else ""
        )
        number = f"{index:02d}_" if index else ""
        return f"clip_{number}{title}{suffix}.{request.format.value}"
