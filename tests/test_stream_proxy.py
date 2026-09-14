"""The stream proxy reports a slow or unreachable grid as a gateway error.

The grid regularly takes longer than the proxy's client timeout. The timeout
used to escape as an unhandled exception, which the player saw as a bare 500.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from sentinel.registry.routers import stream_proxy

URL = "https://grid.example/cam01/index.m3u8"


def fetch_with(handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await stream_proxy._proxy_fetch(client, URL)

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(httpx.ReadTimeout("slow grid"), 504), (httpx.ConnectError("grid down"), 502)],
)
def test_grid_failures_become_gateway_errors(error, status_code):
    def handler(request):
        raise error

    with pytest.raises(HTTPException) as caught:
        fetch_with(handler)
    assert caught.value.status_code == status_code


def test_a_playlist_passes_through():
    response = fetch_with(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/vnd.apple.mpegurl"}, text="#EXTM3U"
        )
    )
    assert response.status_code == 200
    assert response.text == "#EXTM3U"
