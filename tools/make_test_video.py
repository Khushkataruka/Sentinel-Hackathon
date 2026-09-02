"""Synthetic traffic that is BUSY at the loop boundary.

The first clip ended on empty road, so at the loop point there was nothing
on screen to dissociate. A real 24/7 traffic recording always has vehicles
in frame, including at the moment it wraps -- that is precisely why the loop
is dangerous. This one does too.
"""
import sys

import cv2
import numpy as np

W, H, FPS, SECONDS = 640, 480, 15, 20
path, seed = sys.argv[1], int(sys.argv[2])
rng = np.random.default_rng(seed)

writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))

lanes = [(140, 60), (215, 65), (295, 70)]
vehicles = []
for lane_i, (y, h) in enumerate(lanes):
    t = -3.0 - lane_i * 1.5
    while t < SECONDS + 4:
        speed = float(rng.uniform(90, 210))
        vehicles.append({
            "t0": t, "y": y + int(rng.integers(-8, 9)), "h": h,
            "w": int(rng.integers(70, 130)), "speed": speed,
            "colour": tuple(int(c) for c in rng.integers(50, 230, 3)),
        })
        t += float(rng.uniform(1.6, 3.4))

for frame_no in range(FPS * SECONDS):
    t = frame_no / FPS
    img = np.full((H, W, 3), 42, dtype=np.uint8)
    for x in range(0, W, 60):
        cv2.rectangle(img, (x, 182), (x + 30, 188), (115, 115, 115), -1)
        cv2.rectangle(img, (x, 262), (x + 30, 268), (115, 115, 115), -1)
    cv2.line(img, (0, 100), (W, 100), (88, 88, 88), 2)
    cv2.line(img, (0, 380), (W, 380), (88, 88, 88), 2)

    for v in vehicles:
        x = int((t - v["t0"]) * v["speed"]) - v["w"]
        if x < -v["w"] or x > W:
            continue
        cv2.rectangle(img, (x, v["y"]), (x + v["w"], v["y"] + v["h"]), v["colour"], -1)
        cv2.rectangle(img, (x + 8, v["y"] + 8), (x + v["w"] - 8, v["y"] + 20), (18, 18, 18), -1)

    writer.write(img)
writer.release()

def on_screen(t):
    return sum(
        1 for v in vehicles
        if -v["w"] <= int((t - v["t0"]) * v["speed"]) - v["w"] <= W
    )
print(f"{path}: {FPS*SECONDS} frames, {len(vehicles)} vehicles; "
      f"on screen at t=0.0s: {on_screen(0.0)}, at t=19.9s: {on_screen(19.9)}")
