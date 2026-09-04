"""Plate detection and reading.

Only runs on cameras whose survey says plates are readable. Section 3.1 is
blunt: plates are not readable on most of this estate, and identification
runs on appearance with ANPR wherever it happens to work.

The top few guesses are kept, not just the best one. That is what makes the
confusion-weighted fuzzy matching in correlation work at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import rng_for

log = get_logger(__name__)

#: Indian plate formats. Validating against these rejects a large amount of
#: OCR garbage before it ever reaches the database.
PLATE_PATTERNS = [
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$"),   # GJ01AB1234, modern
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,2}\d{1,4}$"),  # older short forms
    re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$"),            # Bharat series
]

STATE_CODES = {
    "GJ", "MH", "RJ", "MP", "DL", "UP", "KA", "TN", "AP", "TS", "HR", "PB",
    "WB", "KL", "OD", "BR", "JH", "CG", "UK", "HP", "GA", "AS", "CH", "DD", "DN",
}


@dataclass
class PlateRead:
    text: str
    confidence: float
    rank: int
    valid_format: bool
    bbox: tuple[int, int, int, int] | None = None


def validate(text: str) -> bool:
    cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())
    if len(cleaned) < 6 or cleaned[:2] not in STATE_CODES:
        return False
    return any(pattern.match(cleaned) for pattern in PLATE_PATTERNS)


class PlateReader(Protocol):
    is_stub: bool

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]: ...


class StubPlateReader:
    """Reads nothing, most of the time -- which is the honest stub.

    Section 3.1 says plates are not legible on most of this estate. A stub
    that returned a plate for every vehicle would make the platform look like
    it solves the problem it explicitly cannot solve, and every route would
    come out plate-anchored and wrongly confident.

    So this returns hypotheses for roughly one crop in eight, deterministically,
    and marks them.
    """

    is_stub = True
    HIT_RATE = 0.125

    def __init__(self) -> None:
        log.warning(
            "stub_plate_reader",
            reason="no ANPR weights; plate reads are placeholders",
            expected_at=str(settings.plate_ocr_model_path),
        )

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]:
        rng = rng_for(crop)
        if rng.random() > self.HIT_RATE:
            return []

        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        state = sorted(STATE_CODES)[int(rng.integers(len(STATE_CODES)))]
        series = "".join(letters[int(rng.integers(len(letters)))] for _ in range(2))
        digits = f"{int(rng.integers(0, 10000)):04d}"
        base = f"{state}{int(rng.integers(1, 39)):02d}{series}{digits}"

        reads = [PlateRead(base, round(0.55 + float(rng.random()) * 0.35, 3), 1,
                           validate(base))]
        # Alternatives that differ by exactly the confusions OCR really makes,
        # so the fuzzy matcher in correlation has something realistic to chew on.
        confusions = {"0": "D", "1": "I", "8": "B", "5": "S", "2": "Z"}
        for rank in range(2, top_k + 1):
            variant = list(base)
            for i, ch in enumerate(variant):
                if ch in confusions and rng.random() < 0.4:
                    variant[i] = confusions[ch]
                    break
            text = "".join(variant)
            reads.append(
                PlateRead(text, round(reads[0].confidence * (0.8 ** (rank - 1)), 3),
                          rank, validate(text))
            )
        return reads


class PyTorchPlateReader:
    """Plate detector plus EasyOCR reader over PyTorch and Ultralytics.

    Adapted from prototype/anpr_v2.py.
    Processes vehicle crops or full video frames. Runs fine-tuned YOLO for license
    plate localization, cuts out the plate, and applies EasyOCR with format
    sanitization and Indian registration validation.
    """

    is_stub = False

    def __init__(
        self,
        lp_model_path: Path | str,
        vehicle_model_path: Path | str | None = None,
        device: str | None = None,
        conf: float = 0.25,
    ) -> None:
        import easyocr
        import torch
        from ultralytics import YOLO

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.conf = conf
        self.lp_model_path = Path(lp_model_path)
        self.lp_detector = YOLO(str(self.lp_model_path)).to(self.device)

        self.vehicle_model_path = Path(vehicle_model_path) if vehicle_model_path else None
        self.vehicle_detector = (
            YOLO(str(self.vehicle_model_path)).to(self.device)
            if self.vehicle_model_path and self.vehicle_model_path.exists()
            else None
        )

        self.ocr_engine = easyocr.Reader(["en"], gpu=(self.device == "cuda"))
        log.info(
            "pytorch_plate_reader_loaded",
            lp_model=str(self.lp_model_path),
            vehicle_model=str(self.vehicle_model_path) if self.vehicle_model_path else None,
            device=self.device,
        )

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]:
        """Process one vehicle crop.

        Implements the PlateReader protocol called by PlateWorker.
        Returns up to top_k PlateRead candidates.
        """
        if crop is None or crop.size == 0:
            return []

        h, w = crop.shape[:2]
        if h < 10 or w < 10:
            return []

        lp_res = self.lp_detector.predict(
            source=crop,
            conf=self.conf,
            imgsz=640,
            verbose=False,
            device=self.device,
        )

        if not lp_res or not lp_res[0].boxes or len(lp_res[0].boxes) == 0:
            return []

        boxes = sorted(lp_res[0].boxes, key=lambda b: -float(b.conf[0]))
        reads: list[PlateRead] = []
        seen_texts: set[str] = set()

        for box in boxes:
            lx1, ly1, lx2, ly2 = box.xyxy[0].cpu().numpy().astype(int)
            lp_score = float(box.conf[0].cpu().numpy())

            lx1, ly1 = max(0, lx1), max(0, ly1)
            lx2, ly2 = min(w, lx2), min(h, ly2)

            if lx2 <= lx1 or ly2 <= ly1:
                continue

            lp_cutout = crop[ly1:ly2, lx1:lx2]
            if lp_cutout.size == 0:
                continue

            try:
                ocr_results = self.ocr_engine.readtext(lp_cutout)
            except Exception as exc:
                log.debug("easyocr_read_failed", error=str(exc))
                continue

            if not ocr_results:
                continue

            for item in ocr_results:
                if len(item) < 3:
                    continue
                raw_text, ocr_prob = item[1], float(item[2])
                clean_text = re.sub(r"[^A-Z0-9]", "", str(raw_text).upper()).strip()
                if not clean_text or clean_text in seen_texts:
                    continue

                seen_texts.add(clean_text)
                combined_conf = round(lp_score * ocr_prob, 4)
                is_valid = validate(clean_text)
                reads.append(
                    PlateRead(
                        text=clean_text,
                        confidence=combined_conf,
                        rank=len(reads) + 1,
                        valid_format=is_valid,
                        bbox=(int(lx1), int(ly1), int(lx2), int(ly2)),
                    )
                )

        reads.sort(key=lambda r: (1 if r.valid_format else 0, r.confidence), reverse=True)
        for rank, r in enumerate(reads[:top_k], start=1):
            r.rank = rank

        return reads[:top_k]

    def process_frame(
        self,
        frame: np.ndarray,
        *,
        vehicle_conf: float = 0.25,
        lp_conf: float | None = None,
        crop_padding: float = 0.12,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Process an entire video frame: detect vehicles, extract crops, read plates.

        Returns vehicle detections and recognized plates matching the sightings
        and plate_hypotheses DB schema.
        """
        if frame is None or frame.size == 0:
            return []

        h, w = frame.shape[:2]
        if self.vehicle_detector is None:
            log.warning("no_vehicle_detector_loaded", action="returning_empty")
            return []

        veh_results = self.vehicle_detector.predict(
            source=frame,
            conf=vehicle_conf,
            verbose=False,
            device=self.device,
        )
        if not veh_results or not veh_results[0].boxes:
            return []

        results: list[dict[str, Any]] = []
        for box in veh_results[0].boxes:
            vx1, vy1, vx2, vy2 = [int(v) for v in box.xyxy[0].cpu().numpy().astype(int)]
            v_conf = float(box.conf[0].cpu().numpy())
            cls_idx = int(box.cls[0].cpu().numpy())
            cls_name = veh_results[0].names.get(cls_idx, "vehicle").lower()

            pw = int((vx2 - vx1) * crop_padding)
            ph = int((vy2 - vy1) * crop_padding)
            crop_x1 = int(max(0, vx1 - pw))
            crop_y1 = int(max(0, vy1 - ph))
            crop_x2 = int(min(w, vx2 + pw))
            crop_y2 = int(min(h, vy2 + ph))

            veh_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
            reads = self(veh_crop, top_k=top_k)

            valid_reads = [r for r in reads if r.valid_format]
            best_read = valid_reads[0] if valid_reads else (reads[0] if reads else None)

            global_lp_bbox = None
            if best_read and best_read.bbox:
                lx1, ly1, lx2, ly2 = best_read.bbox
                global_lp_bbox = [
                    int(crop_x1 + lx1),
                    int(crop_y1 + ly1),
                    int(crop_x1 + lx2),
                    int(crop_y1 + ly2),
                ]

            results.append({
                "vehicle_bbox": [vx1, vy1, vx2, vy2],
                "crop_bbox": [crop_x1, crop_y1, crop_x2, crop_y2],
                "class": cls_name,
                "vehicle_conf": round(v_conf, 4),
                "plate_detected": best_read is not None,
                "plate_bbox": global_lp_bbox,
                "plate_text": best_read.text if best_read else None,
                "plate_confidence": best_read.confidence if best_read else None,
                "valid_format": best_read.valid_format if best_read else False,
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

        return results


def process_anpr_frame(
    frame: np.ndarray,
    frame_id: int = 0,
    vehicle_conf: float = 0.25,
    lp_conf: float = 0.25,
    crop_padding: float = 0.12,
    reader: PlateReader | None = None,
    save_annotated: bool = False,
    output_path: Path | str | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Frame processing helper compatible with prototype/anpr_v2.py signature.

    Takes a raw frame, runs vehicle and plate detection, and returns
    (annotated_frame, frame_results).
    """
    import cv2

    if reader is None:
        reader = load_plate_reader()

    annotated = frame.copy() if save_annotated and frame is not None else frame
    if frame is None or frame.size == 0:
        return annotated, []

    if isinstance(reader, PyTorchPlateReader):
        results = reader.process_frame(
            frame,
            vehicle_conf=vehicle_conf,
            lp_conf=lp_conf,
            crop_padding=crop_padding,
        )
    else:
        results = []

    for item in results:
        item["frame_id"] = frame_id
        if save_annotated and annotated is not None:
            vx1, vy1, vx2, vy2 = item["vehicle_bbox"]
            cv2.rectangle(annotated, (vx1, vy1), (vx2, vy2), (255, 200, 0), 2)
            cv2.putText(
                annotated,
                item["class"],
                (vx1, max(20, vy1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 200, 0),
                2,
            )
            if item.get("plate_detected") and item.get("plate_bbox"):
                gx1, gy1, gx2, gy2 = item["plate_bbox"]
                plate_str = item.get("plate_text") or "PLATE"
                cv2.rectangle(annotated, (gx1, gy1), (gx2, gy2), (0, 255, 0), 2)
                cv2.putText(
                    annotated,
                    plate_str,
                    (gx1, max(20, gy1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2,
                )

    if save_annotated and output_path and annotated is not None:
        cv2.imwrite(str(output_path), annotated)

    return annotated, results


class OnnxPlateReader:
    """Detector plus OCR, both ONNX, both CPU. Not wired in the scaffold."""

    is_stub = False

    def __init__(self, detect_path: Path, ocr_path: Path) -> None:   # pragma: no cover
        raise NotImplementedError(
            "ANPR not wired; export the plate detector and reader to ONNX"
        )

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]:  # pragma: no cover
        raise NotImplementedError


def _find_file(candidates: list[Path | str | None]) -> Path | None:
    for item in candidates:
        if not item:
            continue
        p = Path(item)
        if p.exists() and p.is_file():
            return p
    return None


def load_plate_reader(
    lp_model_path: Path | str | None = None,
    vehicle_model_path: Path | str | None = None,
) -> PlateReader:
    curr_dir = Path(__file__).resolve().parent
    root_dir = Path(__file__).resolve().parents[6]

    lp_path = lp_model_path or _find_file([
        curr_dir / "license-plate-finetune-v1m.pt",
        curr_dir / "license-plate-finetune-v1n.pt",
        curr_dir / "plate_detect.pt",
        settings.plate_detect_model_path
        if str(settings.plate_detect_model_path).endswith(".pt")
        else None,
        settings.plate_detect_model_path.with_suffix(".pt"),
        root_dir / "var" / "models" / "license-plate-finetune-v1m.pt",
        root_dir / "var" / "models" / "license-plate-finetune-v1n.pt",
        root_dir / "var" / "models" / "plate_detect.pt",
        Path("./var/models/license-plate-finetune-v1m.pt"),
        Path("./var/models/plate_detect.pt"),
    ])

    veh_path = vehicle_model_path or _find_file([
        curr_dir / "vehicle_detection_master_v1.pt",
        settings.detect_model_path
        if str(settings.detect_model_path).endswith(".pt")
        else None,
        settings.detect_model_path.with_suffix(".pt"),
        root_dir / "var" / "models" / "vehicle_detection_master_v1.pt",
        Path("./var/models/vehicle_detection_master_v1.pt"),
    ])

    if lp_path:
        try:
            return PyTorchPlateReader(
                lp_model_path=lp_path,
                vehicle_model_path=veh_path,
            )
        except Exception as exc:
            log.error("pytorch_plate_reader_load_failed", path=str(lp_path), error=str(exc))

    detect_onnx, ocr_onnx = settings.plate_detect_model_path, settings.plate_ocr_model_path
    if Path(detect_onnx).exists() and Path(ocr_onnx).exists():
        try:
            return OnnxPlateReader(Path(detect_onnx), Path(ocr_onnx))
        except NotImplementedError:
            log.error("anpr_not_implemented")

    return StubPlateReader()
