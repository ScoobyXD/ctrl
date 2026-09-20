"""One persistent, verified local ONNX CPU session for the capture viewer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from .detector import decode, preprocess
from .winml_runtime import WindowsMLRuntime

Box = tuple[float, float, float, float]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_model(root: Path) -> tuple[Path, dict[str, Any]]:
    lock_path = root / "dependencies/ui-detector.lock.json"
    model_path = root / ".runtime/ui-detector/model.onnx"
    manifest_path = model_path.with_name("export-manifest.json")
    if not model_path.is_file() or not manifest_path.is_file():
        raise RuntimeError("Local detector is missing. Run scripts/install-capture-viewer.ps1.")
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    checkpoint = next(item for item in lock["artifacts"] if item["role"] == "checkpoint")
    if (
        manifest["lockfileSha256"] != _digest(lock_path)
        or manifest["onnx"]["sha256"] != _digest(model_path)
        or manifest["onnx"]["sizeBytes"] != model_path.stat().st_size
        or manifest["checkpointSha256"] != checkpoint["sha256"]
        or manifest["modelId"] != lock["id"]
        or manifest["source"] != lock["source"]
        or manifest["export"] != lock["export"]
        or manifest["tensorContract"] != lock["tensorContract"]
        or manifest["names"] != {"0": "icon"}
    ):
        raise RuntimeError("Detector model/manifest does not match the pinned dependency and export.")
    # The stored onnx.path describes export provenance; never import/read from it.
    return model_path, manifest


class LocalDetector:
    """Keep Windows ML alive until the owned CPU session has been released."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._runtime: WindowsMLRuntime | None = None
        self._session: Any = None
        self._input_name = ""
        self._output_name = ""
        self._size = 0

    def __enter__(self) -> LocalDetector:
        if self._runtime is not None:
            raise RuntimeError("LocalDetector is already active.")
        model, manifest = verified_model(self.root)
        self._runtime = WindowsMLRuntime()
        try:
            self._runtime.__enter__()
            self._session = self._runtime.cpu_session(model)
            inputs, outputs = self._session.get_inputs(), self._session.get_outputs()
            contract = manifest["tensorContract"]
            if (len(inputs) != 1 or inputs[0].name != contract["inputName"]
                    or inputs[0].shape != contract["inputShape"] or inputs[0].type != "tensor(float)"
                    or len(outputs) != 1 or outputs[0].type != "tensor(float)"
                    or outputs[0].shape != manifest["onnx"]["outputs"][0]["shape"]
                    or outputs[0].shape[:2] != [1, 5]):
                raise RuntimeError("Loaded detector tensor contract differs from the verified export.")
            self._input_name, self._output_name = inputs[0].name, outputs[0].name
            self._size = contract["inputShape"][2]
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def detect(self, rgb: np.ndarray) -> tuple[Box, ...]:
        if self._session is None:
            raise RuntimeError("Enter LocalDetector before detection.")
        tensor, transform = preprocess(rgb, size=self._size)
        raw = self._session.run([self._output_name], {self._input_name: tensor})[0]
        return tuple(tuple(float(value) for value in detection["box_xyxy"])
                     for detection in decode(raw, transform))

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        # ORT's Python owner releases its native session before ML bootstrap exit.
        self._session = None
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            runtime.__exit__(exc_type, exc, traceback)
