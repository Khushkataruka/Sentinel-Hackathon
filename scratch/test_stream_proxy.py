import httpx
from sentinel.core.config import settings
from sentinel.core import gridauth

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    **gridauth.session_headers()
}
cookies = gridauth.session_cookies()

# Test segment fetch
seg_url = f"{settings.sentinel_base_url}/cam01/seg00000.ts"
r_seg = httpx.get(seg_url, headers=headers, cookies=cookies, follow_redirects=True)
print("Segment status:", r_seg.status_code, "Content length:", len(r_seg.content))

# Test key fetch
key_url = f"{settings.sentinel_base_url}/enc.key"
r_key = httpx.get(key_url, headers=headers, cookies=cookies, follow_redirects=True)
print("Key status:", r_key.status_code, "Content length:", len(r_key.content), "Hex:", r_key.content.hex())
