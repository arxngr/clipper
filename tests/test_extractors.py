import json
from threading import Event

import pytest

from app.domain.models import Cancelled, DownloadError, SourceInfo
from app.infrastructure.extractors import FallbackExtractor, NativeSocialExtractor


class PageClient:
    def __init__(self, page):
        self.page = page

    def text(self, url, cancelled):
        return self.page


def test_tiktok_extracts_requested_video_from_embedded_player_data():
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "id": "1234",
                        "desc": "A test clip",
                        "video": {
                            "duration": 45,
                            "playAddr": "https://cdn.example.com/clip.mp4",
                            "cover": "https://cdn.example.com/cover.jpg",
                            "PlayAddrStruct": {
                                "UrlList": ["https://www.tiktok.com/media/sample.mp4"]
                            },
                        },
                    }
                }
            }
        }
    }
    page = f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script>'
    source = NativeSocialExtractor(PageClient(page)).inspect(
        "https://www.tiktok.com/@test/video/1234", Event()
    )
    assert source.media_url == "https://cdn.example.com/clip.mp4"
    assert source.duration == 45
    assert source.extractor == "native"
    assert source.alternatives == ("https://www.tiktok.com/media/sample.mp4",)


def test_tiktok_does_not_choose_a_different_video():
    page = '<script>{"item":{"id":"9999","video":{"playAddr":"https://cdn.example.com/wrong.mp4"}}}</script>'
    with pytest.raises(DownloadError):
        NativeSocialExtractor(PageClient(page)).inspect(
            "https://www.tiktok.com/@test/video/1234", Event()
        )


def test_instagram_decodes_escaped_media_url():
    page = '<meta property="og:title" content="A reel"><meta property="og:video" content="https://cdn.example.com/reel.mp4?a=1&amp;b=2">'
    source = NativeSocialExtractor(PageClient(page)).inspect(
        "https://www.instagram.com/reel/ABC/", Event()
    )
    assert source.media_url == "https://cdn.example.com/reel.mp4?a=1&b=2"
    assert source.title == "A reel"


def test_instagram_json_video():
    page = '<script type="application/ld+json">{"@type":"VideoObject","name":"My reel","contentUrl":"https://cdn.example.com/video.mp4"}</script>'
    source = NativeSocialExtractor(PageClient(page)).inspect(
        "https://www.instagram.com/reel/ABC/", Event()
    )
    assert source.title == "My reel"


def test_instagram_selects_versions_only_from_the_requested_post():
    payload = {
        "posts": [
            {
                "code": "OTHER",
                "video_versions": [{"url": "https://cdn.example.com/wrong.mp4"}],
            },
            {
                "code": "ABC",
                "video_duration": 15,
                "video_versions": [
                    {
                        "width": 320,
                        "height": 180,
                        "url": "https://cdn.example.com/small.mp4",
                    },
                    {
                        "width": 1280,
                        "height": 720,
                        "url": "https://cdn.example.com/right.mp4",
                    },
                ],
            },
        ]
    }
    page = f'<script type="application/json">{json.dumps(payload)}</script>'
    source = NativeSocialExtractor(PageClient(page)).inspect(
        "https://www.instagram.com/reel/ABC/", Event()
    )
    assert source.media_url == "https://cdn.example.com/right.mp4"
    assert source.duration == 15


def test_instagram_does_not_download_a_recommended_post():
    page = '<script>{"post":{"shortcode":"OTHER","nested":{"video_url":"https://cdn.example.com/wrong.mp4"}}}</script>'
    with pytest.raises(DownloadError):
        NativeSocialExtractor(PageClient(page)).inspect(
            "https://www.instagram.com/reel/ABC/", Event()
        )


def test_extractor_falls_back_but_never_swallows_cancellation():
    class Native:
        def inspect(self, url, cancelled):
            raise DownloadError("Not exposed")

    class Fallback:
        calls = 0

        def inspect(self, url, cancelled):
            self.calls += 1
            return SourceInfo(url, "Fallback", "Example")

    fallback = Fallback()
    extractor = FallbackExtractor(Native(), fallback)
    assert extractor.inspect("https://example.com/video", Event()).title == "Fallback"
    assert fallback.calls == 1

    class Stopped:
        def inspect(self, url, cancelled):
            raise Cancelled()

    with pytest.raises(Cancelled):
        FallbackExtractor(Stopped(), fallback).inspect(
            "https://example.com/video", Event()
        )
    assert fallback.calls == 1
