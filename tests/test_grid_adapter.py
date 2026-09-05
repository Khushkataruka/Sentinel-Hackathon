"""The sentinel-grid adapter's URL handling.

Two properties are worth a test rather than a comment: media URLs are moved
onto the host that can serve them, and the password exists only inside the
handle open() returns.
"""

from __future__ import annotations

import pytest
from sentinel.core.config import settings
from sentinel.ingest.adapters.builtin.sentinel_grid import SentinelGridAdapter

CATALOGUE = {
    "cameras": [
        {
            # The catalogue is written for browsers, so the RTSP URL can name
            # the CDN host, which resolves and then carries nothing.
            "id": "cam04",
            "location": "13 CN Vidhyalaya",
            "live": True,
            "codec": "h265",
            "width": 1920,
            "height": 1080,
            "fps": 25,
            "rtsp_url": "rtsp://cctv.corp8.cloud:8554/stream/cam04",
            "hls_live_url": "https://cctv.corp8.cloud/cam04/index.m3u8",
        },
        # Nothing but an id: everything else falls back to the documented
        # pattern, which is what a new camera on the grid looks like.
        {"id": "cam30"},
    ]
}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(settings, "grid_email", "alice@example.com")
    monkeypatch.setattr(settings, "grid_password", "hunter2")
    monkeypatch.setattr(settings, "grid_media_host", "103.250.160.189")
    monkeypatch.setattr(settings, "grid_prefer_hls", False)
    a = SentinelGridAdapter({"base_url": "https://cctv.corp8.cloud"})
    monkeypatch.setattr(a, "_fetch_catalogue", lambda: CATALOGUE)
    return a


def test_media_urls_move_off_the_cdn(adapter):
    cam04 = adapter.enumerate()[0]
    assert cam04.url == "rtsp://103.250.160.189:8554/stream/cam04"
    # HLS is the one endpoint a CDN can carry, so it stays where it was.
    assert cam04.metadata["hls"] == "https://cctv.corp8.cloud/cam04/index.m3u8"
    assert cam04.metadata["whep"] == "http://103.250.160.189:8889/stream/cam04/whep"


def test_a_bare_entry_falls_back_to_the_documented_pattern(adapter):
    cam30 = adapter.enumerate()[1]
    assert cam30.url == "rtsp://103.250.160.189:8554/stream/cam30"
    assert cam30.metadata["hls"] == "https://cctv.corp8.cloud/cam30/index.m3u8"


def test_enumerate_never_carries_credentials(adapter):
    """CameraRefs are persisted and served. A password in one is a leak."""
    for ref in adapter.enumerate():
        for value in [ref.url, *(str(v) for v in ref.metadata.values())]:
            assert "hunter2" not in value
            assert "alice" not in value


def test_open_attaches_credentials_percent_encoded(adapter):
    handle = adapter.open("cam04")
    assert handle.url == (
        "rtsp://alice%40example.com:hunter2@103.250.160.189:8554/stream/cam04"
    )
    assert handle.transport == "tcp"
    assert handle.options["rtsp_transport"] == "tcp"


def test_prefer_hls_opens_the_cdn_endpoint_with_the_session(adapter, monkeypatch):
    """The guide's fallback for a machine that cannot reach 8554/TCP."""
    monkeypatch.setattr(settings, "grid_prefer_hls", True)
    monkeypatch.setattr(settings, "sentinel_cookie", "session=abc123")
    adapter.prefer_hls = True
    adapter._cached_at = 0.0
    handle = adapter.open("cam04")
    assert handle.url == "https://cctv.corp8.cloud/cam04/index.m3u8"
    # No userinfo on an HLS URL: the CDN authenticates with the session.
    assert "hunter2" not in handle.url
    assert handle.options["headers"] == "Cookie: session=abc123"
    assert "rtsp_transport" not in handle.options


def test_an_unknown_camera_is_an_adapter_error(adapter):
    from sentinel.ingest.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        adapter.open("cam99")
