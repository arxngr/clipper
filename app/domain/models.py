from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Callable


class DownloadError(Exception):
    """An error that can be displayed to the user."""


class Cancelled(DownloadError):
    pass


class OutputFormat(StrEnum):
    MP4 = "mp4"
    MP3 = "mp3"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass(frozen=True)
class TimeRange:
    start: float = 0
    end: float | None = None

    def __post_init__(self):
        if not isfinite(self.start) or self.start < 0:
            raise DownloadError("Start time must be zero or greater.")
        if self.end is not None and (not isfinite(self.end) or self.end <= self.start):
            raise DownloadError("End time must be greater than start time.")

    def duration(self, source_duration: float | None) -> float | None:
        if source_duration is not None:
            if self.start >= source_duration:
                raise DownloadError("Start time is beyond the end of the video.")
            if self.end is not None and self.end > source_duration + 0.05:
                raise DownloadError("End time is beyond the end of the video.")
        end = self.end if self.end is not None else source_duration
        return None if end is None else end - self.start


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    format: OutputFormat = OutputFormat.MP4
    ranges: tuple[TimeRange, ...] = ()
    quality: int = 1080
    bitrate: int = 192

    def __post_init__(self):
        if len(self.ranges) > 20:
            raise DownloadError("Choose at most 20 time ranges per download.")
        if self.quality not in {360, 480, 720, 1080, 2160}:
            raise DownloadError("Choose a supported video resolution.")
        if self.bitrate not in {128, 192, 320}:
            raise DownloadError("Choose 128, 192, or 320 kbps.")

    @property
    def clips(self) -> tuple[TimeRange, ...]:
        return self.ranges or (TimeRange(),)

    def duration(self, source_duration: float | None) -> float | None:
        durations = [clip.duration(source_duration) for clip in self.clips]
        return None if any(value is None for value in durations) else sum(durations)


@dataclass(frozen=True)
class SourceInfo:
    url: str
    title: str
    platform: str
    duration: float | None = None
    thumbnail: str | None = None
    media_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    extractor: str = "yt-dlp"
    alternatives: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            key: getattr(self, key)
            for key in (
                "url",
                "title",
                "platform",
                "duration",
                "thumbnail",
                "extractor",
            )
        }


@dataclass(frozen=True)
class MediaArtifact:
    path: Path
    duration: float
    has_video: bool
    has_audio: bool


Progress = Callable[[str, float | None], None]
