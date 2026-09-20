# Capture viewer

CTRL's first desktop milestone is a local, manual screenshot viewer. Click **Capture** once to capture the whole primary monitor, including its visible desktop and windows. CTRL briefly hides, returns with the captured image and **Analyzing…**, then draws green outlines over detector proposals. The image and boxes remain fixed until the next click. Resize the window to fit the image; boxes follow exactly the same transform.

The app has one Capture button. It does not run OCR, assign English labels, show probabilities, call JEV or a cloud model, send input to other apps, draw a desktop overlay, or capture on a timer. No credentials are required. All-monitor stitching, window selection, zoom controls, and input actions are outside this milestone.

## Setup and launch

Use a normal interactive Windows x64 desktop and PowerShell 7.2+. The evaluated base interpreter is unpackaged **CPython 3.12.13 x64**, with **uv 0.11.26** available on PATH. Windows App Runtime and VC++ prerequisites remain those documented in [local perception setup](LOCAL_PERCEPTION.md). The installer verifies the selected interpreter, synchronizes exact hash-pinned wheels, checks imports and creates a real Windows ML CPU inference session.

From this worktree:

```powershell
pwsh -NoProfile -File ./scripts/install-capture-viewer.ps1 -Python '<path to CPython 3.12.13 base/python.exe>'
pwsh -NoProfile -File ./scripts/start-capture-viewer.ps1
```

When verified downloads and an exported ONNX model already exist in another CTRL checkout, explicitly reuse those artifacts to avoid downloading/exporting them again:

```powershell
pwsh -NoProfile -File ./scripts/install-capture-viewer.ps1 `
  -Python 'C:/Users/Jonat/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe' `
  -ReuseRuntimeFrom 'C:/Users/Jonat/ctrl'
pwsh -NoProfile -File ./scripts/start-capture-viewer.ps1
```

This host was set up with that reuse command in `C:/Users/Jonat/ctrl-worktrees/screen-capture-preview`, branch `feat/screen-capture-preview`, based on `origin/main` commit `7974163`. The regular checkout was read only. Only the verified CUA release archive, ONNX export, export manifest and model license were copied. Virtual environments and application code are never imported from that checkout. The manifest retains its original export path as provenance; runtime resolution always selects this worktree's ONNX file.

The launcher resolves its own directory, invokes `.runtime/viewer-venv/Scripts/python.exe -I`, and the entry wrapper adds only this worktree's `src`. It works independently of the shell's current directory and ignores external PYTHONPATH/user packages. Setup is explicit; launch does not install packages. The first startup loads the model before enabling Capture. The launcher returns after starting the app without an extra console window; you may close/minimize your launching terminal. Timestamped stdout/stderr logs are written under `.runtime/viewer/`. CTRL owns its helper independently of the launching shell.

## Components and data flow

```text
Capture click (Qt main thread)
  -> create capture ID, disable button, hide CTRL
  -> one 350 ms repaint delay
  -> CaptureWorker (dedicated QThread / one asyncio owner task)
       -> persistent DesktopCapture / private CUA MCP stdio helper
       -> get_desktop_state {} once -> primary native-size PNG -> RGB pixels
       -> CapturedFrame signal -> restore CTRL, show image, clear old boxes
       -> persistent LocalDetector / Windows ML CPU ONNX session
       -> existing preprocess -> session.run -> existing decode
       -> DetectionResult signal -> matching green boxes, enable Capture
```

| Component | Responsibility |
| --- | --- |
| `capture_app.py` | Simple PySide6 window, deliberate click, hide/repaint/restore, status, shutdown |
| `capture_state.py` | Immutable capture records, image-fit geometry, one-job and stale-result guards |
| `capture_viewer.py` | Original image plus separate green rectangles under one paint transform |
| `capture_worker.py` | Long-lived backend ownership, background capture/inference and queued Qt results |
| `desktop_capture.py` | Pinned CUA executable, maintained MCP SDK, exact one primary PNG request |
| `local_detector.py` | Pinned export validation, session lifetime, existing preprocessing and decoding |
| `detector.py`, `winml_runtime.py` | Existing evaluated detector geometry and Windows ML projection |

