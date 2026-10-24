import json
import re
from html.parser import HTMLParser
from threading import Event
from urllib.parse import urlsplit

from app.application.ports import Extractor
from app.domain.models import Cancelled, DownloadError, SourceInfo
from app.domain.urls import validate_url
from app.infrastructure.network import PublicHttpClient, USER_AGENT


class PageData(HTMLParser):
    def __init__(self, document: str):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.documents: list[dict | list] = []
        self._script: list[str] | None = None
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("property") or attributes.get("name")
            if name and attributes.get("content"):
                self.meta[name] = attributes["content"]
        if tag == "script":
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try:
                parsed = json.loads("".join(self._script))
                if isinstance(parsed, (dict, list)):
                    self.documents.append(parsed)
            except (ValueError, RecursionError):
                pass
            self._script = None


def dictionaries(value):
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            yield node
            pending.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            pending.extend(reversed(node))


def media_address(value) -> str | None:
    if isinstance(value, str) and value.startswith("https://"):
        return value
    if isinstance(value, dict):
        for key in ("UrlList", "urlList", "url_list"):
            for address in value.get(key, []):
                if isinstance(address, str) and address.startswith("https://"):
                    return address
    return None


class NativeSocialExtractor:
    def __init__(self, http: PublicHttpClient):
        self.http = http

    def inspect(self, url: str, cancelled: Event) -> SourceInfo:
        host = urlsplit(url).hostname or ""
        if host == "tiktok.com" or host.endswith(".tiktok.com"):
            return self._tiktok(url, PageData(self.http.text(url, cancelled)))
        if host == "instagram.com" or host.endswith(".instagram.com"):
            page = PageData(self.http.text(url, cancelled))
            try:
                return self._instagram(url, page)
            except DownloadError:
                match = re.search(r"/(?:p|reel|reels|tv)/([\w-]+)", urlsplit(url).path)
                if not match:
                    raise
                embed = f"https://www.instagram.com/p/{match[1]}/embed/"
                return self._instagram(url, PageData(self.http.text(embed, cancelled)))
        raise DownloadError("No native extractor for this website.")

    def _tiktok(self, url: str, page: PageData) -> SourceInfo:
        match = re.search(r"/video/(\d+)", urlsplit(url).path)
        expected_id = match[1] if match else None
        for document in page.documents:
            for item in dictionaries(document):
                video = item.get("video")
                if not isinstance(video, dict):
                    continue
                if expected_id and str(item.get("id")) != expected_id:
                    continue
                address = media_address(video.get("playAddr")) or media_address(
                    video.get("downloadAddr")
                )
                if not address:
                    continue
                alternatives = []
                for data in (
                    video.get("PlayAddrStruct", {}),
                    video.get("bitrateInfo", []),
                ):
                    for node in dictionaries(data):
                        for key in ("UrlList", "urlList", "url_list"):
                            values = node.get(key, [])
                            if isinstance(values, list):
                                alternatives.extend(
                                    value for value in values if media_address(value)
                                )
                duration = video.get("duration")
                return SourceInfo(
                    url,
                    str(item.get("desc") or "TikTok video")[:300],
                    "TikTok",
                    float(duration) if duration else None,
                    media_address(video.get("cover")),
                    address,
                    {"User-Agent": USER_AGENT, "Referer": "https://www.tiktok.com/"},
                    "native",
                    tuple(dict.fromkeys(alternatives)),
                )
        raise DownloadError("TikTok did not expose a downloadable video in this page.")

    def _instagram(self, url: str, page: PageData) -> SourceInfo:
        address = media_address(page.meta.get("og:video:secure_url")) or media_address(
            page.meta.get("og:video")
        )
        duration = None
        title = page.meta.get("og:title") or "Instagram video"
        thumbnail = page.meta.get("og:image")
        match = re.search(r"/(?:p|reel|reels|tv)/([\w-]+)", urlsplit(url).path)
        shortcode = match[1] if match else None
        for document in page.documents:
            nodes = list(dictionaries(document))
            tagged = [
                item for item in nodes if item.get("shortcode") or item.get("code")
            ]
            roots = [
                item
                for item in tagged
                if (item.get("shortcode") or item.get("code")) == shortcode
            ]
            # If a page identifies posts, only inspect the requested post's subtree.
            # Otherwise accept structured single-video data such as JSON-LD.
            candidates = (
                [item for root in roots for item in dictionaries(root)]
                if tagged
                else nodes
            )
            for item in candidates:
                candidate = media_address(item.get("video_url")) or media_address(
                    item.get("contentUrl")
                )
                versions = item.get("video_versions")
                if not candidate and isinstance(versions, list):
                    versions = sorted(
                        (version for version in versions if isinstance(version, dict)),
                        key=lambda version: (
                            (version.get("width") or 0) * (version.get("height") or 0)
                        ),
                        reverse=True,
                    )
                    candidate = next(
                        (
                            address
                            for version in versions
                            if (address := media_address(version.get("url")))
                        ),
                        None,
                    )
                if candidate:
                    address = address or candidate
                    value = item.get("video_duration")
                    if isinstance(value, (int, float)):
                        duration = float(value)
                    title = str(item.get("name") or title)[:300]
                    break
            if address:
                break
        if not address:
            raise DownloadError(
                "Instagram did not expose a downloadable video in this page."
            )
        return SourceInfo(
            url,
            title,
            "Instagram",
            duration,
            thumbnail,
            address,
            {"User-Agent": USER_AGENT, "Referer": "https://www.instagram.com/"},
            "native",
        )


class FallbackExtractor:
    def __init__(self, native: Extractor, fallback: Extractor):
        self.native = native
        self.fallback = fallback

    def inspect(self, url: str, cancelled: Event) -> SourceInfo:
        validate_url(url)
        try:
            return self.native.inspect(url, cancelled)
        except Cancelled:
            raise
        except (DownloadError, ValueError, TypeError, KeyError):
            return self.fallback.inspect(url, cancelled)
