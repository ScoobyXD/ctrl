# Local perception verification

Verified **19 September 2026, 17:59 PDT** (`2026-09-20T00:59:24Z` capture). Result: **PASS for the CPU dependency stage**: genuine Windows ML Python projection/catalog activation, pinned detector acquisition/export, and CUA screenshot-to-boxes parity with PyTorch. GPU/NPU acceleration, OCR, JEV and input actions were not exercised.

Base: verified CUA commit `a57d115a10f7effa2b9e5a59d07fe4a37e953918`. Worktree: `C:/Users/Jonat/.codex/worktrees/e28b/[CTRL] Loop`. See [setup](LOCAL_PERCEPTION.md) for reproduction and complete pins.

## Host and installed dependencies

- Windows 11 Pro x64 25H2, build **26200.9457**.
- HP EliteBook X G1a; AMD Ryzen AI 9 HX PRO 375, 12 cores/24 logical processors, 64 GiB installed RAM.
- Radeon 890M driver `32.0.13066.12`; AMD NPU driver `32.0.203.314`. Neither was modified.
- Existing unpackaged CPython **3.12.13 x64**, uv **0.11.26**, VC++ x64 runtime **14.44.35211.00**.
- Windows ML wasdk projection/bootstrap **2.3.0**, matched `onnxruntime-windowsml` **1.25.2.202605110140** (engine **1.25.2**, commit `94bc0cd2b1`). No ordinary ORT distribution is present.
- 89 runtime-environment packages and 41 exporter-environment packages synchronized from checked-in hash locks; both dependency consistency checks passed.
- Initial project-local package installation, model acquisition, ONNX export and a complete repeat of `install-local-perception.ps1` passed. Repeat setup reverified cached CUA/model artifacts and installed package consistency.

## Actual Windows ML integration

The bootstrap reports release `2.3`, minimum `2.3.0.0`, and loads the existing `Microsoft.WindowsAppRuntime.2_2.5.1.0_x64__8wekyb3d8bbwe` runtime. Loaded-module enumeration confirms its `Microsoft.Windows.AI.MachineLearning.dll`, file version **2.1.74.48216**, plus the Python WinRT projection and bootstrap extensions. `ExecutionProviderCatalog.get_default()` succeeds. The saved bootstrap, CPU and fixture diagnostics advertise **MIGraphX, VitisAI and WebGPU**, each with state **`NOT_PRESENT`** and an empty library path. None was installed/readied, and no provider download was requested. Catalog advertisement does not establish provider execution or acceleration.

The `onnxruntime` module is owned by the exact **onnxruntime-windowsml** distribution in `.runtime/winml-venv`. Its actual `onnxruntime_pybind11_state.pyd` is loaded. CPU sessions report exactly `CPUExecutionProvider`; fallback is disabled. The exported detector returns finite FP32 output `[1,5,33600]`.

