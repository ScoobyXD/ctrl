# UIA control options

This is the observation and candidate-list milestone: select one application window, read the UIA controls it exposes, and inspect supported operation candidates. Clicking a candidate only displays its record. There is no action execution, Jev decision, model, credential lookup, OCR, screenshot capture or loop.

The old capture viewer, CUA adapter, YOLO detector, and Jev preview are unchanged. This implementation lives on `codex/uia-control-options` in `C:/Users/Jonat/[CTRL] Loop/worktrees/uia-control-options`, based on `a465da8`. It is not integrated into main.

## Install and run

Use Windows x64 in the logged-in interactive desktop, PowerShell 7.2+, CPython **3.12.13 x64**, and uv **0.11.26**. This host already has the interpreter at the path below. Setup creates a separate `.runtime/uia-venv`; it does not modify the original checkout's environments.

```powershell
Set-Location -LiteralPath 'C:/Users/Jonat/[CTRL] Loop/worktrees/uia-control-options'
pwsh -NoProfile -File ./scripts/install-uia-viewer.ps1 `
  -Python 'C:/Users/Jonat/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe'
pwsh -NoProfile -File ./scripts/start-uia-viewer.ps1
```

Dependencies are five hash-pinned wheel distributions: PySide6, its addons/essentials and shiboken6 **6.11.2**, plus **comtypes 1.4.13**. The Windows UIAutomationCore COM library comes from Windows. No .NET SDK, FlaUI, CUA executable, detector weights, cloud key or account is required. Setup checks the native typelib and keeps its generated Python bindings inside the new venv. Runtime launch is isolated (`-I`) and does not install anything.

1. Select a target from the window list. **Refresh windows** picks up newly opened or closed windows.
2. Click **Observe / Refresh**. The old result is cleared; **Cancel** remains available.
3. Search **Controls** or **Options**. A row shows a native name (or an honest missing-name marker), role, current value/state, enabled/focused/offscreen flags, supported UIA patterns, and physical desktop bounds. Select it for complete details.
4. **Export snapshot JSON** explicitly saves the current local snapshot. Normal observations stay in memory. Exported text may include ordinary target content; the backend does not read password values.

The viewer does not focus or raise the selected target. It never calls Invoke, SetValue, Toggle, Select, Expand, Scroll, ScrollIntoView, Realize, or input APIs. The option list is descriptive, with no dispatch endpoint or executable command stored in it.

## Repeatable owned demo

```powershell
pwsh -NoProfile -File ./scripts/start-uia-demo.ps1
```

The demo opens a native WinForms fixture, the self-contained `tests/fixtures/uia-controls.html` in a separate worktree-local Chrome profile, and the viewer. It does not restart/reconfigure normal Chrome profiles or replace existing sessions. Chrome must be installed; use the demo's `-Chrome` parameter for a nonstandard executable path. Choose **CTRL UIA Native Fixture** or **CTRL UIA Browser Fixture** and observe. Each has a draft containing `Example draft`, a theme of `Light`, checked notifications, a synthetic password, enabled/disabled buttons, and `Activations: 0`. The browser page also has collapsed **Additional options**. Observation should leave all of these unchanged. Close the owned fixture/demo browser windows when finished.

The native fixture can also run on its own:

```powershell
powershell.exe -NoProfile -File ./scripts/uia-native-fixture.ps1
```

## Why a dedicated backend

The existing pinned CUA **0.28.2** was inspected first. Its `get_window_state` really supports HWND-scoped UIA with structured controls and no screenshot. However, its source unconditionally requests `UIA_ValueValuePropertyId` without checking `IsPassword`; it advertises setters without `IsReadOnly` gating and includes an inferred invoke for patternless menu items. It also omits some required states from its serialized nodes and caches the entire subtree before applying traversal caps. Reusing that observation route would not satisfy this milestone's password/capability/budget requirements. The old driver remains unchanged.

Sources: [pinned CUA tool contract](https://raw.githubusercontent.com/trycua/cua/cua-driver-rs-v0.28.2/docs/content/docs/reference/cua-driver/mcp-tools-windows.mdx), [pinned Windows UIA implementation](https://github.com/trycua/cua/blob/fc188250b4ca8549b8e61f937fdb1fb560770e86/libs/cua-driver/rust/crates/platform-windows/src/uia/mod.rs), [comtypes](https://github.com/enthought/comtypes).

## Backend and lifecycle

```text
Explicit Observe -> asynchronous Qt QProcess -> one Python MTA process
  -> check HWND/PID -> UIAutomationCore ControlViewWalker
  -> per-element metadata cache -> password-safe value reads
  -> serializable snapshot -> capability/state rules -> options
  -> JSON pipe -> searchable GUI / explicit export
