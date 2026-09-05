"""Grid stream URLs: which host serves the media, and the credentials on it.

The camera grid does not serve everything from one place, and the split is
not cosmetic:

  HLS    https://cctv.corp8.cloud/<id>/index.m3u8      CDN, session-gated
  RTSP   rtsp://<email>:<password>@<ip>:8554/stream/<id>   direct, no CDN
  WHEP   http://<email>:<password>@<ip>:8889/stream/<id>/whep

A CDN terminates HTTP. It cannot proxy RTSP's interleaved TCP or WebRTC's
UDP, so those two are published on the gateway's own address and authenticate
per connection with the registered email and access password embedded in the
URL. The `@` in the email is therefore ambiguous with the userinfo separator
and MUST be percent-encoded -- alice%40example.com -- which is the single
thing most likely to be got wrong here, and is why building these URLs by
hand is not left to the call sites.

Two rules follow from credentials living in the URL:

  1. A credentialed URL never leaves the process it was built in. It is not
     written to the adapters table, not returned by an API, not put in a log
     line. redact() exists for every one of those paths.
  2. Credentials are attached at open() time, not at enumerate() time, so a
     CameraRef -- which does get persisted and served -- carries a bare URL.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

#: What redact() puts where the credentials were.
REDACTED = "***:***"


def _split_netloc(netloc: str) -> tuple[str, str]:
    """(userinfo, hostport). Rightmost '@' wins: an unencoded email in the
    userinfo is malformed, but we would rather redact it than mis-parse it."""
    if "@" in netloc:
        userinfo, _, hostport = netloc.rpartition("@")
        return userinfo, hostport
    return "", netloc


def _rebuild(url: str, netloc: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def strip_credentials(url: str) -> str:
    """The URL without userinfo. Safe to persist and to hand to an operator."""
    if not url:
        return url
    _, hostport = _split_netloc(urlsplit(url).netloc)
    return _rebuild(url, hostport)


def redact(url: str) -> str:
    """The URL with the credentials replaced, for logs and error messages.

    Unlike strip_credentials this keeps the shape, so a log line still shows
    that the connection was authenticated at all.
    """
    if not url:
        return url
    userinfo, hostport = _split_netloc(urlsplit(url).netloc)
    if not userinfo:
        return url
    return _rebuild(url, f"{REDACTED}@{hostport}")


def with_credentials(url: str, email: str, password: str) -> str:
    """Embed email and password in the URL, percent-encoded.

    safe="" is deliberate: the default safe set for quote() leaves '/' and
    '@' alone, and an unencoded '@' from the email would terminate the
    userinfo early and send the gateway a hostname of 'gmail.com'.

    Existing userinfo is replaced, so calling this twice is harmless. With
    no credentials configured the URL comes back untouched -- an unauthorised
    connection failing at the gateway is a clearer error than a URL with an
    empty password in it.
    """
    if not url or not email or not password:
        return url
    _, hostport = _split_netloc(urlsplit(url).netloc)
    userinfo = f"{quote(email, safe='')}:{quote(password, safe='')}"
    return _rebuild(url, f"{userinfo}@{hostport}")


def retarget(url: str, host: str | None, port: int | None = None) -> str:
    """Move a URL onto the media host, keeping scheme, path and credentials.

    The catalogue is written for browsers, so its RTSP and WHEP entries can
    still name the CDN hostname, which resolves but cannot carry the media.
    Passing host=None or an empty host leaves the URL alone, which is how a
    deployment that really does serve everything from one place opts out.
    """
    if not url or not host:
        return url
    userinfo, hostport = _split_netloc(urlsplit(url).netloc)
    if port is None:
        _, _, existing_port = hostport.partition(":")
        target = f"{host}:{existing_port}" if existing_port else host
    else:
        target = f"{host}:{port}"
    return _rebuild(url, f"{userinfo}@{target}" if userinfo else target)


def rtsp_url(camera_id: str, host: str, port: int = 8554) -> str:
    """Fallback only. The catalogue is the contract; this is what we use when
    an entry omits the field entirely."""
    return f"rtsp://{host}:{port}/stream/{camera_id}"


def whep_url(camera_id: str, host: str, port: int = 8889) -> str:
    return f"http://{host}:{port}/stream/{camera_id}/whep"


def hls_url(camera_id: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{camera_id}/index.m3u8"


def is_rtsp(url: str) -> bool:
    return url.lower().startswith("rtsp")
