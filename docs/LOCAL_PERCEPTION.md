# Windows ML and local UI detection

This stage takes an owned Windows window screenshot through CUA Driver 0.28.2, runs a UI-trained YOLO detector locally on CPU, and returns capture-scoped rectangles and confidence values in **original PNG pixels**. It also compares Windows ML's Python execution route against the original checkpoint using the identical input tensor. It performs no input actions, OCR, captioning, JEV calls, or GPU/NPU provider downloads.

## Reproduce on Windows x64

Prerequisites for the evaluated setup:

- Windows 11 in a logged-in interactive desktop session; tested on 25H2 build 26200.9457.
- PowerShell 7.2+ and built-in Windows PowerShell 5.1/WinForms for the fixture.
- **Unpackaged CPython 3.12.13 x64** and **uv 0.11.26**. Pass the base interpreter explicitly; the default PATH Python on this machine belongs to an unrelated application environment. Microsoft Store Python is not the supported bootstrap route.
- Windows App Runtime x64: this PC already has **2.5.1.0**, which satisfies the projection bootstrap's release `2.3`, minimum `2.3.0.0`. The installer reuses it. If missing, install the signed Microsoft Windows App Runtime through the [official downloads page](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/downloads), then rerun the probe. No App Runtime or GPU/NPU driver was installed/upgraded in this stage.
- Visual C++ x64 runtime: already installed here as `v14.44.35211.00`. Python dependencies are prebuilt wheels; Visual Studio, .NET SDK and CUDA are unnecessary for this route.

From the repository, with network access for first-time package/model acquisition:

```powershell
pwsh -NoProfile -File ./scripts/install-local-perception.ps1 -Python '<absolute path to Python 3.12.13 base/python.exe>'
pwsh -NoProfile -File ./scripts/test-local-perception.ps1
```

The setup reuses the merged, hash-verifying CUA installer. It creates `.runtime/winml-venv` and `.runtime/exporter-venv`, synchronizes exact package versions and artifact hashes from the checked-in locks, checks dependency consistency, verifies the Windows ML bootstrap, acquires only the detector checkpoint/license, exports ONNX, and runs a CPU model smoke check. It installs no packages globally and makes no PATH, scheduled task, registry or driver changes.

Run capture tests in the interactive desktop, not an isolated sandbox desktop. The test launches its own nonactivating native-control fixture, captures only its verified PID/HWND, closes it and the private CUA process, and then performs inference. There are no mouse/keyboard tools in this workflow. The fixture has a 90-second self-close fallback.

The default test writes PNG, overlay, observation JSON, raw tensors, runtime diagnostics, numerical comparison, box records, timings and process logs to `.ctrl/perception/<UTC timestamp>/`. These files and `.runtime/` are ignored. Evidence paths are local to the checkout that produced them.

## Runtime binding and version boundary

The application environment uses the [Microsoft Learn documented Python route](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/distributing-your-app):

| Distribution | Exact version |
|---|---|
| `wasdk-Microsoft.Windows.AI.MachineLearning[all]` | `2.3.0` |
| `wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap` | `2.3.0` |
| `onnxruntime-windowsml` | `1.25.2.202605110140` |
| `numpy` | `1.26.4` |
| `opencv-python-headless` | `4.10.0.84` |
| `Pillow` | `11.1.0` |

The ML projection declares that exact ONNX Runtime wheel dependency. It is not interchangeable with the newest standalone ORT wheel. Full transitive versions/hashes are in `dependencies/windowsml-requirements.lock`. The Python import is still `onnxruntime`; the runtime helper verifies the owning distribution and refuses ordinary/conflicting ORT distributions.

The actual Windows ML interface is `winui3.microsoft.windows.ai.machinelearning.ExecutionProviderCatalog`. `winui3.microsoft.windows.applicationmodel.dynamicdependency.bootstrap.initialize()` activates Windows App Runtime and stays alive throughout inference. The helper records the real loaded Windows ML DLL, projection modules and ORT extension, not just installed package names. CPU sessions explicitly select `CPUExecutionProvider` and disable fallback.

The CPU engine is compiled into the Windows ML wheel's `onnxruntime_pybind11_state.pyd`; it is **not** the App Runtime's separate `onnxruntime.dll`. Therefore the latter's file version does not describe the Python CPU engine. This stage verifies the documented Windows ML projection/catalog plus its matched Windows ML-specific ORT wheel; it does not claim CPU inference executes through the shared App Runtime DLL. See the verification record for observed paths and the retained provider-bridge warning.

The wasdk projection and ORT wheel report MIT licenses. This setup does not use the newer `windowsml` ctypes binding (whose metadata carries an Alpha classifier) or a custom native bridge. Windows ML and ONNX Runtime are parts of this single execution route; no separate `onnxruntime`, `onnxruntime-gpu`, or `onnxruntime-directml` installation is added.

## Model provenance and export

