import json
from pathlib import Path
from threading import Event

from app.config import Settings
from app.domain.models import (
    DownloadError,
    DownloadRequest,
    MediaArtifact,
    OutputFormat,
    Progress,
)
from app.infrastructure.process import ProcessRunner


class FFmpegProcessor:
    def __init__(self, runner: ProcessRunner, settings: Settings):
        self.runner = runner
        self.settings = settings

    def probe(self, path: Path, cancelled: Event) -> MediaArtifact:
        raw = self.runner.run(
            [
                self.settings.ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            cancelled,
            timeout=30,
        )
        metadata = json.loads(raw)
        streams = {stream["codec_type"] for stream in metadata.get("streams", [])}
        try:
            duration = float(metadata["format"]["duration"])
        except (ValueError, KeyError) as error:
            raise DownloadError("The source has no readable duration.") from error
        if duration <= 0:
            raise DownloadError("The source contains no playable media.")
        return MediaArtifact(path, duration, "video" in streams, "audio" in streams)

    def process(
        self,
        source: Path,
        request: DownloadRequest,
        output: Path,
        cancelled: Event,
        progress: Progress,
    ) -> MediaArtifact:
        if len(request.clips) != 1:
            raise DownloadError("Process one clip at a time.")
        clip = request.clips[0]
        metadata = self.probe(source, cancelled)
        duration = request.duration(metadata.duration)
        if request.format == OutputFormat.MP3 and not metadata.has_audio:
            raise DownloadError("This video has no audio track to convert to MP3.")
        if request.format == OutputFormat.MP4 and not metadata.has_video:
            raise DownloadError("This link contains audio only. Choose MP3 instead.")
        arguments = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            str(clip.start),
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-t",
            str(duration),
        ]
        if request.format == OutputFormat.MP3:
            arguments += [
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{request.bitrate}k",
            ]
        else:
            # A 1080p cap limits the short edge, including for portrait video.
            scale = (
                f"scale=w='if(gte(iw,ih),-2,min(iw,{request.quality}))':"
                f"h='if(gte(iw,ih),min(ih,{request.quality}),-2)'"
            )
            arguments += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        arguments += ["-threads", "2", "-progress", "pipe:1", "-nostats", str(output)]

        def on_line(line: str):
            if line.startswith("out_time_us="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    progress(
                        "Creating MP3"
                        if request.format == OutputFormat.MP3
                        else "Trimming & encoding",
                        min(seconds / duration * 100, 99),
                    )
                except ValueError:
                    pass
            if (
                output.exists()
                and output.stat().st_size > self.settings.max_download_bytes
            ):
                raise DownloadError("The output exceeds the configured size limit.")

        self.runner.run(
            arguments, cancelled, timeout=self.settings.job_timeout, on_line=on_line
        )
        result = self.probe(output, cancelled)
        if abs(result.duration - duration) > 0.25:
            raise DownloadError(
                "The output duration did not match the requested clip. Try another source."
            )
        if request.format == OutputFormat.MP3 and not result.has_audio:
            raise DownloadError("The generated MP3 contains no audio.")
        if request.format == OutputFormat.MP4 and (
            not result.has_video or (metadata.has_audio and not result.has_audio)
        ):
            raise DownloadError("The generated video is missing a media track.")
        return result
