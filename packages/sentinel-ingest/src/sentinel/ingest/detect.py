"""Vehicle and person detection.

ONNX Runtime, so it runs on machines without a graphics card. The
preprocessing and NMS are written out rather than pulled from a framework,
because the input shape has to vary per camera -- the estate is not a
uniform grid and a fixed-shape batch across every camera will not work.

If the weights file is absent the loader returns a NullDetector that finds
nothing and says so once. The scaffold has to be runnable before the ONNX
exports exist; a missing model is a logged condition, not a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger

log = get_logger(__name__)

#: COCO ids we care about, mapped to the class names the schema uses.
#: 'auto' (autorickshaw) has no COCO class; it needs a fine-tuned model, and
#: until then autos land as 'car' or 'truck'. Worth knowing when reading counts.
COCO_TO_CLASS = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck", "auto", "bicycle"}


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]     # x1, y1, x2, y2 in image pixels
    score: float
    cls: str

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    @property
    def centre(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0


class Detector(Protocol):
    model_id: int | None

    def __call__(self, image: np.ndarray) -> list[Detection]: ...


# ---------------------------------------------------------------------------
# Preprocessing and NMS
# ---------------------------------------------------------------------------


def letterbox(
    image: np.ndarray, size: int = 640
) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize preserving aspect ratio, pad to square.

    Returns the padded image, the scale applied, and the (left, top) padding,
    so boxes can be mapped back to the original frame. Cameras differ in
    resolution; this is what lets one model serve all of them.
    """
    import cv2

    h, w = image.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    left, top = (size - new_w) // 2, (size - new_h) // 2
    canvas[top : top + new_h, left : left + new_w] = resized
    return canvas, scale, (left, top)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression. Boxes are xyxy."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]

    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class NullDetector:
    """Finds nothing. Used when no weights are present.

    Every other part of the pipeline runs against it: adapters connect,
    frames decode, PTS is tracked, buckets close. Only sightings are absent,
    which makes it a useful way to test the plumbing on a laptop.
    """

    model_id = None

    def __init__(self) -> None:
        log.warning(
            "null_detector",
            reason="no detection weights found; ingest will produce no sightings",
            expected_at=str(settings.detect_model_path),
        )

    def __call__(self, image: np.ndarray) -> list[Detection]:
        return []


class OnnxYoloDetector:
    """YOLO-family detector over ONNX Runtime.

    Handles the two output layouts in circulation: v8-style
    (1, 4+num_classes, num_boxes) with no objectness, and v5-style
    (1, num_boxes, 5+num_classes) with it.
    """

    def __init__(
        self,
        model_path: Path,
        *,
        conf: float | None = None,
        iou: float | None = None,
        model_id: int | None = None,
        input_size: int = 640,
    ) -> None:
        import onnxruntime as ort

        self.conf = conf if conf is not None else settings.detect_conf
        self.iou = iou if iou is not None else settings.detect_iou
        self.model_id = model_id
        self.input_size = input_size

        self.session = ort.InferenceSession(
            str(model_path), providers=settings.onnx_providers
        )
        self.input_name = self.session.get_inputs()[0].name

        shape = self.session.get_inputs()[0].shape
        if isinstance(shape[-1], int) and shape[-1] > 0:
            self.input_size = int(shape[-1])

        log.info(
            "detector_loaded", path=str(model_path), size=self.input_size,
            providers=self.session.get_providers(),
        )

    def __call__(self, image: np.ndarray) -> list[Detection]:
        padded, scale, (pad_left, pad_top) = letterbox(image, self.input_size)
        blob = padded[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

        raw = self.session.run(None, {self.input_name: blob})[0]
        predictions = np.squeeze(raw)

        # v8 ships (4+nc, n); v5 ships (n, 5+nc). Orient to (n, k).
        if predictions.ndim != 2:
            return []
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        if predictions.shape[1] >= 6 and predictions[:, 4].max() <= 1.0 and (
            predictions.shape[1] - 5 == len(COCO_TO_CLASS)
        ):
            objectness = predictions[:, 4]
            class_scores = predictions[:, 5:] * objectness[:, None]
        else:
            class_scores = predictions[:, 4:]

        class_ids = class_scores.argmax(axis=1)
        scores = class_scores.max(axis=1)

        keep_mask = scores >= self.conf
        if not keep_mask.any():
            return []

        boxes_cxcywh = predictions[keep_mask, :4]
        scores = scores[keep_mask]
        class_ids = class_ids[keep_mask]

        # cxcywh in letterboxed space -> xyxy in original image space
        cx, cy, w, h = boxes_cxcywh.T
        boxes = np.stack(
            [
                (cx - w / 2 - pad_left) / scale,
                (cy - h / 2 - pad_top) / scale,
                (cx + w / 2 - pad_left) / scale,
                (cy + h / 2 - pad_top) / scale,
            ],
            axis=1,
        )
        img_h, img_w = image.shape[:2]
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, img_w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, img_h)

        # Class-wise NMS: a motorcycle inside a truck's box is two objects.
        results: list[Detection] = []
        for class_id in np.unique(class_ids):
            name = COCO_TO_CLASS.get(int(class_id))
            if name is None:
                continue
            idx = np.where(class_ids == class_id)[0]
            for k in nms(boxes[idx], scores[idx], self.iou):
                j = idx[k]
                x1, y1, x2, y2 = boxes[j].astype(int).tolist()
                if x2 <= x1 or y2 <= y1:
                    continue
                results.append(Detection((x1, y1, x2, y2), float(scores[j]), name))
        return results


def load_detector(model_id: int | None = None) -> Detector:
    """Load the configured detector, degrading to NullDetector."""
    path = settings.detect_model_path
    if not Path(path).exists():
        return NullDetector()
    try:
        return OnnxYoloDetector(Path(path), model_id=model_id)
    except Exception as exc:
        log.error("detector_load_failed", path=str(path), error=str(exc))
        return NullDetector()
