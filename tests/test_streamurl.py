"""Grid stream URLs. The rules here are the ones that fail silently.

An unencoded '@' does not error, it connects to the wrong host. A credential
left on a URL does not error, it lands in a log file. Both are tested.
"""

from __future__ import annotations

from sentinel.core import streamurl

BARE = "rtsp://103.250.160.189:8554/stream/cam04"


def test_the_at_in_an_email_is_percent_encoded():
    url = streamurl.with_credentials(BARE, "alice@example.com", "hunter2")
    assert url == "rtsp://alice%40example.com:hunter2@103.250.160.189:8554/stream/cam04"
    # The host must survive: an unencoded '@' would end the userinfo early
    # and send the connection to example.com.
    assert "@103.250.160.189:8554" in url


def test_a_password_with_url_syntax_in_it_is_encoded_too():
    url = streamurl.with_credentials(BARE, "a@b.com", "p@ss:w/rd?#")
    assert url == "rtsp://a%40b.com:p%40ss%3Aw%2Frd%3F%23@103.250.160.189:8554/stream/cam04"
    assert streamurl.strip_credentials(url) == BARE


def test_credentials_replace_rather_than_stack():
    once = streamurl.with_credentials(BARE, "a@b.com", "one")
    twice = streamurl.with_credentials(once, "a@b.com", "two")
    # Exactly one '@' survives: the userinfo separator. The email's own is
    # encoded, and the first call's credentials were replaced, not prepended.
    assert twice.count("@") == 1
    assert "two" in twice and "one" not in twice
    assert streamurl.strip_credentials(twice) == BARE


def test_missing_credentials_leave_the_url_alone():
    assert streamurl.with_credentials(BARE, "", "") == BARE
    assert streamurl.with_credentials(BARE, "a@b.com", "") == BARE


def test_redact_keeps_the_shape_and_loses_the_secret():
    url = streamurl.with_credentials(BARE, "alice@example.com", "hunter2")
    redacted = streamurl.redact(url)
    assert "hunter2" not in redacted
    assert "alice" not in redacted
    assert redacted == "rtsp://***:***@103.250.160.189:8554/stream/cam04"
    # Nothing to hide, nothing changed.
    assert streamurl.redact(BARE) == BARE
    assert streamurl.redact("") == ""


def test_retarget_moves_the_host_and_keeps_everything_else():
    catalogue = "rtsp://cctv.corp8.cloud:8554/stream/cam04?x=1"
    assert streamurl.retarget(catalogue, "103.250.160.189", 8554) == (
        "rtsp://103.250.160.189:8554/stream/cam04?x=1"
    )


def test_retarget_preserves_credentials():
    url = streamurl.with_credentials("rtsp://cctv.corp8.cloud/stream/cam04",
                                     "a@b.com", "pw")
    moved = streamurl.retarget(url, "10.0.0.1", 8554)
    assert moved == "rtsp://a%40b.com:pw@10.0.0.1:8554/stream/cam04"


def test_retarget_without_a_host_is_a_no_op():
    """How a single-host deployment opts out."""
    assert streamurl.retarget(BARE, None, 8554) == BARE
    assert streamurl.retarget(BARE, "", 8554) == BARE


def test_retarget_keeps_the_existing_port_when_none_is_given():
    assert streamurl.retarget("rtsp://old:9000/s/1", "new") == "rtsp://new:9000/s/1"
    assert streamurl.retarget("rtsp://old/s/1", "new") == "rtsp://new/s/1"


def test_fallback_url_shapes_match_the_guide():
    assert streamurl.rtsp_url("cam04", "103.250.160.189") == BARE
    assert streamurl.whep_url("cam04", "103.250.160.189") == (
        "http://103.250.160.189:8889/stream/cam04/whep"
    )
    assert streamurl.hls_url("cam04", "https://cctv.corp8.cloud/") == (
        "https://cctv.corp8.cloud/cam04/index.m3u8"
    )
