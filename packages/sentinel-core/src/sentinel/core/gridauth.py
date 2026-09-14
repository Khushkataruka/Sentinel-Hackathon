"""The session the grid's HTTP surface requires.

The catalogue and the HLS endpoints sit behind the CDN's sign-in: an
unauthenticated request is redirected to /auth/login and answered with HTML,
not JSON. Three credentials get past it, and all come from the environment
rather than adapter.toml, because an adapter folder is configuration that
gets copied around and a session is not:

    SENTINEL_SENTINEL_TOKEN / SENTINEL_SENTINEL_COOKIE   a pasted session
    SENTINEL_GRID_EMAIL / SENTINEL_GRID_PASSWORD         login() mints one

ONE SESSION FOR EVERY PROCESS. Each process used to log in for itself and
hold its cookie in memory, so ingest, the registry and every restart minted
another session -- and past a burst of logins the grid refuses every
request, fresh session or not. So a login's cookie is written to a file
beside the media root that all processes read, and a new login happens only
when the shared cookie itself was refused, and at most once per
LOGIN_INTERVAL_S across all of them (the file's mtime is the clock).

Ceiling: the lock serialising logins is per process, so two processes whose
refusals land in the same instant could both log in once. The interval
bounds that to one extra session. A file lock is the upgrade if it matters.

Distinct from the RTSP/WHEP credentials in sentinel.core.streamurl: those
authenticate the *media* connection on the gateway and travel in the URL.
These authenticate the *control* surface on the CDN and travel in headers.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import httpx
from sentinel.core.config import settings
from sentinel.core.logging import get_logger

log = get_logger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

#: Minimum seconds between logins, across every process sharing the file.
LOGIN_INTERVAL_S = 600.0

#: This process's last attempt, successful or not. A failed login writes no
#: file, so without this a refused POST could be retried on every request.
_last_attempt = float("-inf")

# A threading lock, not an asyncio one: ingest calls login() synchronously
# and the registry calls it through asyncio.to_thread.
_login_lock = threading.Lock()


def _session_file() -> Path:
    """var/grid_session: beside the media root, never committed."""
    return Path(settings.media_root).parent / "grid_session"


def _shared_cookie() -> str | None:
    try:
        return _session_file().read_text().strip() or None
    except OSError:
        return None


def _store_shared_cookie(token: str) -> None:
    path = _session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    # Owner-only from creation: the file holds a live credential.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    os.replace(temporary, path)


def _seconds_since_shared_login() -> float:
    try:
        return time.time() - _session_file().stat().st_mtime
    except OSError:
        return float("inf")


def session_headers() -> dict[str, str]:
    # Once a login has produced a session, a pasted token is at best
    # redundant and at worst the expired credential that forced the login.
    if _shared_cookie():
        return {}
    token = settings.sentinel_token.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def session_cookies() -> dict[str, str]:
    """Session cookie, as `name=value` pairs in SENTINEL_SENTINEL_COOKIE,
    overridden by the shared login session.

    From settings, not os.environ: pydantic loads .env into the settings
    object and not into the environment, so a cookie set there was invisible.
    """
    cookies: dict[str, str] = {}
    for part in settings.sentinel_cookie.strip().split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    shared = _shared_cookie()
    if shared:
        cookies["sentinel"] = shared
    return cookies


def cookie_header() -> str:
    """The same cookies as one header value, for clients that take headers
    rather than a jar -- FFmpeg's HTTP reader, in particular."""
    return "; ".join(f"{k}={v}" for k, v in session_cookies().items())


def have_session() -> bool:
    return bool(session_headers() or session_cookies())


def signed_out(response: httpx.Response) -> bool:
    """Whether the grid refused the session: a 401/403, or a redirect that
    ended on the HTML sign-in page."""
    if response.status_code in (401, 403):
        return True
    return "text/html" in response.headers.get("content-type", "").lower()


def login(base_url: str, rejected: dict[str, str] | None = None) -> bool:
    """Sign in with SENTINEL_GRID_EMAIL / PASSWORD, sharing the session.

    `rejected` is the cookie jar the caller's refused request was sent with.
    If the shared session is already a different cookie -- another process or
    request logged in since -- that one is used as it is. Returns whether a
    session the caller has not yet tried is now available.

    Synchronous, because the ingest adapter is; async callers wrap it in
    asyncio.to_thread.
    """
    global _last_attempt
    if not (settings.grid_email and settings.grid_password):
        log.warning("grid_login_no_credentials", detail="SENTINEL_GRID_EMAIL / PASSWORD missing")
        return False

    with _login_lock:
        shared = _shared_cookie()
        if shared and shared != (rejected or {}).get("sentinel"):
            return True

        since = min(_seconds_since_shared_login(), time.monotonic() - _last_attempt)
        if since < LOGIN_INTERVAL_S:
            log.warning("grid_login_throttled", retry_in_s=round(LOGIN_INTERVAL_S - since))
            return False
        _last_attempt = time.monotonic()

        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/auth/login",
                data={"email": settings.grid_email, "password": settings.grid_password},
                headers=BROWSER_HEADERS,
                follow_redirects=False,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            log.error("grid_login_failed", error=str(exc))
            return False

        token = response.cookies.get("sentinel")
        if token is None:
            # Not every Set-Cookie parses into the jar (a missing domain, say).
            match = re.search(r"sentinel=([^;]+)", response.headers.get("set-cookie", ""))
            token = match.group(1) if match else None
        if response.status_code not in (200, 302, 303) or not token:
            log.error("grid_login_rejected", status=response.status_code)
            return False

        _store_shared_cookie(token)
        log.info("grid_login_success", cookie_len=len(token))
        return True
