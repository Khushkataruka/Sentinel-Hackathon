import httpx
from sentinel.core.config import settings

url = f"{settings.sentinel_base_url}/auth/login"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded"
}

# Try POST login
data = {
    "email": settings.grid_email,
    "password": settings.grid_password
}

r = httpx.post(url, data=data, headers=headers, follow_redirects=False)
print("POST login status:", r.status_code)
print("POST login headers:", dict(r.headers))
print("POST login cookies:", dict(r.cookies))
print("POST login text excerpt:", r.text[:300])
