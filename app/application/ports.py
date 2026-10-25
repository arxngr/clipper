from pathlib import Path
from threading import Event
from typing import Protocol

from app.domain.models import DownloadRequest, MediaArtifact, Progress, SourceInfo


class Extractor(Protocol):
    def inspect(self, url: str, cancelled: Event) -> SourceInfo: ...


class Downloader(Protocol):
    def download(
        self,
        source: SourceInfo,
        request: DownloadRequest,
        directory: Path,
        cancelled: Event,
        progress: Progress,
    ) -> Path: ...


class MediaProcessor(Protocol):
    def process(
        self,
        source: Path,
        request: DownloadRequest,
        output: Path,
        cancelled: Event,
        progress: Progress,
    ) -> MediaArtifact: ...


class JobRepository(Protocol):
    def create(self, job_id: str, request: DownloadRequest) -> dict: ...
    def get(self, job_id: str) -> dict | None: ...
    def list(self) -> list[dict]: ...
    def update(self, job_id: str, **changes) -> None: ...