| Item | Pin |
|---|---|
| Source | Microsoft `OmniParser-v2.0`, **detector only** |
| Repository revision | `6600256cb0f1b07651e3bc86166196307bad7e2d` |
| File | `icon_detect/model.pt`, 40,623,819 bytes |
| SHA-256 | `dab3d4351ad00b035db829909a4db98354d5a90f6990e4ac00222a9a95d4bf57` |
| Actual loaded architecture | **YOLO11m**, `yolo11m.yaml`, scale `m`, 20,053,779 unfused parameters |
| Classes | One: `0: icon` |
| Checkpoint's saved Ultralytics version | `8.3.28` |
| Detector/license file | **AGPL-3.0**, independently hash-verified |

The [model card](https://huggingface.co/microsoft/OmniParser-v2.0) and YAML contain stale YOLOv8 descriptions. Architecture above was read from the verified checkpoint. This artifact is a separately sourced UI-trained detector; it is **not proven to be TipTour's original checkpoint**, even though both identify YOLO11m. A repository-level MIT badge does not replace the [detector's AGPL-3.0 license](https://huggingface.co/microsoft/OmniParser-v2.0/blob/6600256cb0f1b07651e3bc86166196307bad7e2d/icon_detect/LICENSE). Export does not change that license. No captioning model or full OmniParser pipeline is installed.

`dependencies/ui-detector.lock.json` records the immutable source, sizes, hashes, license, tensor contract and export options. `scripts/export-ui-detector.py acquire` downloads to temporary files, verifies both size and SHA-256 before making them available, and rejects altered cached files. The checkpoint is hash-verified **before** Ultralytics deserialization.

The separate exporter environment pins `ultralytics==8.3.70`, `torch==2.6.0+cpu`, `torchvision==0.21.0+cpu`, `onnx==1.17.0`, `numpy==1.26.4`, `opencv-python==4.10.0.84` and `Pillow==11.1.0`; transitive hashes are locked. CPU PyTorch comes from its official wheel index. Ultralytics' pinned loader explicitly handles PyTorch 2.6's `weights_only` default. Automatic dependency installation and online checks are disabled; configuration and Matplotlib caches are local.

Export uses `format=onnx`, `imgsz=1280`, `batch=1`, `device=cpu`, `half=False`, `dynamic=False`, `simplify=False`, `opset=17`, `nms=False`. The graph is checked with `onnx.checker.check_model(full_check=True)`. It has IR version 8, input `images: float32[1,3,1280,1280]`, and output `output0: float32[1,5,33600]`. The exporter removes the volatile date metadata and records immutable provenance; two consecutive exports produced identical model bytes. Export metadata, actual model architecture and generated ONNX hash are written to ignored `export-manifest.json`. `simplify=False` avoids an additional ORT/onnxslim dependency.

## Coordinates, boxes and verification

Preprocessing uses RGB, aspect-preserving OpenCV linear resize, centered padding value 114, BCHW float32 and division by 255. The recorded letterbox gain/padding maps predicted coordinates back to original PNG pixels. The raw one-class head returns xywh plus confidence. Local NumPy NMS uses confidence `0.25`, IoU `0.45`, at most 300 detections, followed by inverse mapping/clipping. Every box carries its capture ID and model hashes.

The CUA PNG is not silently equated to its window frame. Original image dimensions, driver bounds, native frame/client/control geometry and acquisition interval are retained separately. The screenshot-to-window transform remains explicitly **unverified**. These detections are not action coordinates or proof of clickability. `icon` is the model class, not an inferred button role or recognized label.

Focused tests cover RGB/layout, padding/inverse mapping, NMS, clipping, invalid outputs, raw PyTorch/ONNX equivalence on the same tensor, decoded boxes and repeated CPU inference. A separate comparison uses the pinned Ultralytics NMS implementation as the decoder reference. The smoke fixture contains actual buttons, text input, combo box, checkboxes and radio buttons. Visual inspection is recorded separately from numerical equivalence: correct export does not establish detection recall or precision on arbitrary apps.

## Updating pins

Package updates are deliberate. Edit `.in` files, regenerate hashes with the evaluated uv tool, then rerun setup and verification. Do not resolve `latest` at application startup.

```powershell
uv --no-config --cache-dir .runtime/uv-cache pip compile dependencies/windowsml-requirements.in --python .runtime/winml-venv/Scripts/python.exe --generate-hashes --output-file dependencies/windowsml-requirements.lock
uv --no-config --cache-dir .runtime/uv-cache pip compile dependencies/exporter-requirements.in --python .runtime/exporter-venv/Scripts/python.exe --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match --generate-hashes --output-file dependencies/exporter-requirements.lock
```

Primary references: [Windows ML Python sample](https://github.com/microsoft/WindowsAppSDK-Samples/blob/main/Samples/WindowsML/python/SqueezeNetPython/winml.py), [PyWinRT bootstrap](https://pywinrt.readthedocs.io/en/latest/api/winui3/index.html), [projection dependency metadata](https://pypi.org/pypi/wasdk-Microsoft.Windows.AI.MachineLearning/2.3.0/json), [pinned Ultralytics exporter](https://github.com/ultralytics/ultralytics/blob/v8.3.70/ultralytics/engine/exporter.py), [PyTorch CPU versions](https://pytorch.org/get-started/previous-versions/), [Windows ML EP requirements](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers).
