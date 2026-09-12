from __future__ import annotations

import re
import httpx
from fastapi import APIRouter, HTTPException, Response, status
from sentinel.core import gridauth
from sentinel.core.config import settings
from sentinel.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/stream", tags=["stream"])

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_active_cookies: dict[str, str] = {}


def _get_base_url() -> str:
    return (settings.sentinel_base_url or "https://cctv.corp8.cloud").rstrip("/")


def _get_cookies() -> dict[str, str]:
    global _active_cookies
    if not _active_cookies:
        _active_cookies = gridauth.session_cookies()
    return _active_cookies


async def _ensure_login(client: httpx.AsyncClient) -> bool:
    global _active_cookies
    email = settings.grid_email
    password = settings.grid_password
    if not email or not password:
        log.warning("stream_proxy_no_credentials", detail="SENTINEL_GRID_EMAIL / PASSWORD missing")
        return False

    login_url = f"{_get_base_url()}/auth/login"
    try:
        res = await client.post(
            login_url,
            data={"email": email, "password": password},
            headers=BROWSER_HEADERS,
            follow_redirects=False,
        )
        if res.status_code in (200, 302, 303):
            if "sentinel" in res.cookies:
                _active_cookies["sentinel"] = res.cookies["sentinel"]
                log.info("stream_proxy_login_success", cookie_len=len(_active_cookies.get("sentinel", "")))
                return True
            # Also check Set-Cookie header if not parsed into res.cookies
            set_cookie = res.headers.get("set-cookie", "")
            if "sentinel=" in set_cookie:
                match = re.search(r"sentinel=([^;]+)", set_cookie)
                if match:
                    _active_cookies["sentinel"] = match.group(1)
                    log.info("stream_proxy_login_cookie_extracted")
                    return True
    except Exception as exc:
        log.error("stream_proxy_login_failed", error=str(exc))
    return False


async def _proxy_fetch(client: httpx.AsyncClient, target_url: str) -> httpx.Response:
    cookies = _get_cookies()
    headers = {**BROWSER_HEADERS, **gridauth.session_headers()}
    
    res = await client.get(target_url, headers=headers, cookies=cookies, follow_redirects=True)
    
    # If unauthenticated or redirected to HTML sign-in page
    if res.status_code in (401, 403) or "json" in res.headers.get("content-type", "") or "<html" in res.text.lower()[:200]:
        if await _ensure_login(client):
            cookies = _get_cookies()
            res = await client.get(target_url, headers=headers, cookies=cookies, follow_redirects=True)
            
    return res


@router.get("/enc.key")
@router.get("/{camera_id}/enc.key")
async def get_encryption_key(camera_id: str = ""):
    target_url = f"{_get_base_url()}/enc.key"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Failed to fetch encryption key")
        return Response(
            content=res.content,
            media_type="application/octet-stream",
            headers={"Access-Control-Allow-Origin": "*"}
        )


@router.get("/{camera_id}/index.m3u8")
async def get_manifest(camera_id: str):
    target_url = f"{_get_base_url()}/{camera_id}/index.m3u8"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=f"Failed to fetch stream manifest for {camera_id}")
        
        content = res.text
        # Rewrite URI="/enc.key" or URI="enc.key" to URI="/stream/{camera_id}/enc.key"
        content = re.sub(
            r'URI=["\']?/?enc\.key["\']?',
            f'URI="/stream/{camera_id}/enc.key"',
            content
        )
        
        return Response(
            content=content,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache"
            }
        )


@router.get("/{camera_id}/{path:path}")
async def get_segment(camera_id: str, path: str):
    if path == "enc.key":
        return await get_encryption_key(camera_id)
        
    target_url = f"{_get_base_url()}/{camera_id}/{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _proxy_fetch(client, target_url)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=f"Failed to fetch segment {path}")
        
        media_type = "video/mp2t" if path.endswith(".ts") else res.headers.get("content-type", "application/octet-stream")
        return Response(
            content=res.content,
            media_type=media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600"
            }
        )
