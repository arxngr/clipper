import json
import subprocess
import shutil
import time
import zipfile
from pathlib import Path
from threading import Event

import pytest

from app.config import Settings
from app.domain.models import (
    DownloadError,
    DownloadRequest,
    OutputFormat,
    TimeRange,
    SourceInfo,
)
from app.application.jobs import JobService
from app.infrastructure.repository import SQLiteJobRepository
from app.infrastructure.media import FFmpegProcessor
from app.infrastructure.process import ProcessRunner


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    settings = Settings.load()
    if not settings.ffmpeg or not settings.ffprobe:
        pytest.skip("Install FFmpeg and ffprobe to run media integration tests")
    directory = tmp_path_factory.mktemp("media")
    source = directory / "colors.mp4"
    runner = ProcessRunner()
    runner.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=25:d=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x90:r=25:d=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=25:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=9",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        Event(),
        timeout=30,
    )
    return settings, directory, source, FFmpegProcessor(runner, settings)


def test_mp4_clip_contains_the_requested_source_interval(media):
    settings, directory, source, processor = media
    request = DownloadRequest("https://example.com/video", ranges=(TimeRange(3, 6),))
    output = directory / "green.mp4"
    result = processor.process(source, request, output, Event(), lambda *_: None)
    assert result.duration == pytest.approx(3, abs=0.05)
    assert result.has_audio and result.has_video
    for position in (0, 2.8):
        pixel = subprocess.check_output(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-ss",
                str(position),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "scale=1:1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )
        red, green, blue = pixel[:3]
        assert green > red + 60 and green > blue + 60
    probe = subprocess.check_output(
        [settings.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)]
    )
    video = next(
        item for item in json.loads(probe)["streams"] if item["codec_type"] == "video"
    )
    assert (video["width"], video["height"]) == (160, 90)


def test_mp3_clip_is_audio_only_with_expected_duration(media):
    _, directory, source, processor = media
    request = DownloadRequest(
        "https://example.com/video",
        format=OutputFormat.MP3,
        ranges=(TimeRange(1.5, 4.5),),
    )
    result = processor.process(
        source, request, directory / "clip.mp3", Event(), lambda *_: None
    )
    assert result.duration == pytest.approx(3, abs=0.1)
    assert result.has_audio and not result.has_video


def test_silent_video_reports_missing_audio(media):
    settings, directory, source, processor = media
    silent = directory / "silent.mp4"
    ProcessRunner().run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "copy",
            str(silent),
        ],
        Event(),
    )
    with pytest.raises(DownloadError, match="no audio"):
        processor.process(
            silent,
            DownloadRequest("https://example.com/video", format=OutputFormat.MP3),
            directory / "silent.mp3",
            Event(),
            lambda *_: None,
        )


def test_out_of_bounds_range_is_rejected_before_encoding(media):
    _, directory, source, processor = media
    with pytest.raises(DownloadError, match="beyond"):
        processor.process(
            source,
            DownloadRequest("https://example.com/video", ranges=(TimeRange(8, 12),)),
            directory / "invalid.mp4",
            Event(),
            lambda *_: None,
        )


@pytest.mark.parametrize("output_format", [OutputFormat.MP4, OutputFormat.MP3])
def test_batch_zip_contains_separate_verified_media_files(
    media, tmp_path, output_format
):
    _, _, source, processor = media

    class LocalExtractor:
        def inspect(self, url, cancelled):
            return SourceInfo(url, "Color fixture", "Fixture", 9)

    class LocalDownloader:
        calls = 0

        def download(self, info, request, directory, cancelled, progress):
            self.calls += 1
            return Path(shutil.copyfile(source, directory / "source.mp4"))

    downloader = LocalDownloader()
    service = JobService(
        SQLiteJobRepository(tmp_path / "jobs.db"),
        LocalExtractor(),
        downloader,
        processor,
        tmp_path,
    )
    try:
        job = service.create(
            DownloadRequest(
                "https://example.com/video",
                format=output_format,
                ranges=(TimeRange(0, 1), TimeRange(6, 8)),
            )
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = service.repository.get(job["id"])
            if result["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert result["status"] == "completed", result["error"]
        assert downloader.calls == 1
        assert result["duration"] == pytest.approx(3, abs=0.15)
        with zipfile.ZipFile(service.artifact_path(result)) as archive:
            assert len(archive.namelist()) == 2
            for index, name in enumerate(archive.namelist()):
                assert name.endswith(f".{output_format.value}")
                path = tmp_path / name
                path.write_bytes(archive.read(name))
                artifact = processor.probe(path, Event())
                assert artifact.duration == pytest.approx(index + 1, abs=0.1)
                assert artifact.has_audio
                assert artifact.has_video == (output_format == OutputFormat.MP4)
    finally:
        service.close()
