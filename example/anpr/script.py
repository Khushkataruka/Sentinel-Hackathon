"""ANPR Pipeline runner script for test images."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

# Ensure sentinel packages are on sys.path
root_dir = Path(__file__).resolve().parents[2]
for pkg in ["sentinel-core", "sentinel-registry", "sentinel-ingest", "sentinel-pipelines", "sentinel-correlation", "sentinel-api"]:
    pkg_src = root_dir / "packages" / pkg / "src"
    if pkg_src.exists() and str(pkg_src) not in sys.path:
        sys.path.insert(0, str(pkg_src))

from sentinel.pipelines.models import anpr

# ==============================================================================
# OCR Engine Selection Switch (TESSERACT or EASYOCR)
# ==============================================================================
OCREngine = anpr.OCREngine
OCR_ENGINE: anpr.OCREngine = anpr.OCREngine.TESSERACT


def run_anpr_on_image(
    image_path: Path | str,
    output_dir: Path | str,
    vehicle_conf: float = 0.20,
    lp_conf: float = 0.20,
    ocr_engine: anpr.OCREngine = OCR_ENGINE,
) -> dict:
    image_path = Path(image_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not image_path.exists():
        raise FileNotFoundError(f"Input image not found: {image_path}")

    frame = cv2.imread(str(image_path))
    if frame is None or frame.size == 0:
        raise ValueError(f"Failed to decode image: {image_path}")

    print(f"[INFO] Loaded input frame: {image_path} (shape: {frame.shape})")
    print(f"[INFO] Using OCR Engine: {ocr_engine.value.upper()}")

    # Load the plate reader (PyTorch fine-tuned YOLO + Tesseract/EasyOCR or Stub)
    reader = anpr.load_plate_reader(ocr_engine=ocr_engine)
    print(f"[INFO] Initialized plate reader: {type(reader).__name__} (is_stub: {reader.is_stub})")

    results_data = {
        "source_image": str(image_path),
        "reader_type": type(reader).__name__,
        "is_stub": reader.is_stub,
        "detections": [],
    }

    annotated = frame.copy()
    h, w = frame.shape[:2]

    # If PyTorchPlateReader, run detection on the frame
    if hasattr(reader, "process_frame"):
        results = reader.process_frame(
            frame,
            vehicle_conf=vehicle_conf,
            lp_conf=lp_conf,
        )
    else:
        results = []

    # If full-frame vehicle detector didn't catch the vehicle in dark scene, or reader is applied directly
    if not results:
        print("[INFO] Running plate reader directly on full frame / vehicle area...")
        reads = reader(frame, top_k=3)
        if reads:
            best = [r for r in reads if r.valid_format]
            best_read = best[0] if best else reads[0]
            results.append({
                "vehicle_bbox": [0, 0, w, h],
                "crop_bbox": [0, 0, w, h],
                "class": "vehicle",
                "vehicle_conf": 1.0,
                "plate_detected": True,
                "plate_bbox": list(best_read.bbox) if best_read.bbox else None,
                "plate_text": best_read.text,
                "plate_confidence": best_read.confidence,
                "valid_format": best_read.valid_format,
                "hypotheses": [
                    {
                        "rank": int(r.rank),
                        "plate": str(r.text),
                        "confidence": float(r.confidence),
                        "valid_format": bool(r.valid_format),
                    }
                    for r in reads
                ],
            })

    print(f"[INFO] Detected {len(results)} vehicle/plate instances.")

    detected_plates_text = []

    for idx, item in enumerate(results, start=1):
        vx1, vy1, vx2, vy2 = item["vehicle_bbox"]
        # Crop vehicle
        v_crop = frame[max(0, vy1):min(h, vy2), max(0, vx1):min(w, vx2)]
        if v_crop.size > 0:
            v_crop_path = output_dir / f"vehicle_crop_{idx}.png"
            cv2.imwrite(str(v_crop_path), v_crop)
            item["vehicle_crop_path"] = str(v_crop_path)

        # Draw vehicle box
        cv2.rectangle(annotated, (vx1, vy1), (vx2, vy2), (255, 200, 0), 2)
        cv2.putText(
            annotated,
            f"{item['class']} ({item['vehicle_conf']:.2f})",
            (vx1, max(20, vy1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 200, 0),
            2,
        )

        # Plate detection handling
        if item.get("plate_detected") and item.get("plate_bbox"):
            px1, py1, px2, py2 = item["plate_bbox"]
            px1, py1 = max(0, px1), max(0, py1)
            px2, py2 = min(w, px2), min(h, py2)

            plate_crop = frame[py1:py2, px1:px2]
            if plate_crop.size > 0:
                plate_crop_path = output_dir / f"detected_plate_{idx}.png"
                cv2.imwrite(str(plate_crop_path), plate_crop)
                item["plate_crop_path"] = str(plate_crop_path)
                print(f"[INFO] Saved detected license plate cutout to: {plate_crop_path}")

            plate_text = item.get("plate_text", "UNKNOWN")
            conf = item.get("plate_confidence", 0.0)
            detected_plates_text.append(f"{plate_text} (conf: {conf:.3f})")

            # Draw license plate box
            cv2.rectangle(annotated, (px1, py1), (px2, py2), (0, 255, 0), 2)
            cv2.putText(
                annotated,
                f"{plate_text} [{conf:.2f}]",
                (px1, max(20, py1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 0),
                2,
            )

        results_data["detections"].append(item)

    # Save annotated full frame
    annotated_path = output_dir / "annotated_frame.png"
    cv2.imwrite(str(annotated_path), annotated)
    results_data["annotated_frame_path"] = str(annotated_path)
    print(f"[INFO] Saved annotated frame to: {annotated_path}")

    # Save JSON metadata
    json_path = output_dir / "ocr_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)
    print(f"[INFO] Saved OCR results JSON to: {json_path}")

    # Save summary text file
    txt_path = output_dir / "ocr_text.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        if detected_plates_text:
            f.write("\n".join(detected_plates_text) + "\n")
        else:
            f.write("No license plates detected.\n")
    print(f"[INFO] Saved OCR text summary to: {txt_path}")

    return results_data


if __name__ == "__main__":
    img_path = root_dir / "example" / "image.png"
    out_dir = root_dir / "example" / "anpr" / "output"

    results = run_anpr_on_image(img_path, out_dir)
    print("\n--- ANPR Pipeline Execution Result ---")
    print(json.dumps(results, indent=2))
