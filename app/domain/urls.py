import ipaddress
from urllib.parse import urlsplit

from app.domain.models import DownloadError


def validate_url(url: str) -> str:
    """Validate the URL shape without performing network I/O."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise ValueError
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError
    except ValueError as error:
        raise DownloadError("Enter a public http:// or https:// video URL.") from error
    return url
