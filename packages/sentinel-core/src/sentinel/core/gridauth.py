"""The session the grid's HTTP surface requires.

The catalogue and the HLS endpoints sit behind the CDN's sign-in: an
unauthenticated request is redirected to /auth/login and answered with HTML,
not JSON. Three credentials get past it, and all come from the environment
rather than adapter.toml, because an adapter folder is configuration that
gets copied around and a session is not:

    SENTINEL_SENTINEL_TOKEN / SENTINEL_SENTINEL_COOKIE   a pasted session
    SENTINEL_GRID_EMAIL / SENTINEL_GRID_PASSWORD         login() mints one

Pasted sessions expire; the password does not. Callers make their request
with whatever session exists and call login() only when signed_out() says the
grid refused it.

Logins are expensive to the grid in two ways, and login() guards both:

- One session per account: a login invalidates the cookie the previous one
  issued. login() is serialised, and skips the POST when another caller has
  already replaced the session that was refused.
- A burst of logins gets every request answered with a plain-text 403, fresh
  session or not. A caller that read that 403 as "signed out" and logged in
  again would keep the block in place, so login() posts at most once per
  LOGIN_INTERVAL_S.

Ceiling: both guards are per process. The registry and ingest share the
account, so each still logs the other out and recovers on its next refused
request. Ingest only needs the session for the catalogue (media goes over
RTSP with its own credentials), so the churn is rare. If it ever costs
frames, the upgrade is one session stored where every process reads it -- a
database row -- instead of one login per process.

Distinct from the RTSP/WHEP credentials in sentinel.core.streamurl: those
authenticate the *media* connection on the gateway and travel in the URL.
These authenticate the *control* surface on the CDN and travel in headers.
"""

from __future__ import annotations

import re
import threading
import time

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

#: Minimum seconds between login POSTs from one process.
LOGIN_INTERVAL_S = 60.0

#: Cookies from a password login. Process-wide: the grid issues a session per
#: account, so every caller in the process shares the one login() obtained.
_login_cookies: dict[str, str] = {}
_last_attempt = float("-inf")

# A threading lock, not an asyncio one: ingest calls login() synchronously
# and the registry calls it through asyncio.to_thread.
_login_lock = threading.Lock()


def session_headers() -> dict[str, str]:
    # Once login() has a fresh session, a pasted token is at best redundant
    # and at worst the expired credential that sent us to login() at all.
    if _login_cookies:
        return {}
    token = settings.sentinel_token.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def session_cookies() -> dict[str, str]:
    """Session cookie, as `name=value` pairs in SENTINEL_SENTINEL_COOKIE,
    overridden by whatever login() obtained.

    From settings, not os.environ: pydantic loads .env into the settings
    object and not into the environment, so a cookie set there was invisible.
    """
    cookies: dict[str, str] = {}
    for part in settings.sentinel_cookie.strip().split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    cookies.update(_login_cookies)
    return cookies


def cookie_header() -> str:
    """The same cookies as one header value, for clients that take headers
    rather than a jar -- FFmpeg's HTTP reader, in particular."""
    return "; ".join(f"{k}={v}" for k, v in session_cookies().items())


def have_session() -> bool:
    return bool(session_headers() or session_cookies())


def signed_out(response: httpx.Response) -> bool:
    """Whether the grid refused the session: a 401/403 (expired, replaced by
    another login, or throttled), or a redirect that ended on the HTML
    sign-in page."""
    if response.status_code in (401, 403):
        return True
    return "text/html" in response.headers.get("content-type", "").lower()


def login(base_url: str, rejected: dict[str, str] | None = None) -> bool:
    """Sign in with SENTINEL_GRID_EMAIL / PASSWORD and keep the session cookie.

    `rejected` is the cookie jar the caller's refused request was sent with.
    If another caller has replaced that session since, the newer one is used
    as it is: logging in again would invalidate it. Returns whether a session
    the caller has not yet tried is now available.

    Synchronous, because the ingest adapter is; async callers wrap it in
    asyncio.to_thread.
    """
    global _last_attempt
    if not (settings.grid_email and settings.grid_password):
        log.warning("grid_login_no_credentials", detail="SENTINEL_GRID_EMAIL / PASSWORD missing")
        return False

    with _login_lock:
        current = _login_cookies.get("sentinel")
        if rejected is not None and current and current != rejected.get("sentinel"):
            return True

        since = time.monotonic() - _last_attempt
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

        _login_cookies["sentinel"] = token
        log.info("grid_login_success", cookie_len=len(token))
        return True
