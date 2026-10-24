from threading import Event

from app.domain.models import DownloadError, DownloadRequest, SourceInfo
from app.infrastructure.ytdlp import SourceDownloader


def test_native_downloader_tries_alternate_media_endpoints(tmp_path):
    class Http:
        calls = []

        def download(self, url, destination, cancelled, headers, progress):
            self.calls.append(url)
            if "blocked" in url:
                destination.write_bytes(b"partial")
                raise DownloadError("Refused")
            destination.write_bytes(b"media")
            return destination

    class NoFallback:
        def inspect(self, *args):
            raise AssertionError(
                "A working native alternate should not use the fallback"
            )

    http = Http()
    source = SourceInfo(
        "https://www.tiktok.com/@test/video/1",
        "Test",
        "TikTok",
        media_url="https://blocked.example.com/video.mp4",
        alternatives=("https://working.example.com/video.mp4",),
    )
    result = SourceDownloader(http, NoFallback()).download(
        source,
        DownloadRequest(source.url),
        tmp_path,
        Event(),
        lambda *_: None,
    )
    assert len(http.calls) == 2
    assert result.read_bytes() == b"media"
