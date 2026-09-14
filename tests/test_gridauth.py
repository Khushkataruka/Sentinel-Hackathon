"""Password login for the grid's control surface. No network: httpx.post is
replaced with canned sign-in responses.

The rules that matter are when login() does NOT post. Every process used to
mint its own session, and past a burst of logins the grid refuses every
request -- so one session is shared through a file, and logins are rare.
"""

from __future__ import annotations

import os
import stat
import time

import httpx
import pytest
from sentinel.core import gridauth
from sentinel.core.config import settings

BASE = "https://grid.example"


@pytest.fixture(autouse=True)
def credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(gridauth, "_last_attempt", float("-inf"))
    monkeypatch.setattr(settings, "grid_email", "alice@example.com")
    monkeypatch.setattr(settings, "grid_password", "hunter2")
    monkeypatch.setattr(settings, "sentinel_token", "expired-token")
    monkeypatch.setattr(settings, "sentinel_cookie", "")


def answer(monkeypatch, status, set_cookie=None):
    headers = {"set-cookie": set_cookie} if set_cookie else {}
    response = httpx.Response(
        status, headers=headers, request=httpx.Request("POST", f"{BASE}/auth/login")
    )
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)


def forbid_login(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("should not log in"))


def share(cookie, age_s=0.0):
    """A session some process already wrote, `age_s` seconds ago."""
    path = gridauth._session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cookie)
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def test_login_writes_one_owner_only_session_and_drops_the_stale_token(monkeypatch):
    assert gridauth.session_headers() == {"Authorization": "Bearer expired-token"}
    answer(monkeypatch, 302, "sentinel=fresh; Path=/; HttpOnly")

    assert gridauth.login(BASE + "/")
    assert gridauth.session_cookies() == {"sentinel": "fresh"}
    assert gridauth.session_headers() == {}
    assert stat.S_IMODE(gridauth._session_file().stat().st_mode) == 0o600


def test_another_process_reuses_the_shared_session_without_logging_in(monkeypatch):
    """What used to mint a session per process and per restart."""
    share("from-registry")
    forbid_login(monkeypatch)

    assert gridauth.session_cookies() == {"sentinel": "from-registry"}
    # A request refused with no session at all finds the shared one.
    assert gridauth.login(BASE, rejected={})


def test_a_session_someone_else_already_replaced_is_reused(monkeypatch):
    share("newer")
    forbid_login(monkeypatch)

    assert gridauth.login(BASE, rejected={"sentinel": "older"})


def test_a_refused_shared_session_is_not_replaced_within_the_interval(monkeypatch):
    """A fresh session refused straight away is the grid throttling us;
    logging in again on every 403 is what kept the block in place."""
    share("current", age_s=60)
    forbid_login(monkeypatch)

    assert not gridauth.login(BASE, rejected={"sentinel": "current"})


def test_a_refused_shared_session_is_replaced_once_the_interval_passes(monkeypatch):
    share("dead", age_s=gridauth.LOGIN_INTERVAL_S + 1)
    answer(monkeypatch, 302, "sentinel=fresh; Path=/")

    assert gridauth.login(BASE, rejected={"sentinel": "dead"})
    assert gridauth.session_cookies() == {"sentinel": "fresh"}


def test_a_failed_login_is_not_retried_straight_away(monkeypatch):
    """A failed POST writes no file, so the file's age alone would allow
    another attempt on the very next request."""
    answer(monkeypatch, 200)  # the sign-in page again, no cookie
    assert not gridauth.login(BASE)

    forbid_login(monkeypatch)
    assert not gridauth.login(BASE)
    assert gridauth.session_cookies() == {}
    assert gridauth.session_headers() == {"Authorization": "Bearer expired-token"}


def test_no_credentials_means_no_request(monkeypatch):
    monkeypatch.setattr(settings, "grid_password", "")
    forbid_login(monkeypatch)

    assert not gridauth.login(BASE)


@pytest.mark.parametrize(
    ("status", "content_type", "expected"),
    [
        (403, "text/plain", True),
        (401, "", True),
        (200, "text/html; charset=utf-8", True),  # redirected to the sign-in page
        (200, "application/json", False),
        (200, "video/mp2t", False),  # an HLS segment is not a sign-in page
        (404, "application/json", False),
    ],
)
def test_signed_out(status, content_type, expected):
    response = httpx.Response(status, headers={"content-type": content_type})
    assert gridauth.signed_out(response) is expected
