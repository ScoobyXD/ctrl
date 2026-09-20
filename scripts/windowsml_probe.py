"""Report actual Windows ML bootstrap, distribution, and CPU inference evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.winml_runtime import WindowsMLRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="Local ONNX model to execute on CPU.")
    parser.add_argument("--input-npz", type=Path, help="NPZ whose array keys match model input names; otherwise use zeros for concrete shapes.")
    parser.add_argument("--output", type=Path, help="Optional JSON diagnostic output path.")
    parser.add_argument("--diagnostics-only", action="store_true", help="Bootstrap and create the Windows ML catalog without loading a model.")
    args = parser.parse_args()
    if args.diagnostics_only and args.model:
        parser.error("Choose --diagnostics-only or --model, not both.")
    if not args.diagnostics_only and args.model is None:
        parser.error("Provide --model or --diagnostics-only.")
    if args.input_npz and not args.model:
        parser.error("--input-npz requires --model.")

    runtime = WindowsMLRuntime()
    report = {}
    exit_code = 0
    try:
        with runtime:
            report = runtime.diagnostics()
            if args.model:
                import numpy as np

                session = runtime.cpu_session(args.model)
                try:
                    inputs = session.get_inputs()
                    if args.input_npz:
                        with np.load(args.input_npz, allow_pickle=False) as archive:
                            feeds = {name: archive[name] for name in archive.files}
                        if set(feeds) != {item.name for item in inputs}:
                            raise ValueError("NPZ keys must exactly match model input names.")
                    else:
                        dtype_map = {"tensor(float)": np.float32, "tensor(float16)": np.float16, "tensor(double)": np.float64, "tensor(int64)": np.int64, "tensor(int32)": np.int32, "tensor(uint8)": np.uint8, "tensor(bool)": np.bool_}
                        feeds = {}
                        for item in inputs:
                            if item.type not in dtype_map or any(not isinstance(dim, int) or dim <= 0 for dim in item.shape):
                                raise ValueError(f"Input {item.name} needs explicit --input-npz: {item.type} {item.shape}")
                            feeds[item.name] = np.zeros(item.shape, dtype=dtype_map[item.type])
                    started = time.perf_counter()
                    outputs = session.run(None, feeds)
                    elapsed = time.perf_counter() - started
                    summaries = []
                    for spec, value in zip(session.get_outputs(), outputs, strict=True):
                        array = np.asarray(value)
                        finite = bool(np.isfinite(array).all()) if np.issubdtype(array.dtype, np.number) else None
                        if finite is False:
                            raise ValueError(f"Output {spec.name} contains non-finite values.")
                        summaries.append({"name": spec.name, "shape": list(array.shape), "dtype": str(array.dtype), "all_finite": finite})
                    report.update(runtime.diagnostics())
                    report["cpu_inference"] = {
                        "model": str(args.model.resolve()),
                        "input_source": str(args.input_npz.resolve()) if args.input_npz else "synthetic zeros; runtime smoke only, not detector correctness",
                        "providers": session.get_providers(),
                        "inputs": [{"name": name, "shape": list(value.shape), "dtype": str(value.dtype)} for name, value in feeds.items()],
                        "outputs": summaries,
                        "first_run_seconds": elapsed,
                    }
                finally:
                    del session
            report["status"] = "ok"
            report["validation_scope"] = "Windows ML bootstrap and catalog only" if args.diagnostics_only else "Windows ML bootstrap, catalog, and CPU ONNX execution"
    except Exception as exc:
        exit_code = 1
        report.update(runtime.diagnostics())
        report.update(status="error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    text = json.dumps(report, indent=2, allow_nan=False)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
