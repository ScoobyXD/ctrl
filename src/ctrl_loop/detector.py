"""FP32 UI detector preprocessing and image-pixel decoding (no input actions)."""
from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Letterbox:
    image_width: int
    image_height: int
    input_size: int
    gain: float
    resized_width: int
    resized_height: int
    pad_left: int
    pad_top: int

    def as_dict(self):
        return asdict(self)


def preprocess(rgb: np.ndarray, size: int = 1280):
    """Match Ultralytics 8.3.70 square LetterBox, with RGB input explicitly."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("Expected uint8 HWC RGB image")
    height, width = rgb.shape[:2]
    if not height or not width or size <= 0 or size % 32:
        raise ValueError("Invalid image size or detector size (multiple of 32 required)")
    gain = min(size / height, size / width)
    resized_width, resized_height = round(width * gain), round(height * gain)
    left = round((size - resized_width) / 2 - 0.1)
    top = round((size - resized_height) / 2 - 0.1)
    resized = cv2.resize(rgb, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(
        resized, top, size - resized_height - top,
        left, size - resized_width - left, cv2.BORDER_CONSTANT, value=(114, 114, 114),
    )
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    transform = Letterbox(width, height, size, gain, resized_width, resized_height, left, top)
    return tensor, transform


def iou_one(box, boxes):
    intersection = np.maximum(0, np.minimum(box[2:], boxes[:, 2:]) - np.maximum(box[:2], boxes[:, :2]))
    inter_area = intersection[:, 0] * intersection[:, 1]
    area = (box[2] - box[0]) * (box[3] - box[1])
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return inter_area / np.maximum(area + areas - inter_area, 1e-9)


def decode(raw, transform: Letterbox, confidence=0.25, iou_threshold=0.45, max_detections=300):
    """Decode the selected one-class YOLO head; boxes use original PNG pixels."""
    raw = np.asarray(raw)
    if raw.ndim != 3 or raw.shape[0] != 1 or raw.shape[1] != 5:
        raise ValueError(f"Expected one-class [1,5,N] detector output, got {raw.shape}")
    if not np.isfinite(raw).all():
        raise ValueError("Detector output contains NaN or infinity")
    if not 0 <= confidence <= 1 or not 0 <= iou_threshold <= 1 or max_detections <= 0:
        raise ValueError("Invalid confidence, IoU or detection limit")
    predictions = raw[0].T
    predictions = predictions[predictions[:, 4] > confidence]
    if not len(predictions):
        return []
    scores = predictions[:, 4]
    boxes = np.column_stack((predictions[:, :2] - predictions[:, 2:4] / 2,
                             predictions[:, :2] + predictions[:, 2:4] / 2))
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1]) & (scores <= 1)
    boxes, scores = boxes[valid], scores[valid]
    order = np.argsort(-scores, kind="stable")[:30000]
    kept = []
    while len(order) and len(kept) < max_detections:
        index = order[0]
        kept.append(index)
        remaining = order[1:]
        order = remaining[iou_one(boxes[index], boxes[remaining]) <= iou_threshold]
    detections = []
    for index in kept:
        box = boxes[index].astype(np.float64)
        box[[0, 2]] = np.clip((box[[0, 2]] - transform.pad_left) / transform.gain, 0, transform.image_width)
        box[[1, 3]] = np.clip((box[[1, 3]] - transform.pad_top) / transform.gain, 0, transform.image_height)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        detections.append({"box_xyxy": box.tolist(), "confidence": float(scores[index]),
                           "class_id": 0, "class_name": "icon"})
    return detections
