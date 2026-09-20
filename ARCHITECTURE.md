# Windows visual assistant architecture

Decision record · 19 September 2026 · Fresh project baseline; application not yet implemented.

## What we are building

We are starting a fresh Windows-only application. It will look at a selected application window, find visual controls and read their text locally, describe the available actions in English, ask JEV which action fits the user's task, and carry out one validated action through CUA Driver. It then observes the result before deciding anything else.

The local application supplies the eyes and hands. JEV supplies a structured judgment over choices that our code has already defined. Our ordinary application code connects those parts, tracks the selected window, and decides whether an action can proceed. Drawing the detected boxes and displaying probabilities are interface work; they do not require another AI model.

The working codebase has been cleared for a fresh implementation. Previous application code, dependencies, build configuration, and research were moved into a recovery backup outside this project. Existing browser code, interface code, agent loops, and adapters are **not assumed reused**. The repository's Git history and configured remote are preserved. This document defines the new application; its integration and performance have not been validated.

The initial experience is a selected-window screenshot with numbered boxes, recognized labels, and a JEV decision preview. A transparent desktop overlay, voice interaction, and a general-purpose planning model are not prerequisites.

```text
Selected Windows window
        |
   CUA Driver capture
        |
        +--> local UI-trained YOLO --> control rectangles
        +--> local OCR ------------> text and text rectangles
                                      |
                         ordinary candidate-building code
                                      |
                         English descriptions inside JSON
                                      |
                              online JEV decision
                                      |
                     local validation and target recheck
                                      |
                             CUA Driver input
                                      |
                         fresh observation + verification
```

## Component decisions

**CUA Driver handles capture and action delivery.** This is the user's selected Windows driver. Wrap it behind our own small adapter so the rest of the application uses explicit observation and action records. Current CUA contracts bind window operations to a PID and window ID, distinguish window-local coordinates from desktop coordinates, and return action evidence. Pin the evaluated release before implementation; advertised background delivery is conditional. [CUA interface contracts](https://cua.ai/docs/reference/cua-driver/contracts), [application integration surfaces](https://github.com/trycua/cua/blob/main/libs/cua-driver/README.md).

**A locally executed, UI-trained YOLO detector finds boxes.** “UI-trained” describes the weights, not a requirement to train a new model on this PC. Existing suitable weights can be evaluated. The exact portable checkpoint remains unresolved. TipTour's bundled model metadata identifies YOLO11m, but its checked-in inference artifact is a CoreML package. We have not established equivalent original weights or a validated Windows export. A generic object-detection checkpoint is not an equivalent substitute. [TipTour model specification](https://github.com/milind-soni/tiptour-macos/blob/52582467c883d66484542f3be8e259340eb524f1/TipTour/UIElementDetector.mlpackage/Data/com.apple.CoreML/model.mlmodel), [Ultralytics export formats](https://docs.ultralytics.com/modes/export/).

**Windows ML is the selected Windows inference direction.** It is the closest role-equivalent to CoreML here, powered by ONNX Runtime and supporting optional execution providers. ONNX is the portable model representation, not the detector itself. Establish a correct CPU baseline, then evaluate compatible acceleration. Python/controller integration, the exact Windows ML binding or native bridge, and deployment packaging must be validated. Installing an ordinary Python ONNX Runtime wheel is not evidence that Windows ML integration is complete. No GPU/NPU use, frame rate, latency, or hardware capability is guaranteed. [Microsoft Windows ML overview](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview).

**OCR is a separate local component whose backend is not selected.** It returns text, bounds, and recognition confidence. Candidate-building code associates text with detector boxes using containment, overlap, distance, and reading order. It removes duplicates without merging different controls that happen to share a label. Unlabeled icons remain explicitly unlabeled unless evidence supports a name; we do not require a new captioning model or the full OmniParser pipeline.

**JEV is the online chooser.** English descriptions inside JSON are its input. Screenshots are interpreted locally. The previous project's OpenRouter route is a known integration option, not inherited implementation. Keep transport separate from decision semantics: OpenRouter and TypeSafe's direct API have different credentials and request envelopes. Validate the selected provider adapter before enabling actions. [OpenRouter JEV model](https://openrouter.ai/typesafe/jev-1.13), [TypeSafe primitives](https://docs.typesafe.ai/introduction).

## Internal contracts

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

## JEV question semantics

Use **Choice** to select among the defined action alternatives. Preserve every option's probability and the reported confidence for inspection. These are separate from YOLO detection confidence and OCR recognition confidence. None alone proves that an action is safe, correctly grounded, or successful. [Choice contract](https://docs.typesafe.ai/primitives/choice).

Use separate **Noul** statements for “the user's requested result is already satisfied” and “no viable supported target is visible.” Noul returns a 0–1 yes-probability. Phrase each statement against observable evidence. Questions evaluated together do not see one another's answers; local code resolves contradictions and insufficient evidence. Thresholds and ambiguity rules remain evaluation decisions, not copied constants from TipTour. [Noul contract](https://docs.typesafe.ai/primitives/noul).

**Score is optional.** It expresses an ordered degree, such as progress against explicitly described levels. It is not needed to choose an action and is not a substitute for Choice probabilities or completion evidence. [Score contract](https://docs.typesafe.ai/primitives/score).

## Execution and verification

The controller validates response types, finite probability values, known option IDs, and correspondence to the outstanding capture. It checks that the selected operation and arguments exist in the local registry. Unknown, malformed, ambiguous, or stale decisions cause no input.

Immediately before input, recheck the target window and relevant region. If identity, geometry, label, or other required grounding changed, invalidate the decision and reobserve. Freshness is more than elapsed time. Maintain one input action in flight and an immediately available stop control. Retry observation or model calls within explicit limits; never blindly repeat a potentially completed input after an uncertain delivery result.

After input, obtain a fresh observation. Record delivery separately from semantic success. A changed screenshot may indicate repainting, animation, or cursor movement; it does not establish that the requested operation worked. Verification needs an expected observable effect, such as the settings dialog appearing or a field containing the requested value. If evidence is inadequate, report uncertainty and reobserve or stop. A high completion Noul is a model judgment, not independent proof.

## Data boundary and implementation stages

Screenshots, crops, detector tensors, OCR processing, coordinate maps, and input execution stay local in this design. JEV receives task text, selected labels/descriptions, relevant textual state, and limited action history. These descriptions can still contain sensitive screen content. The request preview should show what will leave the PC. Keep credentials out of logs and source; choose diagnostic retention explicitly. Verify the selected CUA release's telemetry configuration rather than assuming every dependency is network-silent.

1. **Read-only capture:** select one window and show its screenshot; validate identity, capture, resizing, and coordinates.
2. **Local perception:** evaluate named UI-trained weights and an OCR backend; show boxes/labels; establish CPU correctness and timings before acceleration.
3. **JEV preview:** display the outgoing descriptions, choices, distributions, and Noul judgments without input.
4. **Single action:** enable one validated action followed by fresh observation and explicit effect assessment.
5. **Bounded loop:** add step/time/retry limits, stop behavior, stale-target handling, and repeated-no-progress detection.

The controller language is provisionally Python; the Windows ML integration boundary and UI framework remain undecided. Pin model provenance, runtime, driver, and provider versions as each stage is validated. This architecture commits to the component responsibilities and data flow, not unmeasured performance or an already working integration.