The wheel emits **`Init provider bridge failed`**. Investigation of [the exact ORT initialization source](https://github.com/microsoft/onnxruntime/blob/94bc0cd2b1/onnxruntime/python/onnxruntime_pybind_module.cc#L34) and [shared-provider loader](https://github.com/microsoft/onnxruntime/blob/94bc0cd2b1/onnxruntime/core/session/provider_bridge_ort.cc) traced this to an attempted load of `onnxruntime_providers_shared.dll`, absent from this published wheel. Initialization continues successfully. The same warning occurs when importing the wheel without the Windows ML bootstrap, so changing bootstrap order does not resolve it.

CPU inference is compiled into the Python extension ([exact linkage source](https://github.com/microsoft/onnxruntime/blob/94bc0cd2b1/cmake/onnxruntime_python.cmake#L210)); its inspected PE imports do not depend on `onnxruntime.dll`. This explains why the installed App Runtime's separate ORT 1.24 DLL is not the Python CPU engine. The result is the **documented Windows ML Python projection/catalog plus its matched Windows ML ORT wheel**, not a claim of CPU execution through that shared DLL. No DLL was deleted/copied and no alternate runtime package was substituted. The warning remains in local logs; shared/vendor-provider operation remains unverified.

## Checkpoint and export evidence

- Microsoft OmniParser v2 detector revision **`6600256cb0f1b07651e3bc86166196307bad7e2d`**.
- Checkpoint SHA-256 **`dab3d4351ad00b035db829909a4db98354d5a90f6990e4ac00222a9a95d4bf57`**, 40,623,819 bytes.
- Actual checkpoint: **YOLO11m**, `yolo11m.yaml`, scale `m`, class `0: icon`; 20,053,779 unfused parameters (20,030,803 fused); saved using Ultralytics 8.3.28.
- Detector license: **AGPL-3.0**. The license text was independently size/hash-verified. This is not established as TipTour's original model.
- Pinned export: Ultralytics **8.3.70**, CPU PyTorch **2.6.0+cpu**, torchvision **0.21.0+cpu**, ONNX **1.17.0**; static FP32, batch 1, 1280 square, opset 17, no embedded NMS/simplification.
- Full ONNX checker and input/output schema checks pass. Two consecutive normalized exports match; the setup repeat also reproduces the same ONNX SHA-256: **`8b3c5b7ae2d86ed543937d9e3b63bf23498dfafe0dee0ef5bdea3e3945d91611`**, 80,881,318 bytes.

## Final owned-window test

| Evidence | Observed result |
|---|---|
| Capture ID | `b85fcb8f-80c0-422b-915d-f4af55cf1310` |
| Owned target | PID `32488`, HWND `1377364`, `CTRL Loop - Local UI detector fixture` |
| Original PNG | **922 × 625**, no post-capture resize |
| PNG SHA-256 | `e0aac6c2425bfb92d1909e2651d2c76e34cbfeef970837312acfac2c0624ec57` |
| CUA frame bounds | `{x:89,y:80,width:924,height:627}` |
| Native Win32 frame bounds | `{x:80,y:80,width:942,height:636}` |
| Geometry treatment | Image, CUA frame and native frame/client/control bounds retained separately; action transform unverified |
| Decoded detections | **10** at confidence `0.25`, NMS IoU `0.45`; confidence range **0.6146–0.9459** |
| PyTorch versus Windows ML raw output | Maximum coordinate error **0.0034485** input pixels; confidence error **0.000005067** |
| Raw tolerances | Coordinates: `rtol=1e-4, atol=0.02`; confidences: `rtol=1e-4, atol=2e-5` |
| Decoded comparison | Equal counts, maximum original-image box difference **0 pixels**, confidence difference **0.000001669** |
| Independent decoder | Same result against **Ultralytics 8.3.70 NMS**, separate from the local NumPy decoder |
| Repeat inference | Three additional runs match first output within `rtol=atol=1e-6` |
| Input actions / accessibility requests | **0 / 0** |
| Cleanup | CUA and fixture both stopped gracefully, exit **0**, no cleanup errors |

The tensor hash supplied to both engines is `38b57f51cf1e8bff4d49132907761759df8ca7b2bd8ce347ebeb9a11f74ad981`. Full records contain every box's coordinates, confidence, model identity and capture ID. Three focused geometry/error tests pass. An independent comparison also found preprocessing identical to pinned Ultralytics `LetterBox(auto=False)` for the fixture and three odd/portrait image dimensions.

Visual inspection of `fixture.png` and `boxes.png` confirms boxes around all five large buttons, the text field, combo box and close icon. The two decorative colored fiducials were also detected; the checkboxes and radio buttons were missed in this final capture. This demonstrates a functioning UI-trained detector with visible precision/recall limits, not a complete control inventory or proof of clickability. No OCR-derived text or roles are attached to the detections.

## Measured timings and limits

| Operation | Time |
|---|---:|
| CUA capture RPC | **272.60 ms** |
| Capture workflow including fixture/startup/cleanup | **4,306.57 ms** |
| Preprocessing | **30.03 ms** |
| Windows ML CPU session creation | **300.98 ms** |
| First detector inference | **974.29 ms** |
| Three warm inference runs | **942.88 / 909.69 / 917.00 ms** |
| Warm inference median | **917.00 ms** |
| Local decoding | **1.26 ms** |
| PyTorch reference subprocess, including checkpoint load | **15,245.96 ms** |

These are one controlled test's measurements on this laptop, with default CPU threading and concurrent desktop activity. They are not a frame-rate claim or an application-loop benchmark. The earlier fixture run had a 672.51 ms warm median; run-to-run variability is visible. GPU/NPU performance was not measured. The installed NPU driver `.314` falls outside Microsoft's currently documented VitisAI `.280`–`.297` range; no driver change was needed for CPU.

Ignored local evidence:

- `.runtime/installation-repeat.log`, `.runtime/perception-verification.log`.
- `.runtime/diagnostics/windowsml-bootstrap.json`, `windowsml-cpu.json`.
- `.runtime/ui-detector/model-inspection.json`, `export-manifest.json`, `export-reproducibility.json`.
- `.ctrl/perception/20260920T005921089Z/`: `observation.json`, `detector-result.json`, `fixture.png`, `boxes.png`, raw input/reference tensors, reference logs, exact CUA request log and process logs.

Only CUA initialization, tool discovery, PID-filtered window enumeration and exact-window capture were requested. All screenshots/tensors stayed local. No OCR, JEV, captioning pipeline, model API, input action, GPU/NPU provider download, driver upgrade, merge or push was part of this verification.
