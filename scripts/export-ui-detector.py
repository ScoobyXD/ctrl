#!/usr/bin/env python3
"""Acquire and export the pinned UI detector; never install inference packages.

Run with .runtime/exporter-venv/Scripts/python.exe. Commands are deliberately
separate: acquire is the only network operation; inspect/export/reference use
only a verified local checkpoint. Reference input/output files are raw,
little-endian float32 tensors, with output shape recorded in <output>.json.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "dependencies" / "ui-detector.lock.json"
DEFAULT_MODEL_DIR = ROOT / ".runtime" / "ui-detector"


def read_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_artifact(path: Path, artifact: dict) -> None:
    if not path.is_file():
        raise ValueError(f"Missing {path}. Run the explicit acquire command first.")
    if path.stat().st_size != artifact["sizeBytes"] or sha256(path) != artifact["sha256"]:
        raise ValueError(f"Artifact verification failed for {path}; refusing to load it.")


def acquire(model_dir: Path, lock: dict) -> dict:
    """Download exact source artifacts to temporary files, verify, then publish."""
    model_dir.mkdir(parents=True, exist_ok=True)
    source = lock["source"]
    results = []
    for artifact in lock["artifacts"]:
        target = model_dir / artifact["localName"]
        if target.exists():
            verify_artifact(target, artifact)
            results.append({"path": str(target), "status": "already-verified"})
            continue
        url = (
            f"https://huggingface.co/{source['repository']}/resolve/"
            f"{source['revision']}/{artifact['sourcePath']}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "CTRL-Loop-detector-setup/1"})
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=model_dir, prefix="download-", suffix=".part", delete=False) as stream:
                temporary_path = Path(stream.name)
                with urllib.request.urlopen(request, timeout=120) as response:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > artifact["sizeBytes"]:
                            raise ValueError(f"Download exceeds pinned size: {artifact['sourcePath']}")
                        stream.write(chunk)
            verify_artifact(temporary_path, artifact)
            temporary_path.replace(target)
            results.append({"path": str(target), "status": "downloaded-and-verified"})
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return {"artifacts": results, "source": source}


def verified_model(model_dir: Path, lock: dict):
    # Set before importing Ultralytics: disable connectivity checks, telemetry
    # and automatic dependency installation. No generic ORT wheel is needed.
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_CONFIG_DIR"] = str(ROOT / ".runtime" / "exporter-config")
    matplotlib_config = ROOT / ".runtime" / "exporter-matplotlib"
    matplotlib_config.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(matplotlib_config)
    installed = {}
    for package, expected in lock["exporterVersions"].items():
        try:
            installed[package] = version(package)
        except PackageNotFoundError as error:
            raise ValueError(f"Missing exporter dependency {package}=={expected}. Run setup first.") from error
        if installed[package] != expected:
            raise ValueError(f"Expected {package}=={expected}, found {installed[package]}.")
    for artifact in lock["artifacts"]:
        verify_artifact(model_dir / artifact["localName"], artifact)
    checkpoint = model_dir / next(a["localName"] for a in lock["artifacts"] if a["role"] == "checkpoint")
    from ultralytics import YOLO
    from ultralytics import settings

    settings.update({"sync": False})
    model = YOLO(str(checkpoint.resolve()), task="detect")
    model.to("cpu")
    names = {str(key): value for key, value in model.names.items()}
    if len(names) != lock["tensorContract"]["expectedClassCount"]:
        raise ValueError(f"Unexpected checkpoint classes: {names}")
    module_types = Counter(type(module).__name__ for module in model.model.modules())
    yaml = model.model.yaml
    inspection = {
        "modelId": lock["id"],
        "lockfileSha256": sha256(LOCK_PATH),
        "source": lock["source"],
        "checkpointSha256": sha256(checkpoint),
        "architecture": {
            "class": type(model.model).__name__,
            "yamlFile": str(yaml.get("yaml_file", "")),
            "scale": yaml.get("scale"),
            "classCount": len(names),
            "parameterCount": sum(parameter.numel() for parameter in model.model.parameters()),
            "moduleTypes": dict(sorted(module_types.items())),
        },
        "names": names,
        "checkpointUltralyticsVersion": model.ckpt.get("version"),
        "exporterVersions": installed,
        "python": platform.python_version(),
    }
    write_json(model_dir / "model-inspection.json", inspection)
    return model, inspection


def export(model_dir: Path, lock: dict) -> dict:
    model, inspection = verified_model(model_dir, lock)
    output_path = Path(model.export(**lock["export"])).resolve()
    import onnx

    graph = onnx.load(str(output_path))
    # Ultralytics stamps the export time into the ONNX protobuf, changing its
    # hash on every run. Keep timestamps in the manifest, and retain immutable
    # model provenance inside the model so identical exports are reproducible.
    properties = {item.key: item.value for item in graph.metadata_props if item.key != "date"}
    properties.update({
        "source_repository": lock["source"]["repository"],
        "source_revision": lock["source"]["revision"],
        "source_checkpoint_sha256": inspection["checkpointSha256"],
        "source_license": lock["source"]["license"],
        "model_id": lock["id"],
    })
    onnx.helper.set_model_props(graph, dict(sorted(properties.items())))
    onnx.save(graph, str(output_path))
    onnx.checker.check_model(graph, full_check=True)
    inputs = [
        {"name": item.name, "shape": [dim.dim_value for dim in item.type.tensor_type.shape.dim],
         "elementType": item.type.tensor_type.elem_type}
        for item in graph.graph.input
    ]
    expected = lock["tensorContract"]
    if len(inputs) != 1 or inputs[0] != {
        "name": expected["inputName"], "shape": expected["inputShape"], "elementType": onnx.TensorProto.FLOAT
    }:
        raise ValueError(f"Unexpected exported input: {inputs}")
    standard_opsets = [entry.version for entry in graph.opset_import if entry.domain in ("", "ai.onnx")]
    if standard_opsets != [lock["export"]["opset"]]:
        raise ValueError(f"Unexpected exported ONNX opset: {standard_opsets}")
    if any(node.op_type == "NonMaxSuppression" for node in graph.graph.node):
        raise ValueError("Unexpected embedded NMS in exported graph")
    outputs = [
        {"name": item.name, "shape": [dim.dim_value for dim in item.type.tensor_type.shape.dim],
         "elementType": item.type.tensor_type.elem_type}
        for item in graph.graph.output
    ]
    if (
        len(outputs) != 1
        or outputs[0]["elementType"] != onnx.TensorProto.FLOAT
        or len(outputs[0]["shape"]) != 3
        or outputs[0]["shape"][:2] != [1, 4 + expected["expectedClassCount"]]
        or outputs[0]["shape"][2] <= 0
    ):
        raise ValueError(f"Unexpected exported raw detection output: {outputs}")
    metadata = {
        **inspection,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "export": lock["export"],
        "tensorContract": expected,
        "onnx": {
            "path": str(output_path), "sha256": sha256(output_path),
            "sizeBytes": output_path.stat().st_size, "irVersion": graph.ir_version,
            "volatileMetadataRemoved": ["date"],
            "inputs": inputs,
            "outputs": outputs,
        },
    }
    write_json(model_dir / "export-manifest.json", metadata)
    return metadata


def reference(model_dir: Path, lock: dict, input_path: Path, output_path: Path) -> dict:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Reference input and output must be different files.")
    model, inspection = verified_model(model_dir, lock)
    import numpy as np
    import torch
    from ultralytics.nn.modules import C2f, Detect
    from ultralytics.utils.ops import non_max_suppression

    shape = lock["tensorContract"]["inputShape"]
    expected_elements = int(np.prod(shape))
    if input_path.stat().st_size != expected_elements * 4:
        raise ValueError(f"Input must contain exactly {shape} little-endian float32 values.")
    array = np.fromfile(input_path, dtype="<f4").reshape(shape)
    if not np.isfinite(array).all() or array.min() < 0 or array.max() > 1:
        raise ValueError("Input must be finite, preprocessed RGB data in [0, 1].")
    tensor = torch.from_numpy(array)
    # Mirror the pinned exporter's CPU/fusion/head preparation. Use identical
    # tensors for this reference and Windows ML, avoiding .pt auto-letterboxing.
    reference_model = deepcopy(model.model).cpu().float().eval().fuse()
    for module in reference_model.modules():
        if isinstance(module, Detect):
            module.dynamic = False
            module.export = True
            module.format = "onnx"
        elif isinstance(module, C2f):
            module.forward = module.forward_split
    with torch.inference_mode():
        prediction = reference_model(tensor)
    if not isinstance(prediction, torch.Tensor):
        raise ValueError(f"Expected one raw prediction tensor, got {type(prediction).__name__}")
    values = prediction.detach().cpu().numpy().astype("<f4", copy=False)
    if not np.isfinite(values).all():
        raise ValueError("Checkpoint returned non-finite predictions")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    values.tofile(output_path)
    # Preserve an independent official decoder reference. NMS may modify its
    # input, so clone the prediction and leave the saved raw tensor unchanged.
    official_boxes = non_max_suppression(
        prediction.clone(), conf_thres=0.25, iou_thres=0.45,
        max_det=300, nc=lock["tensorContract"]["expectedClassCount"],
    )[0].detach().cpu().numpy()
    metadata = {
        "modelId": lock["id"], "checkpointSha256": inspection["checkpointSha256"],
        "inputPath": str(input_path.resolve()), "inputSha256": sha256(input_path),
        "inputShape": shape, "outputPath": str(output_path.resolve()),
        "outputSha256": sha256(output_path), "outputShape": list(values.shape),
        "dtype": "float32", "byteOrder": "little", "device": "cpu",
        "exporterVersions": inspection["exporterVersions"],
        "officialNms": {
            "implementation": "ultralytics.utils.ops.non_max_suppression",
            "ultralyticsVersion": inspection["exporterVersions"]["ultralytics"],
            "coordinateSpace": "letterboxed_input_pixels",
            "confidenceThreshold": 0.25,
            "iouThreshold": 0.45,
            "maxDetections": 300,
            "boxes": [
                {"box_xyxy": row[:4].tolist(), "confidence": float(row[4]), "class_id": int(row[5])}
                for row in official_boxes
            ],
        },
    }
    write_json(Path(str(output_path) + ".json"), metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "inspect", "export", "reference"))
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--input-tensor", type=Path, help="Reference input: raw little-endian float32 [1,3,1280,1280]")
    parser.add_argument("--output", type=Path, help="Reference output raw float32 file; shape is written to <output>.json")
    args = parser.parse_args()
    lock = read_lock()
    model_dir = args.model_dir.resolve()
    if args.command == "acquire":
        result = acquire(model_dir, lock)
    elif args.command == "inspect":
        _, result = verified_model(model_dir, lock)
    elif args.command == "export":
        result = export(model_dir, lock)
    else:
        if args.input_tensor is None or args.output is None:
            parser.error("reference requires --input-tensor and --output")
        result = reference(model_dir, lock, args.input_tensor, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Detector setup failed: {error}", file=sys.stderr)
        sys.exit(1)
