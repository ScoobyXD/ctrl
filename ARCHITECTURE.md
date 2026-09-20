# Windows visual assistant architecture

## Separate UIA observation milestone

This branch adds a second, independent viewer for the observation and candidate-list stages only. A selected HWND/PID is read through Windows UI Automation COM (`comtypes` 1.4.13), including browser content exposed by its UIA provider. A disposable MTA helper builds per-element property caches, excludes password values, and returns serializable snapshot-scoped controls and capability-backed operation options. The PySide6 GUI provides manual refresh, filtering, details, cancellation and explicit JSON export. It has no action dispatcher, Jev integration, model inference, screenshot capture or automated target input.

The pinned CUA implementation was checked first. It exposes genuine UIA, but its unconditional value cache, inferred menu actions and missing serialized states do not satisfy this milestone. The dedicated reader is intentionally separate from the unchanged capture/CUA/YOLO viewer documented below. Native WinForms and ordinary Chrome page UIA were live verified. [Implementation, capability rules, measured evidence and limits](docs/UIA_VIEWER.md).

Decision record · 20 September 2026 · The primary-screen capture-and-YOLO desktop viewer is implemented and live verified. OCR, JEV integration into the GUI, and action delivery remain future scope.

## Current milestone: manual capture viewer

CTRL is a Windows PySide6 desktop app with one **Capture** button. Each deliberate click takes one screenshot of the whole primary monitor, runs the existing local UI-trained YOLO detector, and draws green outline boxes on the captured image inside CTRL. The image and boxes remain fixed until another click. No prompt field, OCR, English detection labels, probability panel, JEV/cloud call, automated input, repeated capture, or desktop overlay is part of this milestone.

CTRL briefly hides and allows 350 ms for desktop repaint before calling CUA's `get_desktop_state` once. It returns with the original image and Analyzing status, then matching green boxes. Capture is disabled while a job is active. Errors restore the window, close invalid capture connections, and allow only a subsequent deliberate retry. Closing cancels pending work and ignores late results while releasing the owned helper and detector.

```text
Capture click -> hide CTRL -> one repaint delay
  -> private CUA MCP helper -> primary-screen native PNG
  -> immutable CapturedFrame -> restore CTRL / show image
  -> local Windows ML CPU YOLO -> DetectionResult
  -> same-image green rectangles in the viewer
```

The Qt UI/controller, capture adapter and detector worker are separate. Capture and inference run off the UI thread. A persistent MCP connection and Windows ML CPU session are owned by one worker while the app is open. The session is released before the Windows ML runtime. Source pixels and rectangles remain separate; a shared aspect-fit drawing transform keeps boxes aligned while resizing and across display DPI. There is no zoom control in this first version.

Current records are `CapturedFrame` (unique ID, capture-return UTC time, dimensions and owned RGB pixels) and `DetectionResult` (the exact originating frame and original-image-pixel rectangles). The lifecycle accepts one job, rejects duplicate/stale events and requires the same frame object before applying results. A rectangle is only a detector proposal, not proof that the region is interactive.

[Capture viewer setup, architecture, troubleshooting and validation](docs/CAPTURE_VIEWER.md) contains the exact launcher/setup commands, runtime layout and evidence. Two real primary-display captures at 2880 × 1800, CPU inference, resize alignment, fixed results between clicks, replacement on the second click, responsive UI and shutdown were checked on this host. Deterministic tests separately cover errors and lifecycle races. Earlier selected-window fixtures remain dependency evidence and do not stand in for primary-screen validation. Standalone installer packaging and clean-machine verification are not complete.

## Future assistant direction

Later stages may select an application window, read text locally, build English action descriptions, ask JEV to choose a validated action, deliver it through CUA, and observe its effect. Those stages are not invoked by the capture app. The existing decision-only CLI is retained as a separately verified dependency experiment.

The prior application was moved into a recovery backup; Git history and dependency/provenance documentation are preserved. The following component decisions and proposed assistant contracts retain useful design work for later milestones. They do not describe features present in the current viewer.

## Component decisions

**CUA Driver handles current primary-screen capture and future action delivery.** This is the user's selected Windows driver. Wrap it behind our own small adapter so the rest of the application uses explicit observation and action records. Current CUA contracts bind window operations to a PID and window ID, distinguish window-local coordinates from desktop coordinates, and return action evidence. Release 0.28.2 is pinned. The viewer uses primary-display capture without a selected window or accessibility-tree walk; advertised future background input delivery is conditional. [CUA interface contracts](https://cua.ai/docs/reference/cua-driver/contracts), [application integration surfaces](https://github.com/trycua/cua/blob/main/libs/cua-driver/README.md).

