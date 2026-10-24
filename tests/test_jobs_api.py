import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.application.jobs import JobService
from app.config import Settings
from app.domain.models import (
    Cancelled,
    DownloadError,
    DownloadRequest,
    MediaArtifact,
    SourceInfo,
    TimeRange,
)
from app.infrastructure.repository import SQLiteJobRepository


class FixtureExtractor:
    def inspect(self, url, cancelled):
        return SourceInfo(url, "Test <video>", "Fixture", 60)


class FixtureDownloader:
    def download(self, source, request, directory, cancelled, progress):
        path = directory / "source.mp4"
        path.write_bytes(b"source")
        return path


class FixtureProcessor:
    def process(self, source, request, output, cancelled, progress):
        output.write_bytes(b"verified artifact")
        return MediaArtifact(output, request.duration(60), True, True)


def wait_for(repository, job_id, statuses):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = repository.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    pytest.fail("Job did not reach the expected state")


@pytest.fixture
def service(tmp_path):
    service = JobService(
        SQLiteJobRepository(tmp_path / "jobs.db"),
        FixtureExtractor(),
        FixtureDownloader(),
        FixtureProcessor(),
        tmp_path,
    )
    yield service
    service.close()


def test_completed_artifact_and_persisted_history(service, tmp_path):
    job = service.create(
        DownloadRequest("https://example.com/video", ranges=(TimeRange(2, 8),))
    )
    result = wait_for(service.repository, job["id"], {"completed", "failed"})
    assert result["status"] == "completed"
    assert result["duration"] == 6
    assert service.artifact_path(result).read_bytes() == b"verified artifact"
    assert "<" not in result["filename"]
    assert list((tmp_path / "tmp").iterdir()) == []
    assert (
        SQLiteJobRepository(tmp_path / "jobs.db").get(job["id"])["status"]
        == "completed"
    )


def test_failed_job_never_publishes_an_artifact(service, tmp_path):
    class BrokenProcessor:
        def process(self, source, request, output, cancelled, progress):
            output.write_bytes(b"incomplete")
            raise DownloadError("No audio track")

    service.processor = BrokenProcessor()
    job = service.create(DownloadRequest("https://example.com/video"))
    result = wait_for(service.repository, job["id"], {"failed"})
    assert result["error"] == "No audio track"
    assert result["filename"] is None
    with pytest.raises(DownloadError):
        service.artifact_path(result)
    service.close()
    assert list((tmp_path / "tmp").iterdir()) == []


def test_cancelled_job_cannot_become_completed(service):
    started = threading.Event()

    class WaitingDownloader:
        def download(self, source, request, directory, cancelled, progress):
            started.set()
            assert cancelled.wait(3)
            raise Cancelled()

    service.downloader = WaitingDownloader()
    job = service.create(DownloadRequest("https://example.com/video"))
    assert started.wait(3)
    assert service.cancel(job["id"])["status"] == "cancelled"
    service.close()
    assert service.repository.get(job["id"])["status"] == "cancelled"
    assert service.repository.get(job["id"])["filename"] is None


def test_restart_marks_unfinished_jobs_interrupted(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    repository.create("old", DownloadRequest("https://example.com/video"))
    repository.interrupt_unfinished()
    assert repository.get("old")["status"] == "failed"
    assert "stopped" in repository.get("old")["error"]


def test_api_inspect_create_download_and_validation(service, tmp_path):
    settings = Settings(tmp_path, "ffmpeg", "ffprobe", None)
    with TestClient(create_app(settings, service)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["ready"]
        info = client.post("/api/inspect", json={"url": "https://example.com/video"})
        assert info.json()["title"] == "Test <video>"
        assert "media_url" not in info.json()
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://example.com/video",
                "ranges": [{"start": 1, "end": 3}],
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        wait_for(service.repository, job_id, {"completed"})
        assert client.get(f"/api/jobs/{job_id}/file").content == b"verified artifact"
        assert client.get("/api/jobs/missing").status_code == 404
        assert (
            client.post("/api/jobs", json={"url": "http://localhost/video"}).status_code
            == 400
        )
        assert (
            client.post(
                "/api/jobs",
                json={
                    "url": "https://example.com/video",
                    "ranges": [{"start": 8, "end": 2}],
                },
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/jobs", json={"url": "https://example.com/video", "quality": 999}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/jobs",
                json={"url": "https://example.com/video"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_missing_media_tools_report_setup_needed(tmp_path):
    with TestClient(create_app(Settings(tmp_path, None, None, None))) as client:
        assert not client.get("/api/health").json()["ready"]
        assert (
            client.post(
                "/api/jobs", json={"url": "https://example.com/video"}
            ).status_code
            == 503
        )


def test_multiple_clips_are_downloaded_once_and_served_as_zip(service, tmp_path):
    class CountingDownloader(FixtureDownloader):
        calls = 0

        def download(self, *args):
            self.calls += 1
            return super().download(*args)

    service.downloader = CountingDownloader()
    with TestClient(
        create_app(Settings(tmp_path, "ffmpeg", "ffprobe", None), service)
    ) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://example.com/video",
                "ranges": [{"start": 1, "end": 3}, {"start": 10, "end": 15}],
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        job = wait_for(service.repository, job_id, {"completed", "failed"})
        assert job["status"] == "completed"
        assert job["duration"] == 7
        assert job["clip_count"] == 2
        assert service.downloader.calls == 1
        artifact = service.artifact_path(job)
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            assert len(names) == 2
            assert names[0].startswith("clip_01_") and names[1].startswith("clip_02_")
            assert archive.testzip() is None
        download = client.get(f"/api/jobs/{job_id}/file")
        assert download.headers["content-type"] == "application/zip"


def test_batch_failure_never_publishes_a_partial_zip(service):
    class SecondClipFails(FixtureProcessor):
        calls = 0

        def process(self, *args):
            self.calls += 1
            if self.calls == 2:
                raise DownloadError("The second clip failed")
            return super().process(*args)

    service.processor = SecondClipFails()
    job = service.create(
        DownloadRequest(
            "https://example.com/video", ranges=(TimeRange(1, 3), TimeRange(10, 15))
        )
    )
    result = wait_for(service.repository, job["id"], {"failed"})
    assert result["filename"] is None
    assert not (service.output_dir / job["id"]).exists()


def test_combined_output_limit_applies_to_all_clips(service):
    service.max_artifact_bytes = 20
    job = service.create(
        DownloadRequest(
            "https://example.com/video", ranges=(TimeRange(1, 3), TimeRange(10, 15))
        )
    )
    result = wait_for(service.repository, job["id"], {"failed"})
    assert "combined" in result["error"]
    assert result["filename"] is None