`CapturedFrame` carries a unique capture ID, UTC capture-return time, original RGB image, and dimensions. The timestamp is when capture returns; it is not a sensor exposure timestamp. Pixels are copied into owned contiguous storage and made read only. `DetectionResult` retains that exact frame and original-image-pixel `xyxy` rectangles. Invalid/out-of-bounds rectangles fail validation. Matching an ID alone cannot attach boxes to different pixels: both lifecycle and viewer require the exact original frame object.

Image and rectangles use a common aspect-preserving scale and centered offsets in Qt logical coordinates. Qt handles display DPI; the original CUA pixels remain unchanged. The green pen is cosmetic (constant width when the image scales). The app does not rasterize rectangles into the original screenshot. There are no numbered/English labels or confidence annotations.

## Capture lifecycle and failure behavior

There is no screenshot on startup. One click disables Capture before hiding CTRL and starts a single-shot 350 ms delay to allow desktop repaint. The worker then makes exactly one `get_desktop_state` call. CUA's pinned contract returns only the primary display at native resolution; the app makes no accessibility-tree request and does not start or escalate a CUA session. Which monitor is primary comes from Windows, independently of the monitor containing CTRL.

After acquisition, the main thread immediately replaces the image, clears old boxes, restores CTRL's prior window state and displays Analyzing. Inference runs off the Qt thread. Completed results apply only to the active capture. Extra clicks during either phase do not queue jobs. The displayed result stays fixed even if the actual desktop changes.

If capture fails, CTRL restores and keeps the previous image/result. If inference fails, it keeps the newly captured image without boxes. Errors appear in the status line; there is no automatic screenshot retry. A capture transport/tool/decode failure closes the helper. The next explicit click may reconnect and take one new screenshot. Missing/corrupt startup dependencies leave a visible error and disabled button; rerun setup and reopen.

Closing cancels the pending repaint timer and invalidates outstanding results. An awaited MCP call or idle queue is cancelled; native CPU inference already executing is allowed to finish before cleanup. The UI remains responsive with a closing status. Late results are ignored. The application never forcibly terminates its QThread. The detector releases its session before Windows ML exits. MCP enter, capture and exit run in one asyncio task, preserving the SDK's AnyIO context ownership.

CUA is an owned child, launched with `mcp --direct --no-overlay`; the pinned MCP SDK uses `CREATE_NO_WINDOW` and a Windows Job Object. Only the child's telemetry/update settings are disabled. SDK shutdown closes stdin, waits for graceful EOF exit, then terminates its process tree if necessary. It does not terminate unrelated helpers.

## Runtime layout and local data

- `.runtime/viewer-venv/`: fresh app environment, with PySide6 6.11.2, MCP 1.30.0 and unchanged evaluated Windows ML packages. All 122 distributions are pinned with hashes in `dependencies/capture-viewer-requirements.lock`.
- `.runtime/cua-driver/`: pinned CUA 0.28.2 release archive and extracted files; executable SHA-256 is checked at startup/reconnect.
- `.runtime/ui-detector/`: model.onnx, export-manifest.json, LICENSE. Startup checks model hash/size, lock hash, source/checkpoint provenance, export settings and tensor contract.
- `.runtime/diagnostics/viewer-windowsml.json`: setup probe evidence.
- `.runtime/viewer/cua-driver.stderr.log`: helper diagnostics; no application screenshot saving.
- `.ctrl/capture-viewer/<UTC run>/`: screenshots and JSON only from the explicit live verification command below, ignored by Git.

Normal captures and results live in memory and replace the previous capture. They are not saved by default or sent to a service. Installation downloads dependencies when needed; runtime capture/inference is local and uses no cloud credentials. See [CUA configuration](CUA_DRIVER.md) and [model provenance/license](LOCAL_PERCEPTION.md). The detector weights are AGPL-3.0, separate from this repository's MIT license.

## Verification

All **99 offline regression tests passed**, including the existing detector/JEV/credential suite and 40 new capture/detector/viewer tests. These use fake capture/detector services for lifecycle failures, actual Qt painting for geometry, and the actual MCP SDK with a private fake server for timeout/EOF cancellation checks:

