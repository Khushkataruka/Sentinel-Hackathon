import httpx

base = "http://localhost:8000"

print("--- Testing /stream/cam01/index.m3u8 ---")
r_m3u8 = httpx.get(f"{base}/stream/cam01/index.m3u8")
print("Status:", r_m3u8.status_code)
print("Content-Type:", r_m3u8.headers.get("content-type"))
print("Body excerpt:\n", r_m3u8.text[:400])

print("\n--- Testing /stream/cam01/enc.key ---")
r_key = httpx.get(f"{base}/stream/cam01/enc.key")
print("Status:", r_key.status_code)
print("Content-Type:", r_key.headers.get("content-type"))
print("Key length:", len(r_key.content), "Hex:", r_key.content.hex())

print("\n--- Testing /stream/cam01/seg00000.ts ---")
r_seg = httpx.get(f"{base}/stream/cam01/seg00000.ts")
print("Status:", r_seg.status_code)
print("Content-Type:", r_seg.headers.get("content-type"))
print("Segment bytes length:", len(r_seg.content))
