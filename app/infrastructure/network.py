import ipaddress
import socket
import time
from pathlib import Path
from threading import Event
from urllib.parse import urljoin, urlsplit

import httpx

from app.domain.models import Cancelled, DownloadError, Progress
from app.domain.urls import validate_url

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


def validate_public_host(url: str) -> None:
    validate_url(url)
    try:
        addresses = socket.getaddrinfo(
            urlsplit(url).hostname, None, type=socket.SOCK_STREAM
        )
    except OSError as error:
        raise DownloadError(
            "Could not reach this website. Check the URL and your connection."
        ) from error
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise DownloadError("Only public internet media URLs are supported.")


class PublicHttpClient:
    """Bounded requests with explicit redirect validation for native extractors."""

    def __init__(self, max_bytes: int, timeout: int = 3600):
        self.max_bytes = max_bytes
        self.timeout = timeout

    def _read(
        self,
        url: str,
        cancelled: Event,
        *,
        headers: dict | None = None,
        destination: Path | None = None,
        progress: Progress | None = None,
    ) -> bytes:
        deadline = time.monotonic() + self.timeout
        limit = self.max_bytes if destination else 8 * 1024**2
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        with httpx.Client(
            timeout=httpx.Timeout(20, read=30), trust_env=False
        ) as client:
            for _ in range(6):
                if cancelled.is_set():
                    raise Cancelled("Cancelled.")
                validate_public_host(url)
                with client.stream("GET", url, headers=request_headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise DownloadError(
                                "The website returned an invalid redirect."
                            )
                        url = urljoin(url, location)
                        continue
                    if response.status_code >= 400:
                        raise DownloadError(
                            "The website refused this request. The video may require login or be unavailable."
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if destination and (
                        content_type.startswith("text/")
                        or "json" in content_type
                        or "mpegurl" in content_type
                    ):
                        raise DownloadError(
                            "The media endpoint did not return a downloadable file."
                        )
                    total = int(response.headers.get("content-length", "0"))
                    if total > limit:
                        raise DownloadError(
                            "This download exceeds the configured size limit."
                        )
                    data = bytearray()
                    received = 0
                    handle = destination.open("wb") if destination else None
                    try:
                        for chunk in response.iter_bytes(256 * 1024):
                            if cancelled.is_set():
                                raise Cancelled("Cancelled.")
                            if time.monotonic() > deadline:
                                raise DownloadError("The download timed out.")
                            received += len(chunk)
                            if received > limit:
                                raise DownloadError(
                                    "This download exceeds the configured size limit."
                                )
                            if handle:
                                handle.write(chunk)
                            else:
                                data.extend(chunk)
                            if progress:
                                progress(
                                    "Downloading source",
                                    received / total * 100 if total else None,
                                )
                    finally:
                        if handle:
                            handle.close()
                    return bytes(data)
            raise DownloadError("The website redirected too many times.")

    def text(self, url: str, cancelled: Event) -> str:
        try:
            return self._read(url, cancelled).decode("utf-8", errors="replace")
        except httpx.HTTPError as error:
            raise DownloadError(
                "Could not read the video page. Check your connection or try another link."
            ) from error

    def download(
        self,
        url: str,
        destination: Path,
        cancelled: Event,
        headers: dict[str, str],
        progress: Progress,
    ) -> Path:
        try:
            self._read(
                url,
                cancelled,
                headers=headers,
                destination=destination,
                progress=progress,
            )
            return destination
        except httpx.HTTPError as error:
            raise DownloadError(
                "The media download was interrupted. Please try again."
            ) from error
