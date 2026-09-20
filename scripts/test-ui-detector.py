"""Run a captured fixture through Windows ML CPU and the pinned PyTorch reference."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image, ImageDraw
from ctrl_loop.detector import decode, preprocess
from ctrl_loop.winml_runtime import WindowsMLRuntime


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.runs <= 20:
        parser.error("--runs must be between 1 and 20")
    observation = json.loads(args.observation.read_text(encoding="utf-8-sig"))
    run_dir = args.observation.resolve().parent
    report_path = run_dir / "detector-result.json"
    report = {"passed": False, "capture_id": observation["capture_id"], "input_actions": 0}
    try:
        if not observation["passed"]:
            raise ValueError("Capture verification did not pass")
        source = Path(observation["image"]["path"])
        if digest(source) != observation["image"]["sha256"]:
            raise ValueError("Capture PNG hash differs from observation")
        with Image.open(source) as loaded:
            image = loaded.convert("RGB")
        if image.size != (observation["image"]["width"], observation["image"]["height"]):
            raise ValueError("Capture dimensions differ from observation")
        lock_path = ROOT / "dependencies/ui-detector.lock.json"
        lock = json.loads(lock_path.read_text())
        model_dir = ROOT / ".runtime/ui-detector"
        manifest = json.loads((model_dir / "export-manifest.json").read_text())
        model_path = model_dir / "model.onnx"
        if manifest["lockfileSha256"] != digest(lock_path) or manifest["onnx"]["sha256"] != digest(model_path):
            raise ValueError("Export identity differs from model lock or ONNX bytes; re-export")
        if manifest["names"] != {"0": "icon"}:
            raise ValueError(f"Unexpected model classes: {manifest['names']}")
        started = time.perf_counter()
        tensor, transform = preprocess(np.asarray(image))
        preprocess_ms = (time.perf_counter() - started) * 1000
        input_path = run_dir / "input.f32"
        tensor.astype("<f4", copy=False).tofile(input_path)
        reference_path = run_dir / "pytorch-output.f32"
        command = [str(ROOT / ".runtime/exporter-venv/Scripts/python.exe"),
                   str(ROOT / "scripts/export-ui-detector.py"), "reference",
                   "--input-tensor", str(input_path), "--output", str(reference_path)]
        started = time.perf_counter()
        reference_process = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (run_dir / "reference.stdout.log").write_text(reference_process.stdout, encoding="utf-8")
        (run_dir / "reference.stderr.log").write_text(reference_process.stderr, encoding="utf-8")
        if reference_process.returncode:
            raise RuntimeError("PyTorch reference failed; inspect reference logs")
        reference_ms = (time.perf_counter() - started) * 1000
        reference_meta = json.loads(Path(str(reference_path) + ".json").read_text())
        if (reference_meta["inputSha256"] != digest(input_path)
                or reference_meta["checkpointSha256"] != manifest["checkpointSha256"]
                or reference_meta["outputSha256"] != digest(reference_path)):
            raise ValueError("Reference identity/hash mismatch")
        reference = np.fromfile(reference_path, dtype="<f4").reshape(reference_meta["outputShape"])
        with WindowsMLRuntime() as runtime:
            started = time.perf_counter()
            session = runtime.cpu_session(model_path)
            session_ms = (time.perf_counter() - started) * 1000
            try:
                if len(session.get_inputs()) != 1 or session.get_inputs()[0].shape != [1, 3, 1280, 1280]:
                    raise ValueError("Unexpected model input contract")
                started = time.perf_counter()
                output = session.run(None, {"images": tensor})[0]
                first_run_ms = (time.perf_counter() - started) * 1000
                measured_ms = []
                for _ in range(args.runs):
                    started = time.perf_counter()
                    repeated = session.run(None, {"images": tensor})[0]
                    measured_ms.append((time.perf_counter() - started) * 1000)
                    np.testing.assert_allclose(output, repeated, rtol=1e-6, atol=1e-6)
                report["runtime"] = runtime.diagnostics()
                report["session_providers"] = session.get_providers()
            finally:
                del session
        if output.shape != reference.shape or not np.isfinite(output).all():
            raise ValueError("Unexpected or non-finite ONNX output")
        difference = np.abs(output - reference)
        # FP32 fusion/backend differences: coordinates in input pixels, probabilities separately.
        coordinates_match = bool(np.allclose(output[:, :4], reference[:, :4], rtol=1e-4, atol=0.02))
        scores_match = bool(np.allclose(output[:, 4:], reference[:, 4:], rtol=1e-4, atol=2e-5))
        report["numerical_comparison"] = {
            "same_input_sha256": digest(input_path), "output_shape": list(output.shape),
            "coordinate_max_abs_error": float(difference[:, :4].max()),
            "score_max_abs_error": float(difference[:, 4:].max()),
            "coordinate_tolerance": {"rtol": 1e-4, "atol": 0.02},
            "score_tolerance": {"rtol": 1e-4, "atol": 2e-5},
            "passed": coordinates_match and scores_match,
        }
        if not coordinates_match or not scores_match:
            raise ValueError("PyTorch/Windows ML raw outputs differ beyond declared FP32 tolerances")
        started = time.perf_counter()
        detections = decode(output, transform)
        decode_ms = (time.perf_counter() - started) * 1000
        reference_boxes = decode(reference, transform)
        if not detections or len(detections) != len(reference_boxes):
            raise ValueError("Fixture must produce nonempty and equal decoded box counts")
        # Greedy one-to-one matching by confidence-ordered box position; count alone is insufficient.
        unmatched = list(reference_boxes)
        box_errors, score_errors = [], []
        for detection in detections:
            errors = [max(abs(np.array(detection["box_xyxy"]) - other["box_xyxy"])) for other in unmatched]
            index = int(np.argmin(errors))
            other = unmatched.pop(index)
            box_errors.append(float(errors[index]))
            score_errors.append(abs(detection["confidence"] - other["confidence"]))
        if max(box_errors) > 0.1 or max(score_errors) > 2e-5:
            raise ValueError("Decoded boxes/confidences differ beyond declared tolerances")
        official = reference_meta["officialNms"]
        official_boxes = official["boxes"]
        if len(official_boxes) != len(detections):
            raise ValueError("Independent Ultralytics NMS box count differs")
        unmatched_official = []
        for item in official_boxes:
            box = np.array(item["box_xyxy"], dtype=np.float64)
            box[[0, 2]] = np.clip((box[[0, 2]] - transform.pad_left) / transform.gain, 0, transform.image_width)
            box[[1, 3]] = np.clip((box[[1, 3]] - transform.pad_top) / transform.gain, 0, transform.image_height)
            unmatched_official.append({**item, "box_xyxy": box})
        official_errors, official_score_errors = [], []
        for detection in detections:
            errors = [max(abs(np.array(detection["box_xyxy"]) - other["box_xyxy"])) for other in unmatched_official]
            index = int(np.argmin(errors))
            other = unmatched_official.pop(index)
            if detection["class_id"] != other["class_id"]:
                raise ValueError("Independent decoder class differs")
            official_errors.append(float(errors[index]))
            official_score_errors.append(abs(detection["confidence"] - other["confidence"]))
        if max(official_errors) > 0.1 or max(official_score_errors) > 2e-5:
            raise ValueError("Independent Ultralytics decoder differs beyond declared tolerances")
        report["official_decoder_comparison"] = {
            "count": len(official_boxes), "max_box_error_pixels": max(official_errors),
            "max_confidence_error": max(official_score_errors), "passed": True,
            "reference": "Ultralytics 8.3.70 non_max_suppression on PyTorch output",
        }
        identity = {"id": lock["id"], "source_revision": lock["source"]["revision"],
                    "checkpoint_sha256": manifest["checkpointSha256"], "onnx_sha256": manifest["onnx"]["sha256"],
                    "license": lock["source"]["license"]}
        for index, detection in enumerate(detections, start=1):
            detection.update(capture_id=observation["capture_id"], id=f"{observation['capture_id']}:d{index}",
                             coordinate_space="original_screenshot_pixels", model=identity)
        overlay = image.copy()
        drawing = ImageDraw.Draw(overlay)
        for index, detection in enumerate(detections, start=1):
            box = detection["box_xyxy"]
            drawing.rectangle(box, outline="#d52234", width=2)
            drawing.text((box[0] + 2, max(0, box[1] - 13)), f"{index}: {detection['confidence']:.2f}", fill="#ad0014")
        overlay.save(run_dir / "boxes.png")
        report.update(passed=True, observation_path=str(args.observation.resolve()), model=identity,
                      image=observation["image"], letterbox=transform.as_dict(), detections=detections,
                      coordinate_note="Boxes refer only to original PNG pixels; screenshot-to-window transform remains unverified.",
                      thresholds={"confidence": 0.25, "iou": 0.45, "max_detections": 300},
                      decoded_comparison={"count": len(detections), "max_box_error_pixels": max(box_errors),
                                          "max_confidence_error": max(score_errors)},
                      timings_ms={"preprocessing": preprocess_ms, "session_creation": session_ms,
                                  "first_inference": first_run_ms, "warm_inference_runs": measured_ms,
                                  "warm_inference_median": float(np.median(measured_ms)), "decoding": decode_ms,
                                  "reference_subprocess_including_load": reference_ms})
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Detector evidence: {report_path}")
    print(json.dumps({"passed": report["passed"], "detections": len(report["detections"]),
                      "timings_ms": report["timings_ms"], "numerical_comparison": report["numerical_comparison"]}, indent=2))


if __name__ == "__main__":
    main()
