# UIA + Jev + native CUA execution

The UIA viewer has an explicitly started **CUA execution** workflow. It first asks Jev to choose a relevant window for the goal from a fresh window inventory. It then observes that window, asks the existing Jev selector for one action, observes again to validate that control, delivers a supported operation through pinned CUA Driver 0.28.2, and collects a new UIA observation before considering another step. Merely opening the viewer, inspecting requests, or running a selection preview sends no target input.

## Start a run

Install the existing pinned dependencies in this checkout if they are absent:

```powershell
pwsh -NoProfile -File ./scripts/install-uia-viewer.ps1 -Python '<CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/install-jev.ps1 -Python '<CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/install-cua-driver.ps1
pwsh -NoProfile -File ./scripts/start-uia-viewer.ps1
```

Enter a goal, open **CUA execution**, choose the action/time bounds, and click **Start execution**. The goal is shared with the preview tab. The manual Window picker controls observation and preview; execution chooses its own target and does not require a picker selection. For example, an X goal can select the browser even when the picker currently shows an unrelated editor. Execution always takes its own initial observation; saved JSON and preview results cannot be executed. The selector uses the existing AWS Secrets Manager/OpenRouter setup and its existing classification/reduction budgets. Each new step has fresh candidate IDs and the recent delivery history. Explicit argument values stay local; observed non-password values may appear in later Jev selection evidence under the existing text projection.

Window selection makes at most one structured model request against at most 64 current visible, titled top-level windows, under a 24,000-byte budget for the complete request. It sends the goal, observed titles and temporary candidate IDs; native HWND/PID identities stay local. It does not silently truncate the window list or choose the first alphabetical entry. The existing native reader caps each observed title at 512 characters. The model can select an observed candidate, report no match, or report ambiguity. No match stops with `no_window`; ambiguity stops with `ambiguous_window`; budget exhaustion stops before control selection or input. Window titles are untrusted evidence, never instructions. A title-only inventory cannot establish the contents of inactive browser tabs or distinguish windows whose titles provide insufficient context; clarify the goal or make the relevant page identifiable before starting another run.

The returned candidate must belong to the current inventory and goal. Before reading any controls, the helper lists windows again and requires the selected HWND, PID and title to still match; the initial UIA observation checks the title again. Disappearance, identity reuse or a changed title stops with `stale_target`. Later titles may change as navigation proceeds. It does not switch to another window or retry selection automatically. This stage shares the run's cancellation and time bounds; the existing control freshness and native input checks still apply afterward.

**Stop execution** requests cancellation, then terminates an unresponsive helper. Changing the goal or closing the viewer also stops it. The manual window picker is disabled during execution; programmatic picker changes neither cancel nor rebind the run. Windows job objects contain observer and driver child lifetimes. Cancellation cannot undo an input already dispatched: review the action receipt and target before starting again. Each run is bounded to 1–20 actions and 5–1200 seconds; provider requests already sent may still complete remotely. One action is in flight at a time.

The action log and result JSON distinguish delivery, failure, uncertainty and observed changes. `input_actions` counts positive native delivery receipts; `attempted_actions` also includes uncertain dispatches. Neither a Jev probability, a positive receipt, nor a changed UIA tree proves the user's goal is complete. `goal_completed` remains false. No-match, no-progress, repeated state/action, argument requirements, target changes, failures and limits stop the loop. Unknown deliveries are never automatically retried.

## Explicit arguments

If the selected operation needs text, a number or a direction, execution stops with `needs_arguments` unless exactly one user-supplied binding identifies the current control. Inspect its name/AutomationId in the selected observation, enter bindings in the execution tab, then deliberately start a fresh run:

```json
{
  "bindings": [
    {"operation": "set_value", "automation_id": "note", "value": "Synthetic note"},
    {"operation": "set_range_value", "name": "Zoom", "value": 125},
    {"operation": "scroll_vertical", "name": "Results", "direction": "down"}
  ]
}
```

A binding needs an exact `name` or `automation_id`, and can add `role`; all supplied fields must match exactly one control in the fresh observation. No text is parsed from a goal or invented by the selector. Empty text is a valid explicit replacement. Numbers must be finite and within the current provider's minimum/maximum. Scroll directions must match the selected axis. Scroll execution itself is currently refused for the driver reason below.

## Native mapping and freshness

