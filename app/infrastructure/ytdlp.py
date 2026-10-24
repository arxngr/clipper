import json
import sys
from pathlib import Path
from threading import Event

from app.config import Settings
from app.domain.models import (
    Cancelled,
    DownloadError,
    DownloadRequest,
    OutputFormat,
    Progress,
    SourceInfo,
)
from app.infrastructure.network import PublicHttpClient, validate_public_host
from app.infrastructure.process import ProcessRunner


class YtDlpAdapter:
    def __init__(self, runner: ProcessRunner, settings: Settings):
        self.runner = runner
        self.settings = settings

    def _base(self):
        arguments = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-playlist",
            "--socket-timeout",
            "20",
            "--retries",
            "2",
            "--extractor-retries",
            "1",
        ]
        if self.settings.ffmpeg:
            arguments.extend(
                ["--ffmpeg-location", str(Path(self.settings.ffmpeg).parent)]
            )
        if self.settings.deno:
            arguments.extend(["--js-runtimes", f"deno:{self.settings.deno}"])
        return arguments

    def inspect(self, url: str, cancelled: Event) -> SourceInfo:
        validate_public_host(url)
        try:
            raw = self.runner.run(
                self._base() + ["--dump-single-json", "--skip-download", "--", url],
                cancelled,
                timeout=90,
            )
            metadata = json.loads(
                next(line for line in raw.splitlines() if line.startswith("{"))
            )
            if metadata.get("_type") in {"playlist", "multi_video"}:
                raise DownloadError(
                    "Paste a single video link; playlists and multi-video posts are not supported yet."
                )
            if metadata.get("is_live"):
                raise DownloadError(
                    "Live streams are not supported. Choose a recorded video."
                )
            return SourceInfo(
                url,
                str(metadata.get("title") or "Untitled video")[:300],
                str(metadata.get("extractor_key") or "Video"),
                metadata.get("duration"),
                metadata.get("thumbnail"),
            )
        except Cancelled:
            raise
        except (ValueError, StopIteration) as error:
            raise DownloadError("Could not read this video's details.") from error
        except DownloadError as error:
            if "single video" in str(error) or "Live streams" in str(error):
                raise
            raise DownloadError(
                "Could not access this video. It may require login, be unavailable, or need a site-support update."
            ) from error

    def download(
        self,
        source: SourceInfo,
        request: DownloadRequest,
        directory: Path,
        cancelled: Event,
        progress: Progress,
    ) -> Path:
        selection = (
            "bestaudio/best"
            if request.format == OutputFormat.MP3
            else f"bestvideo[height<={request.quality}]+bestaudio/best[height<={request.quality}]/worst"
        )
        arguments = self._base() + [
            "--format",
            selection,
            "--merge-output-format",
            "mkv",
            "--newline",
            "--progress",
            "--progress-template",
            "download:bytes=%(progress.downloaded_bytes)s;total=%(progress.total_bytes)s;estimate=%(progress.total_bytes_estimate)s",
            "--max-filesize",
            str(self.settings.max_download_bytes),
            "--output",
            str(directory / "source.%(ext)s"),
            "--print",
            "after_move:filepath",
            "--",
            source.url,
        ]

        def on_line(line: str):
            if line.startswith("bytes="):
                fields = dict(
                    part.split("=", 1) for part in line.split(";") if "=" in part
                )
                downloaded = float(fields.get("bytes", "0"))
                total = fields.get("total")
                if total in {None, "NA", "None"}:
                    total = fields.get("estimate")
                percent = (
                    downloaded / float(total) * 100
                    if total not in {None, "NA", "None", "0"}
                    else None
                )
                progress("Downloading source", percent)
            size = sum(
                path.stat().st_size for path in directory.iterdir() if path.is_file()
            )
            if size > self.settings.max_download_bytes:
                raise DownloadError("This download exceeds the configured size limit.")

        self.runner.run(
            arguments, cancelled, timeout=self.settings.job_timeout, on_line=on_line
        )
        candidates = [
            path
            for path in directory.glob("source.*")
            if path.suffix not in {".part", ".ytdl"}
        ]
        if len(candidates) != 1 or candidates[0].stat().st_size == 0:
            raise DownloadError("The website did not return a complete media file.")
        return candidates[0]


class SourceDownloader:
    def __init__(self, http: PublicHttpClient, fallback: YtDlpAdapter):
        self.http = http
        self.fallback = fallback

    def download(
        self,
        source: SourceInfo,
        request: DownloadRequest,
        directory: Path,
        cancelled: Event,
        progress: Progress,
    ) -> Path:
        if not source.media_url:
            return self.fallback.download(
                source, request, directory, cancelled, progress
            )
        for address in dict.fromkeys((source.media_url, *source.alternatives)):
            try:
                return self.http.download(
                    address,
                    directory / "source.mp4",
                    cancelled,
                    source.headers,
                    progress,
                )
            except Cancelled:
                raise
            except DownloadError:
                (directory / "source.mp4").unlink(missing_ok=True)
        # Resolve again if every public media endpoint supplied by the page failed.
        resolved = self.fallback.inspect(source.url, cancelled)
        return self.fallback.download(resolved, request, directory, cancelled, progress)
