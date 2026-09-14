import os
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).parent.parent
os.chdir(HERE)

run_dir = HERE / "out" / "demo_run"
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "videos").mkdir(parents=True, exist_ok=True)
(run_dir / "tracks").mkdir(parents=True, exist_ok=True)

python_exe = sys.executable
ingest_exe = HERE / "venv" / "Scripts" / "sentinel-ingest.exe"
pipeline_exe = HERE / "venv" / "Scripts" / "sentinel-pipeline.exe"
correlation_exe = HERE / "venv" / "Scripts" / "sentinel-correlation.exe"

print("Starting pipeline run on cctv052x2004080516x01638.mp4...")

# Step 1: Ingest offline
print("\n--- [1/4] Ingest Offline (decode, detect, track, write sightings) ---")
cmd_ingest = [
    str(ingest_exe), "offline", "cctv052x2004080516x01638.mp4",
    "--out", str(run_dir)
]
res = subprocess.run(cmd_ingest, capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print("Ingest Error:", res.stderr)

# Step 2: Pipelines (describe, embed, plate, violate)
print("\n--- [2/4] Drain Model Pipelines (describe, embed, plate, violate) ---")
cmd_pipe = [str(pipeline_exe), "run", "--all", "--drain"]
res_pipe = subprocess.run(cmd_pipe, capture_output=True, text=True)
print(res_pipe.stdout)
if res_pipe.returncode != 0:
    print("Pipeline Error:", res_pipe.stderr)

# Step 3: Correlation
print("\n--- [3/4] Correlation & Cross-camera matching ---")
cmd_corr_drain = [str(correlation_exe), "run", "--drain"]
res_corr_d = subprocess.run(cmd_corr_drain, capture_output=True, text=True)
print(res_corr_d.stdout)

cmd_corr_cross = [
    str(correlation_exe), "crosscam",
    "--cameras", "cctv052x2004080516x01638",
    "--out", str(run_dir / "correlations.json"),
    "--html", str(run_dir / "report.html")
]
res_corr_c = subprocess.run(cmd_corr_cross, capture_output=True, text=True)
print(res_corr_c.stdout)

# Step 4: Annotate
print("\n--- [4/4] Annotate Video ---")
cmd_ann = [
    str(ingest_exe), "annotate", "cctv052x2004080516x01638.mp4",
    "--tracks", str(run_dir / "tracks"),
    "--out", str(run_dir / "videos"),
    "--correlations", str(run_dir / "correlations.json")
]
res_ann = subprocess.run(cmd_ann, capture_output=True, text=True)
print(res_ann.stdout)

print("\nPipeline execution finished!")
