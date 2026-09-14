"""Password login for the grid's control surface. No network: httpx.post is
replaced with canned sign-in responses.

The rules that matter are when login() does NOT post: the grid keeps one
session per account, so a second login invalidates the first, and a burst of
logins gets every request refused.
"""

from __future__ import annotations

import httpx
import pytest
from sentinel.core import gridauth
from sentinel.core.config import settings

BASE = "https://grid.example"


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setattr(gridauth, "_login_cookies", {})
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


def test_login_keeps_the_session_and_drops_the_stale_token(monkeypatch):
    assert gridauth.session_headers() == {"Authorization": "Bearer expired-token"}
    answer(monkeypatch, 302, "sentinel=fresh; Path=/; HttpOnly")

    assert gridauth.login(BASE + "/")
    assert gridauth.session_cookies() == {"sentinel": "fresh"}
    assert gridauth.session_headers() == {}


def test_a_rejected_login_leaves_the_session_alone(monkeypatch):
    answer(monkeypatch, 200)  # the sign-in page again, no cookie

    assert not gridauth.login(BASE)
    assert gridauth.session_cookies() == {}
    assert gridauth.session_headers() == {"Authorization": "Bearer expired-token"}


def test_no_credentials_means_no_request(monkeypatch):
    monkeypatch.setattr(settings, "grid_password", "")
    forbid_login(monkeypatch)

    assert not gridauth.login(BASE)


def test_a_session_someone_else_already_replaced_is_reused(monkeypatch):
    """Two requests refused together: the second must not log the first out."""
    monkeypatch.setattr(gridauth, "_login_cookies", {"sentinel": "newer"})
    forbid_login(monkeypatch)

    assert gridauth.login(BASE, rejected={"sentinel": "older"})
    assert gridauth.session_cookies() == {"sentinel": "newer"}


def test_the_current_session_refused_means_log_in_again(monkeypatch):
    """Another process's login replaced ours: the cookie we hold is dead."""
    monkeypatch.setattr(gridauth, "_login_cookies", {"sentinel": "dead"})
    answer(monkeypatch, 302, "sentinel=fresh; Path=/")

    assert gridauth.login(BASE, rejected={"sentinel": "dead"})
    assert gridauth.session_cookies() == {"sentinel": "fresh"}


def test_logins_are_rate_limited(monkeypatch):
    """A fresh session refused straight away is the grid throttling us;
    logging in again on every 403 would keep it that way."""
    answer(monkeypatch, 302, "sentinel=fresh; Path=/")
    assert gridauth.login(BASE)

    forbid_login(monkeypatch)
    assert not gridauth.login(BASE, rejected={"sentinel": "fresh"})


@pytest.mark.parametrize(
    ("status", "content_type", "expected"),
    [
        (403, "text/plain", True),  # replaced by another login, or throttled
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