The adapter inspects the installed driver's actual `tools/list` and verifies its executable SHA-256 and negotiated server version against `dependencies/cua-driver.lock.json`. It owns a private `mcp --direct --no-overlay` process, with telemetry and update checks disabled for the child. It does not install a daemon or modify the user's driver settings.

| Observed operation | Native mapping / limitation |
| --- | --- |
| `activate` | `click` via UIA Invoke |
| `toggle` | `click` via UIA Toggle, only without a preceding Invoke pattern |
| `select` | `click` via UIA SelectionItem, only without preceding Invoke/Toggle patterns |
| `expand` | `click` via UIA ExpandCollapse, only when it is the first driver click pattern and the state permits expansion |
| `set_value` | `set_value` with explicit text and a writable, known non-password Value control |
| `set_range_value` | `set_value` with explicit numeric text, writable RangeValue, known range, and no competing Value pattern |
| `collapse` | Refused: the pinned advertised action surface cannot express explicit collapse |
| `scroll_horizontal`, `scroll_vertical` | Require explicit direction, then refuse: this driver can fall back from failed element scrolling to a different window region |

This mapping follows the pinned Windows implementation at `fc188250b4ca8549b8e61f937fdb1fb560770e86`; it does not infer an operation from a label or click a convenient screen coordinate. The original preview continues to show all provider-supported candidates, including operations the execution adapter cannot faithfully deliver.

After selection, a new UIA snapshot must preserve PID/HWND, the control's native RuntimeId, its identity metadata and ancestor chain, relevant state/value, and the selected enabled/onscreen operation. Snapshot indices, AutomationIds and labels alone are insufficient. Missing/ambiguous identities stop input. Geometry may move between selection and revalidation; execution uses the fresh bounds. Some providers, including some WinForms tab controls, expose no usable RuntimeId and cannot execute under this policy.

The driver then obtains its own fresh window tree without a screenshot. The adapter matches a unique native row by role, label, enabled capability and physical bounds (within two pixels), checks click priority, and passes that row's current `element_token` to the exact PID/HWND. The adapter supplies no desktop coordinates and never retries; native click may use the driver's background input fallback when a UIA pattern fails, and its reported delivery route is retained. This is a conservative bridge between two UIA observations, not a guarantee that arbitrary providers are truthful or that the UI cannot change during a native call.

The observation helper establishes Per-Monitor V2 DPI awareness before initializing UIA so its bounds agree with the driver's physical desktop coordinates. This follows [Microsoft's UIA screen-scaling guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling); the live fixture checks this on the host's scaled display.

## CLI and verification

The same helper is runnable directly; starting it is an explicit live execution request:

```powershell
$request = @{
  goal = 'Select the existing X tab, then open Bookmarks'
  run_id = 'manual-run-1'
  max_steps = 2
  max_seconds = 180
  max_elements = 1000
  arguments = @{}
}
$request | ConvertTo-Json -Compress -Depth 8 | & ./.runtime/jev-venv/Scripts/python.exe -I ./scripts/cua-run.py --stdin
```

Omitting `target` selects a window automatically, as the viewer does. For owned fixtures or an intentionally fixed CLI target, add `target = @{ hwnd = 12345; pid = 6789 }` using a current PID/HWND; this bypasses only the automatic window-selection stage. Output is JSON-lines with a run ID: progress, window_inventory, window_selection, observation, selection, action, result/error. The result includes the selected target and window-selection result when automatic selection runs. The viewer keeps stdin open and sends `{"cancel":true,"run_id":"manual-run-1"}` to request cancellation. The helper has a hard lifetime guard even if credentials or UIA hang. Export is deliberate; no target snapshots or provider requests are automatically logged by this helper.

Offline tests cover automatic window selection, no-match and ambiguity stops, stale window identities, cancellation and budgets, selected-target propagation, execution state transitions, and native mapping/transport boundaries. These use fake model/native responses and send no target input. The earlier benign desktop smoke used the real observer, full selector contract with deterministic offline decisions, and real pinned driver. See [measured execution verification](CUA_EXECUTION_VERIFICATION.md) for those historical outcomes and limitations; they do not establish live accuracy of automatic window selection.

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_execution.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_window_selection.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_execution_runtime.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_execution_cli.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_uia_execution_viewer.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -m unittest discover -s tests -p 'test_cua_driver.py' -v
```
