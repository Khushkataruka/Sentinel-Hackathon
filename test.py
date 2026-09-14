import urllib.request
import json

# Test the search endpoint with a colour filter
data = json.dumps({"colour": "white"}).encode('utf-8')
req = urllib.request.Request(
    'http://localhost:8080/search/description',
    data=data,
    headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Sentinel-User': 'admin'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print(f"Search ID: {result['search_id']}")
        print(f"Candidates: {result['candidate_count']}")
        print(f"Routes: {result['route_count']}")
        print(f"Sightings returned: {len(result.get('sightings', []))}")
        if result.get('sightings'):
            s = result['sightings'][0]
            print(f"\nFirst sighting:")
            print(f"  crop_ref: {s.get('crop_ref')}")
            print(f"  colour: {s.get('colour')}")
            print(f"  make: {s.get('make')}")
            print(f"  model: {s.get('model')}")
            print(f"  caption: {s.get('caption', '')[:80]}")
            print(f"  score: {s.get('score')}")
            print(f"  matched_on: {s.get('matched_on')}")
except urllib.error.HTTPError as e:
    print(f"Error: {e.code}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"Error: {e}")