```powershell
& ./.runtime/viewer-venv/Scripts/python.exe -I -m unittest discover -s tests -v
```

Explicit visible integration check (creates a local fixture, takes two real screenshots and saves local evidence):

```powershell
& ./.runtime/viewer-venv/Scripts/python.exe -I ./scripts/test-capture-viewer.py --live
```

Measured on this host, 20 September 2026 UTC (`.ctrl/capture-viewer/20260920T030309Z/verification.json`):

- Real visible PySide6 launch, real CUA primary capture and real Windows ML CPU YOLO inference passed.
- Windows primary display was 1920 × 1200 logical pixels at 150% DPI; both CUA images were **2880 × 1800 native pixels**.
- First deliberate Qt test click: **60 boxes, 1.750 s** including repaint/capture/inference; second: **59 boxes, 1.218 s**. These are two observations, not a benchmark guarantee.
- A visible fixture changed from magenta to cyan between clicks. The first result stayed unchanged until the second click; the second image, capture ID and boxes replaced it.
- Actual saved viewer renders were visually inspected at the normal size, 760 × 850 and 1280 × 620 logical window sizes. Green outlines remained attached to the screenshot. CTRL was absent from the captured desktop.
- A 20 ms UI heartbeat continued during both jobs (86 and 60 ticks); maximum observed gap across the run, including startup/rendering, was 0.313 s.
- The worker stopped and the test process exited normally. A separate process inspection confirmed no CUA/Python processes from this worktree remained after the smoke run. The simple PowerShell launcher was then verified to open the production CTRL window with its own worktree runtime and private helper. Error restoration, cancellation, stale-result rejection and precise painted geometry are separately covered by deterministic tests; they must not be confused with real OS capture failures.
- Fresh worktree environment install and repeated cached install passed. CPU probe returned finite `[1,5,33600]` output. The known `Init provider bridge failed` warning remains; explicit CPU execution succeeded.

The Codex computer-use Node runtime could not initialize because its sandbox still references the obsolete bracketed workspace name. The visible integration check therefore used Qt's test clicks and local rendered evidence. This tool limitation did not affect the project's native CUA helper. Earlier selected-window fixture documents remain dependency evidence; they do not substitute for this new primary-screen flow.

## Troubleshooting and limitations

Run on an unlocked interactive desktop. An isolated/sandboxed desktop can prevent CUA acquisition. After an error CTRL should be visible again; inspect the status and `.runtime/viewer/cua-driver.stderr.log`. Missing-model/hash/version errors require setup in this checkout, not pointing Python at another checkout's environment. A model already present with incompatible provenance is rejected rather than silently overwritten.

The 350 ms hide/repaint delay worked on this host. Other desktop composition/remote-session conditions, protected windows, unusual monitor changes and sleep/lock transitions are not comprehensively tested. Only primary-monitor capture and this host's 150% scaling were live verified. Unit rendering tests cover multiple aspect ratios. No all-monitor stitching, GPU/NPU acceleration, OCR, action grounding or input delivery is claimed.

Boxes are detector proposals. Some small controls are missed and some decorative/text regions are detected. They do not establish that a region is interactive. This is the existing pinned OmniParser UI detector, not a claim of original TipTour weights.

This milestone is runnable source with a simple PowerShell launcher, not a standalone installer. Clean-machine installation and the full fresh-download/export branch were not exercised in this worktree; verified-artifact reuse was. A future installer can package the PySide6 entry point and pinned runtime, explicitly provision Windows App Runtime/VC++ prerequisites, include CUA/model license notices, and verify installation/removal on a clean Windows machine. That packaging work remains future scope.

## Implementation references

The primary-display behavior and optional-session contract were checked against the pinned [CUA Windows MCP tools](https://raw.githubusercontent.com/trycua/cua/cua-driver-rs-v0.28.2/docs/content/docs/reference/cua-driver/mcp-tools-windows.mdx) and [CUA contracts](https://raw.githubusercontent.com/trycua/cua/cua-driver-rs-v0.28.2/docs/content/docs/reference/cua-driver/contracts.mdx). Transport uses the maintained [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk); threading follows [Qt's QThread ownership and signal model](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html).