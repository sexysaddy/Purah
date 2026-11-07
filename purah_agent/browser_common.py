"""Shared guardrails for opt-in browser automation backends."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class BrowserNavigationError(ValueError):
    """A requested browser navigation is outside PURAH's public-page policy."""


def external_http_url(raw_url: str) -> str:
    """Validate an LLM-supplied navigation URL (no internal hosts or schemes).

    This deliberately does not claim to be a DNS-rebinding defense; browser remote
    debugging is powerful and MUST only be enabled for a dedicated profile.
    """
    if not isinstance(raw_url, str) or not raw_url.strip() or len(raw_url) > 2048:
        raise BrowserNavigationError("Требуется HTTP(S) URL длиной до 2048 символов.")
    url = raw_url.strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port  # Reject malformed port numbers early.
    except ValueError as exc:
        raise BrowserNavigationError("Некорректный URL браузера.") from exc
    if (
        parsed.scheme not in {"http", "https"} or not host or
        parsed.username is not None or parsed.password is not None or
        host in {"localhost", "localhost.localdomain"} or
        host.endswith((".localhost", ".local", ".internal", ".test")) or
        "\\" in url or any(ord(char) < 32 for char in url)
    ):
        raise BrowserNavigationError("Разрешены только внешние HTTP(S)-страницы без учётных данных в URL.")
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if not addr.is_global:
            raise BrowserNavigationError("Навигация на приватный/локальный IP запрещена.")
    return url


def readable_browser_tab(url: str, start_pages: frozenset[str]) -> bool:
    """Allow public HTTP(S) tabs and known empty start tabs for navigation only."""
    if url in start_pages:
        return True
    try:
        external_http_url(url)
    except BrowserNavigationError:
        return False
    return True
