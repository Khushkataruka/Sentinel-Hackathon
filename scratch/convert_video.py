import subprocess
import imageio_ffmpeg
from pathlib import Path

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
print("Using ffmpeg binary at:", ffmpeg_path)

input_file = Path("out/demo_run/videos/cctv052x2004080516x01638.annotated.mp4")
temp_output = Path("out/demo_run/videos/cctv052x2004080516x01638.h264.mp4")

cmd = [
    ffmpeg_path,
    "-y",
    "-i", str(input_file),
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    str(temp_output)
]

res = subprocess.run(cmd, capture_output=True, text=True)
print("FFmpeg returncode:", res.returncode)
if res.returncode == 0:
    temp_output.replace(input_file)
    print("Successfully re-encoded annotated video to standard H.264 format!")
else:
    print("Error:", res.stderr)
