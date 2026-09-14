import urllib.request
import json

# Test 1: Check what models are available
try:
    req = urllib.request.Request('http://localhost:8008/v1/models')
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        print("=== Available Models ===")
        print(json.dumps(data, indent=2))
except Exception as e:
    print(f"Models endpoint error: {e}")

# Test 2: Try embeddings endpoint
try:
    payload = json.dumps({"model": "google/gemma-3-27b-it", "input": "white sedan"}).encode('utf-8')
    req = urllib.request.Request(
        'http://localhost:8008/v1/embeddings',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        if 'data' in data and len(data['data']) > 0:
            embedding = data['data'][0]['embedding']
            print(f"\n=== Embeddings Work ===")
            print(f"Dimension: {len(embedding)}")
            print(f"First 5 values: {embedding[:5]}")
        else:
            print(f"\n=== Embeddings Response ===")
            print(json.dumps(data, indent=2))
except urllib.error.HTTPError as e:
    print(f"\nEmbeddings HTTP Error: {e.code}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"\nEmbeddings error: {e}")
