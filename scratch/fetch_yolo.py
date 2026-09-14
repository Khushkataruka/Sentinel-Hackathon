import os
from pathlib import Path
import httpx

out_dir = Path("./var/models")
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "yolo.onnx"

url = "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.onnx"
print("Downloading YOLOv8n ONNX from", url)
r = httpx.get(url, follow_redirects=True, timeout=60.0)
print("Status:", r.status_code, "Length:", len(r.content))

if r.status_code == 200:
    out_file.write_bytes(r.content)
    print("Saved yolo.onnx to", out_file.resolve())
else:
    print("Failed to download YOLOv8n ONNX")