```

`uia_backend.UIAReader` reads controls; `uia_contract.build_options` derives candidates; `scripts/uia-observe.py` is the supported process boundary; `uia_viewer.UIAViewer` owns each helper. Windows listing uses read-only EnumWindows/caption/PID APIs in its own helper. No native element pointer, RuntimeId or durable locator escapes the observation process. Element IDs are generated from a UUID snapshot ID and traversal ordinal. Labels and AutomationId are metadata, never uniqueness or persistence guarantees.

The helper initializes COM as MTA and owns no GUI windows. A `TreeScope_Element` cache contains metadata, pattern-availability flags and non-content state. Cached traversal fetches one node's metadata at a time; it never calls subtree-wide FindAll. Value/RangeValue text is absent from that cache and is read only after cached and fresh `IsPassword` are explicitly false. Unknown password status withholds values and setters. TextPattern document text, legacy values, selected text and password content are never requested. Missing properties are `null`, not default false. Text fields are capped at 512 characters, with explicit warnings.

The viewer defaults to 1,000 elements and a 10-second soft budget; CLI defaults to 8 seconds. CLI limits are 1–5,000 elements, 1–64 depth (default 40) and 0.1–30 seconds. Navigation checks time/count/depth limits between calls. UIA2 connection/transaction timeouts add provider limits when supported. An independent helper watchdog exits after soft timeout + 1.5 seconds; Qt also kills the helper after soft timeout + 3 seconds. Qt never blocks waiting on UIA. Cancellation/close kill only the owned helper, ignore late output and clear stale results. Responses are capped at 7.5 MB (8 MiB receiver limit). Partial traversal, missing properties, initialization errors, target disappearance and empty results are shown explicitly.

This design follows Microsoft's [MTA threading guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading) and [per-element cached property reads](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients). UIA calls can still block inside a provider; the process deadline is the final isolation boundary.

## Candidate rules and JSON interface

Options require `enabled=true` and `offscreen=false`, plus the provider's actual availability flag. Disabled/offscreen/unknown-state controls remain visible in Controls and keep their reported patterns. No operation is invented from a role, name, rectangle or AutomationId.

| Pattern / state | Option |
| --- | --- |
| Invoke | activate |
| Toggle | toggle |
| SelectionItem | select |
| ExpandCollapse with known non-leaf state | expand and/or collapse |
| Value, explicitly writable and non-password | set_value |
| RangeValue, explicitly writable and non-password | set_range_value with reported range |
| Scroll with known scrollable axis | scroll_horizontal / scroll_vertical |

Text, VirtualizedItem, ScrollItem and other reported patterns do not create guessed input options. Setter options require explicit future user-supplied values. Pattern support is the provider's claim; it does not prove a future action would succeed or be appropriate. Some providers expose unexpected writable Value patterns on title bars or checkboxes; these are displayed as reported, with no test action attempted.

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/uia-observe.py windows
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/uia-observe.py observe `
  --hwnd <returned-hwnd> --pid <returned-pid> --max-elements 1000 --timeout 8
```

Each command writes exactly one UTF-8 JSON object to stdout. The caller should parse it in memory; redirect only when a local export is intended. `windows` returns `{windows:[{hwnd,pid,title}]}`. Observation schema version 1 contains:

- `snapshot_id`, `source: "windows-uia"`, `target`, `started_at`, `elapsed_ms`.
- `status: complete|partial|error`, `error`, `truncated`, `warnings`, `coverage`.
- `elements`: snapshot-scoped `id`, `parent_id`, `depth`, `name`, `role`, AutomationId/class/framework metadata, `value`, `is_password`, enabled/focused/offscreen, bounds `[x,y,width,height]`, pattern names, raw state and per-property errors.
- `options`: snapshot-scoped `id`, `element_id`, `operation`, `label`, and a descriptive `requires` object. No native references or execution methods.

Future selection/Jev code can consume this JSON, but would need an independently designed re-observation, authorization and execution boundary. This milestone provides none of those later stages.

## Validation and limits

The installer and hash sync were run successfully. **All 46 UIA tests passed** (22 backend, 7 CLI process, 17 Qt/process; combined run 7.730 seconds). These deterministic tests are separate from live provider evidence; they exercise password exclusion, unknown capability states, duplicate labels/IDs, budgets, missing targets, stale results, hung/crashed helpers, repeated refresh, cancel, close, filtering and explicit export. The production CLI watchdog terminated fake 10-second initialization/observation hangs with one generic JSON error; the real native reader also returned exactly 5 controls with explicit partial/truncated status under a 5-element cap. Existing capture/detector/Jev source and tests are unchanged; the full old dependency suite was not reprovisioned for this independent environment.

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_uia_*.py' -v
```

