"""The session the grid's HTTP surface requires.

The catalogue and the HLS endpoints sit behind the CDN's sign-in: an
unauthenticated request is redirected to /auth/login and answered with HTML,
not JSON. One of two credentials gets past it, and both come from the
environment rather than adapter.toml, because an adapter folder is
configuration that gets copied around and a session is not.

Distinct from the RTSP/WHEP credentials in sentinel.core.streamurl: those
authenticate the *media* connection on the gateway and travel in the URL.
These authenticate the *control* surface on the CDN and travel in headers.
"""

from __future__ import annotations

from sentinel.core.config import settings


def session_headers() -> dict[str, str]:
    token = settings.sentinel_token.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def session_cookies() -> dict[str, str]:
    """Session cookie, as `name=value` pairs in SENTINEL_SENTINEL_COOKIE.

    From settings, not os.environ: pydantic loads .env into the settings
    object and not into the environment, so a cookie set there was invisible.
    """
    cookies: dict[str, str] = {}
    for part in settings.sentinel_cookie.strip().split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def cookie_header() -> str:
    """The same cookies as one header value, for clients that take headers
    rather than a jar -- FFmpeg's HTTP reader, in particular."""
    return "; ".join(f"{k}={v}" for k, v in session_cookies().items())


def have_session() -> bool:
    return bool(session_headers() or session_cookies())
