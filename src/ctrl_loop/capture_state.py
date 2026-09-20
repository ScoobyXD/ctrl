"""Capture-scoped immutable records and the single-job lifecycle."""
from dataclasses import dataclass
from datetime import datetime
import math
from uuid import uuid4

import numpy as np

Box = tuple[float, float, float, float]


@dataclass(frozen=True, eq=False)
class CapturedFrame:
    capture_id: str
    captured_at: datetime
    rgb: np.ndarray

    def __post_init__(self):
        if self.rgb.dtype != np.uint8 or self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError("Capture must be an RGB uint8 image")
        if not self.capture_id or min(self.rgb.shape[:2]) <= 0:
            raise ValueError("Empty capture")
        # Independent owned pixels: neither inference nor painting can mutate them.
        pixels = np.array(self.rgb, copy=True, order="C")
        pixels.flags.writeable = False
        object.__setattr__(self, "rgb", pixels)

    @property
    def width(self):
        return self.rgb.shape[1]

    @property
    def height(self):
        return self.rgb.shape[0]


@dataclass(frozen=True)
class DetectionResult:
    frame: CapturedFrame
    boxes: tuple[Box, ...]

    def __post_init__(self):
        boxes = tuple(tuple(float(v) for v in box) for box in self.boxes)
        for box in boxes:
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError("Invalid detection rectangle")
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= self.frame.width and 0 <= y1 < y2 <= self.frame.height):
                raise ValueError("Detection rectangle is outside the capture")
        object.__setattr__(self, "boxes", boxes)


def fit_image(width, height, viewport_width, viewport_height):
    """Return image-to-widget scale and centered offset in logical Qt pixels."""
    if min(width, height, viewport_width, viewport_height) <= 0:
        return 0.0, 0.0, 0.0
    scale = min(viewport_width / width, viewport_height / height)
    return scale, (viewport_width - width * scale) / 2, (viewport_height - height * scale) / 2


class CaptureLifecycle:
    def __init__(self):
        self.phase = "starting"
        self.active_id = None
        self.frame = None
        self.result = None

    def ready(self):
        if self.phase == "starting":
            self.phase = "idle"
            return True
        return False

    def begin(self):
        if self.phase != "idle":
            return None
        self.active_id = uuid4().hex
        self.phase = "capturing"
        return self.active_id

    def accept_frame(self, frame):
        if self.phase != "capturing" or frame.capture_id != self.active_id:
            return False
        self.frame = frame
        self.result = None
        self.phase = "analyzing"
        return True

    def accept_result(self, result):
        # Require the original object as well as ID: reused IDs cannot rebind pixels.
        if self.phase != "analyzing" or result.frame is not self.frame:
            return False
        if result.frame.capture_id != self.active_id:
            return False
        self.result = result
        self.active_id = None
        self.phase = "idle"
        return True

    def fail(self, capture_id):
        if self.phase not in ("capturing", "analyzing") or capture_id != self.active_id:
            return False
        self.active_id = None
        self.phase = "idle"
        return True

    def stop(self):
        self.active_id = None
        self.phase = "closing"