Live verification used Windows build **26200** on this host's interactive desktop with an owned WinForms window and the ordinary HTML fixture in Chrome **153.0.8010.52** (no forced accessibility flag, extension, DOM parser or website API). Initial direct observations on 20 September 2026 UTC returned **22 native controls / 10 options in 109 ms** and **59 browser controls / 21 options in 140 ms**, both complete within the exposed control view. Native and browser draft values were `Example draft`; password elements were identified with null values; disabled buttons had no options; the native read-only counter had no setter; both counters stayed at zero. Browser UIA exposed its actual page edit, checkbox, select and expandable details alongside browser chrome.

The repeatable live GUI test (after demo readiness files exist) is:

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/test-uia-viewer.py `
  --native-ready .ctrl/uia/demo-native-ready.json `
  --browser-ready .ctrl/uia/demo-browser-ready.json
```

It targets only the owned fixture identities, presses the viewer's own Observe control, checks visible table contents and responsiveness, exports fixture-only JSON and saves viewer images under ignored `.ctrl/uia/`. Add `--keep-open` to leave the verified browser options visible afterward. The live GUI check passed two observations per target with new snapshot IDs, displayed **22 / 10** native and **59 / 21** browser controls/options, verified filtering and non-executing selection previews, and confirmed unchanged fixture values and absent password sentinels in both exports. The initial GUI run recorded 125 ms for each final observation and 213 GUI timer ticks across the test; these are measured examples, not benchmark guarantees.

The final `--keep-open` run passed again (native 109 ms, browser 125 ms, 198 GUI timer ticks). The regular hidden-console launcher and demo reuse command were also executed successfully, including the worktree path's literal square brackets. One viewer was left open with the browser options populated; its completed status was independently confirmed through UIA.

Local evidence: `.ctrl/uia/native-observation.json`, `browser-observation.json` (direct backend); `native-viewer-export.json`, `browser-viewer-export.json` (GUI exports); `viewer-summary.json` (latest repeatable GUI result); `viewer.png`, `native-viewer.png`, `browser-viewer.png` (actual Qt-rendered viewer images). These fixture-only artifacts are intentionally ignored by Git. No personal target was used for observation validation.

Coverage limits:

- A completed traversal means the currently exposed control view was walked within budget, **not** that every application element was found. Canvas/WebGL, inaccessible custom widgets, collapsed/virtualized children, inactive tabs and other windows can be absent. Nothing is expanded, scrolled or realized to improve coverage.
- Browser accessibility can activate internally on observation. No browser focus/input/flags are used by the reader. Only the selected browser window's exposed tree is read; inactive page DOM is outside scope.
- Provider metadata can be stale or inaccurate. A UIA snapshot is not atomic; values and flags may change between reads. HWND/PID checks catch common closure/reuse but do not make identities permanent.
- Password protection depends on the provider correctly exposing `IsPassword`; the reader withholds unknowns and never requests password values. Ordinary non-password UI text can still be sensitive, and explicit exports contain that text. No observations are uploaded or automatically logged.
- Elevated/protected apps, locked/noninteractive desktops, and provider failures may return an error or incomplete tree. Launch on the normal desktop at the target's accessible integrity level. The sandbox desktop produced hidden controls and slow calls here; this does not represent the normal-desktop result. No system/security settings were changed.
- Validation covers one native framework and one Chrome page on this PC, not every browser/application. Standalone packaging and clean-machine installation are not verified.

Generated environments, the isolated browser profile, JSON observations and images remain ignored under `.runtime/` and `.ctrl/`. Only implementation, launchers, pinned dependency definitions, fixtures, tests and documentation are committed.
