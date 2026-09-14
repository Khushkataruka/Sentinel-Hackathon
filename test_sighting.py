import urllib.request
import json
import time

# 1. Get a random sighting from the live_sightings endpoint
req1 = urllib.request.Request('http://localhost:8001/live_sightings')
req1.add_header('X-Sentinel-User', 'admin')
req1.add_header('Accept', 'application/json')
with urllib.request.urlopen(req1) as resp:
    sightings = json.loads(resp.read().decode())
    if not sightings:
        print("No sightings found")
        exit(1)
    target = sightings[0]
    read_id = target['read_id']
    print(f"Target sighting: {read_id} (Camera: {target.get('camera_id')})")

# 2. Search for routes for that sighting
data = json.dumps({}).encode('utf-8')
req2 = urllib.request.Request(
    f'http://localhost:8001/search/sighting/{read_id}',
    data=data,
    method='POST'
)
req2.add_header('X-Sentinel-User', 'admin')
req2.add_header('Content-Type', 'application/json')
req2.add_header('Accept', 'application/json')

print("Starting search...")
start = time.time()
try:
    with urllib.request.urlopen(req2, timeout=60) as resp:
        result = json.loads(resp.read().decode())
        print(f"Search successful in {time.time()-start:.2f}s")
        print(f"Found {result.get('candidate_count')} candidates and {result.get('route_count')} routes")
        print("Routes:")
        for r in result.get('routes', [])[:3]:
            print(f"  Score {r.get('score'):.2f}: {r.get('cameras')}")
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
