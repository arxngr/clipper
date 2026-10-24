import math

import pytest

from app.domain.models import DownloadError, DownloadRequest, TimeRange
from app.domain.urls import validate_url


@pytest.mark.parametrize(
    "start,end", [(-1, None), (5, 5), (8, 2), (math.nan, None), (0, math.inf)]
)
def test_invalid_ranges_are_rejected(start, end):
    with pytest.raises(DownloadError):
        TimeRange(start=start, end=end)


def test_range_duration_and_bounds():
    request = DownloadRequest(
        "https://example.com/video", ranges=(TimeRange(120, 300),)
    )
    assert request.duration(600) == 180
    with pytest.raises(DownloadError, match="beyond"):
        request.duration(200)
    assert TimeRange(30).duration(100) == 70


def test_multiple_ranges_keep_their_order_and_total_duration():
    request = DownloadRequest(
        "https://example.com/video", ranges=(TimeRange(60, 120), TimeRange(600, 900))
    )
    assert request.duration(1000) == 360
    assert request.clips[1].start == 600
    with pytest.raises(DownloadError, match="beyond"):
        request.duration(800)
    with pytest.raises(DownloadError, match="20"):
        DownloadRequest("https://example.com/video", ranges=(TimeRange(0, 1),) * 21)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/video",
        "http://127.0.0.1",
        "http://[::1]",
        "http://169.254.169.254",
        "https://user:pass@example.com",
        "https://example.com:8080/video",
    ],
)
def test_non_public_or_credentialed_urls_are_rejected(url):
    with pytest.raises(DownloadError):
        validate_url(url)


def test_public_url_is_accepted():
    assert validate_url("https://www.youtube.com/watch?v=example").startswith(
        "https://"
    )
