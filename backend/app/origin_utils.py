from __future__ import annotations

from urllib.parse import urlsplit


def is_allowed_browser_origin(
    origin: str | None,
    host: str | None,
    configured_origins: list[str],
) -> bool:
    """Allow configured frontends and the host currently serving the request."""
    if not origin:
        return False
    normalized = origin.rstrip("/")
    if normalized in configured_origins:
        return True
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.netloc.lower() == (host or "").strip().lower()
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )
