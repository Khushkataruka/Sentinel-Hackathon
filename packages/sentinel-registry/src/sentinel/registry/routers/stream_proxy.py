from __future__ import annotations

import asyncio
import re

import httpx
from fastapi import APIRouter, HTTPException, Response, status
from sentinel.core import gridauth
from sentinel.core.config import settings
from sentinel.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/stream", tags=["stream"])


def _get_base_url() -> str:
    return (settings.sentinel_base_url or "https://cctv.corp8.cloud").rstrip("/")


async def _proxy_fetch(client: httpx.AsyncClient, target_url: str) -> httpx.Response:
    async def get(cookies: dict[str, str]) -> httpx.Response:
        return await client.get(
            target_url,
            headers={**gridauth.BROWSER_HEADERS, **gridauth.session_headers()},
            cookies=cookies,
            follow_redirects=True,
        )

    cookies = gridauth.session_cookies()
    res = await get(cookies)

    # Signed out, or the grid's JSON auth error. A player fetches several
    # segments at once, so passing the refused cookies lets login() skip the
    # POST when a sibling request has already logged in.
    if gridauth.signed_out(res) or "json" in res.headers.get("content-type", ""):
        if await asyncio.to_thread(gridauth.login, _get_base_url(), cookies):
            res = await get(gridauth.session_cookies())

    return res


@router.get("/enc.key")
@router.get("/{camera_id}/enc.key")
async def get_encryption_key(camera_id: str = ""):
    target_url = f"{_get_base_url()}/enc.key"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code, detail="Failed to fetch encryption key"
            )
        return Response(
            content=res.content,
            media_type="application/octet-stream",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@router.get("/{camera_id}/index.m3u8")
async def get_manifest(camera_id: str):
    target_url = f"{_get_base_url()}/{camera_id}/index.m3u8"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code,
                detail=f"Failed to fetch stream manifest for {camera_id}",
            )

        content = res.text
        # Rewrite URI="/enc.key" or URI="enc.key" to URI="/stream/{camera_id}/enc.key"
        content = re.sub(
            r'URI=["\']?/?enc\.key["\']?', f'URI="/stream/{camera_id}/enc.key"', content
        )

        return Response(
            content=content,
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )


@router.get("/{camera_id}/{path:path}")
async def get_segment(camera_id: str, path: str):
    if path == "enc.key":
        return await get_encryption_key(camera_id)

    target_url = f"{_get_base_url()}/{camera_id}/{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code, detail=f"Failed to fetch segment {path}"
            )

        media_type = (
            "video/mp2t"
            if path.endswith(".ts")
            else res.headers.get("content-type", "application/octet-stream")
        )
        return Response(
            content=res.content,
            media_type=media_type,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
        )