**A locally executed, UI-trained YOLO detector finds boxes.** The evaluated checkpoint is Microsoft's OmniParser v2 `icon_detect/model.pt`, pinned by immutable revision and SHA-256. Loading it confirms **YOLO11m** with one `icon` class despite stale YOLOv8 sidecar metadata. The detector weights are **AGPL-3.0**. A pinned CPU FP32 ONNX export and a read-only CUA fixture comparison are verified; small controls can be missed and decorative regions can be detected. This is separately sourced UI-trained detection, not verified original TipTour weights. TipTour's bundled artifact remains CoreML. [Model provenance and setup](docs/LOCAL_PERCEPTION.md), [measured verification](docs/LOCAL_PERCEPTION_VERIFICATION.md), [TipTour model specification](https://github.com/milind-soni/tiptour-macos/blob/52582467c883d66484542f3be8e259340eb524f1/TipTour/UIElementDetector.mlpackage/Data/com.apple.CoreML/model.mlmodel).

**Windows ML is the selected Windows inference direction.** The verified Python route uses the `wasdk` WinRT projection/bootstrap and its pinned `onnxruntime-windowsml` wheel. Windows ML catalog activation is checked independently of CPU model execution. The Python CPU engine is embedded in that wheel's native extension; it does not execute through the installed App Runtime's separate `onnxruntime.dll`. ONNX is the portable model representation. CPU output and box equivalence against PyTorch passed for the owned fixture. Optional GPU/NPU execution and application packaging remain unverified; a shared-provider warning is documented. [Binding and version boundary](docs/LOCAL_PERCEPTION.md), [Microsoft Windows ML overview](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview).

**Future OCR is a separate local component whose backend is not selected.** It returns text, bounds, and recognition confidence. Candidate-building code associates text with detector boxes using containment, overlap, distance, and reading order. It removes duplicates without merging different controls that happen to share a label. Unlabeled icons remain explicitly unlabeled unless evidence supports a name; we do not require a new captioning model or the full OmniParser pipeline.

**JEV is the future online chooser, absent from the capture app.** The implemented decision-only adapter uses OpenRouter's `/api/alpha/decisions`, requesting `typesafe/jev-1.13` with TypeSafe as the only provider and fallbacks disabled. A live Choice/two-Noul fixture request returned `typesafe/jev-1.13-20260917` from `TypeSafe`; the adapter requires that exact resolved model/provider. The existing AWS Secrets Manager credential is retrieved into process memory separately from the decision adapter. English task and candidate descriptions are its input; screenshots and geometry stay local. The verified fixture uses manually annotated labels on archived detector boxes, because OCR is absent. Local dispatch IDs and a payload fingerprint bind results to the originating request/capture, without proving screen freshness or grounding. No input actions run. [Setup and contracts](docs/JEV.md), [measured verification](docs/JEV_VERIFICATION.md), [OpenRouter JEV model](https://openrouter.ai/typesafe/jev-1.13), [TypeSafe primitives](https://docs.typesafe.ai/introduction).

## Proposed future assistant contracts

These are proposed application contracts, not literal CUA or provider request schemas.

| Record | Required meaning |
|---|---|
| `Observation` | Unique capture ID; acquisition time; exact window/PID identity; image dimensions; coordinate origin and transform; window geometry; local screenshot reference |
| `Detection` | Capture ID; detector box in image pixels; model/version; class if meaningful; detection confidence |
| `TextRegion` | Capture ID; text; image-pixel bounds; OCR backend/version; recognition confidence |
| `Candidate` | Capture-scoped ID; evidence sources; label/description; box; supported action alternatives; uncertainty; link to the original observation |
| `Decision` | Request/capture IDs; selected action ID; full Choice distribution and confidence; Noul results; provider/model; timing and errors |
| `ActionResult` | Exact attempted action and target; delivery outcome; driver evidence; fresh observation ID; separately assessed task effect |

Candidate IDs refer to one observation. A matching label on a later screen does not make an old ID valid. Preserve image-to-window coordinate transforms explicitly, including any resizing, cropping, and display scaling. The CUA adapter converts those coordinates exactly once into the coordinate system required by the pinned driver. Never mix screenshot pixels and desktop coordinates implicitly.

An action option combines a candidate with an allowed operation and bounded arguments. A rectangle is not proof that a region is clickable or editable. Initially expose only operations supported by the observation and tested adapter. Text-entry content must come from the user's task or another explicit local input; JEV is not asked to invent arbitrary text or programs.

Illustrative internal decision input:

```json
{
  "capture_id": "capture-0042",
  "task": "Open the application's settings",
  "observed_state": "Main window. A Settings label is visible near the top right.",
  "actions": {
    "capture-0042:c7:click": "Click the visible control labeled Settings near the top right.",
    "reobserve": "Take a fresh observation before choosing an input action.",
    "no_target": "No currently supported visible action advances the task."
  }
}
```

Coordinates and executable arguments stay in the local action registry. JEV returns an option ID; the application resolves it. The actual provider adapter serializes this state into that provider's documented envelope.

## Future JEV question semantics

Use **Choice** to select among the defined action alternatives. Preserve every option's probability and the reported confidence for inspection. These are separate from YOLO detection confidence and OCR recognition confidence. None alone proves that an action is safe, correctly grounded, or successful. [Choice contract](https://docs.typesafe.ai/primitives/choice).

Use separate **Noul** statements for “the user's requested result is already satisfied” and “no viable supported target is visible.” Noul returns a 0–1 yes-probability. Phrase each statement against observable evidence. Questions evaluated together do not see one another's answers; local code resolves contradictions and insufficient evidence. Thresholds and ambiguity rules remain evaluation decisions, not copied constants from TipTour. [Noul contract](https://docs.typesafe.ai/primitives/noul).

**Score is optional.** It expresses an ordered degree, such as progress against explicitly described levels. It is not needed to choose an action and is not a substitute for Choice probabilities or completion evidence. [Score contract](https://docs.typesafe.ai/primitives/score).

## Future action execution and verification

The controller validates response types, finite probability values, known option IDs, and correspondence to the outstanding capture. It checks that the selected operation and arguments exist in the local registry. Unknown, malformed, ambiguous, or stale decisions cause no input.

Immediately before input, recheck the target window and relevant region. If identity, geometry, label, or other required grounding changed, invalidate the decision and reobserve. Freshness is more than elapsed time. Maintain one input action in flight and an immediately available stop control. Retry observation or model calls within explicit limits; never blindly repeat a potentially completed input after an uncertain delivery result.

After input, obtain a fresh observation. Record delivery separately from semantic success. A changed screenshot may indicate repainting, animation, or cursor movement; it does not establish that the requested operation worked. Verification needs an expected observable effect, such as the settings dialog appearing or a field containing the requested value. If evidence is inadequate, report uncertainty and reobserve or stop. A high completion Noul is a model judgment, not independent proof.

## Data boundary and implementation stages

The current viewer keeps screenshots, detector tensors and geometry local and makes no cloud calls. In the future assistant design, screenshots, crops, OCR processing, coordinate maps, and input execution also stay local. JEV receives task text, selected labels/descriptions, relevant textual state, and limited action history. These descriptions can still contain sensitive screen content. The request preview should show what will leave the PC. Keep credentials out of logs and source; choose diagnostic retention explicitly. Verify the selected CUA release's telemetry configuration rather than assuming every dependency is network-silent.

1. **Manual capture viewer — implemented and live verified:** primary-monitor screenshot, fixed image with green YOLO boxes, one-job lifecycle and simple launcher are available. Earlier exact-window fixture evidence is retained. General window-selection UI and action-coordinate transforms remain future work.
2. **Local perception — CPU detector verified:** pinned UI-trained YOLO11m produces original-image boxes with CPU correctness/timing evidence. OCR, readable labels, text-to-box association, and acceleration remain unverified.
3. **JEV preview — decision-only route verified:** the CLI shows outgoing English descriptions, full Choice distributions, and two Noul judgments over manually labeled archived fixture candidates. Runtime AWS authentication, OpenRouter/TypeSafe routing, and response validation passed; no input ran. See [setup](docs/JEV.md) and [verification](docs/JEV_VERIFICATION.md).
4. **Single action — future work:** enable one validated action followed by fresh observation and explicit effect assessment, after grounding, coordinate transforms, and target rechecks are established.
5. **Bounded loop — future work:** add step/time/retry limits, stop behavior, stale-target handling, and repeated-no-progress detection.

The local perception implementation uses Python with Windows ML's documented projection and an isolated exporter environment. The current manual capture controller and PySide6 viewer are implemented; an assistant/action-loop controller remains future work. Detector, Python runtime packages and CUA versions are pinned; installed Windows App Runtime and hardware-driver versions are recorded. The OpenRouter/JEV decision route is implemented and verified with an exact resolved-model check. OCR, real-screen candidate grounding, action thresholds, target rechecks, and input/effect verification still need implementation and evaluation. This architecture commits to component responsibilities and data flow, not unmeasured performance or a completed assistant loop.
