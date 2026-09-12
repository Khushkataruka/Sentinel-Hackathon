import httpx
from sentinel.core.config import settings
from sentinel.core import gridauth

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    **gridauth.session_headers()
}
cookies = gridauth.session_cookies()

url = f"{settings.sentinel_base_url}/cam01/index.m3u8"
r = httpx.get(url, headers=headers, cookies=cookies, follow_redirects=True)
print("HLS with User-Agent status:", r.status_code)
print("HLS content-type:", r.headers.get("content-type"))
print("HLS content excerpt:\n", r.text[:500])

# Let's test RTSP / WHEP endpoint as well!
# RTSP: rtsp://email:password@103.250.160.189:8554/stream/cam01
# WHEP: http://103.250.160.189:8889/stream/cam01/whep or http://email:password@103.250.160.189:8889/stream/cam01/whep
whep_url = f"http://103.250.160.189:8889/cam01/whep"
try:
    r_whep = httpx.post(whep_url, headers=headers, timeout=3.0)
    print("WHEP status:", r_whep.status_code, r_whep.text[:200])
except Exception as e:
    print("WHEP err:", e)
