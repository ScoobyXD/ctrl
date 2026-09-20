# UIA viewer guide

The viewer reads the accessibility information exposed by one Windows application window. **Controls** shows what was observed; **Options** lists operations supported by those controls; **Jev selection preview** can recommend one option for your goal. Selecting a window, table row, or recommendation never clicks, types, scrolls, focuses, or otherwise operates the target. The optional Jev button makes remote model requests; ordinary observation and table inspection stay local.

This is a manual, fixed-snapshot viewer, with no screenshot capture, OCR, action executor, or automatic agent loop. The separate capture viewer, CUA adapter, and YOLO detector are unchanged.

## Quick tour

1. Choose an application in **Window**. Use **Refresh windows** after opening or closing an application.
2. Click **Observe / Refresh** to collect its current accessible controls. The previous snapshot is cleared while the helper runs. **Cancel** stops the helper and leaves no current snapshot.
3. Inspect **Controls** and **Options**. Click a row to read its complete JSON record below the table. Resize columns, click headers to sort, and use the table's horizontal scrollbar for columns further right. Drag the divider above the details pane to give either pane more room.
4. Optionally enter a goal on **Jev selection preview**, then click **Preview selection with Jev**. This explicitly starts remote selection. A selected preview is a recommendation to inspect, with **Input actions: 0**.
5. After the target changes, click **Observe / Refresh** again. There is no continuous monitoring or automatic refresh.

### Window picker

The list contains visible, titled top-level windows, sorted by title. It can include terminals, system windows, and the viewer itself. It is a list of windows, not every installed application or every browser tab. A browser's inactive tabs are not separate targets unless they have their own windows.

Each label includes the window title, **PID** (the owning process) and **HWND** (the Windows window handle). These help distinguish duplicate titles; multiple windows may share a PID. Choosing one only sets the observation target. It does not bring that application forward or immediately read its controls.

The **Window (N)** label reports the actual number of entries. The popup shows up to ten rows and now scrolls in pixels, with an explicitly styled thumb that contrasts with its track. Use the mouse wheel, drag the thumb, or use the arrow keys. With eleven entries and ten visible rows, there is only one row's worth of overflow; the top and bottom views naturally differ by just one row. The previous Windows theme painted the large thumb almost like the background, making the small remaining track look like the thumb. That misleading appearance and item-by-item scrolling explained the apparent jump; it did not indicate a hidden middle group.

**Refresh windows** rebuilds this list and retains the same HWND/PID if still present. It also clears the existing snapshot and Jev result, so press **Observe / Refresh** afterward. Changing the selected window clears them too. The number of entries in this picker is unrelated to the Controls and Options counts.

### Controls tab: what the application exposes

Each row is one UIA element from the selected window's exposed control tree. Rows can represent the window itself, panes, text, scrollbars, browser chrome, and other containers as well as interactive controls. They are not all buttons or actionable items.

The columns show its accessible **Name**, **Role**, **Current value**, **Enabled**, **Focused**, **Offscreen**, **Current state**, **Supported behaviors**, physical desktop **Bounds (x, y, w, h)**, and a snapshot-scoped element ID. `Unavailable` means the provider did not supply a usable value; it does not mean false. `(No accessible name)` means the provider omitted the name. Password and unknown-password values are withheld.

**Supported behaviors** are UIA patterns reported by the application, such as Invoke, Value, Toggle or Scroll. **Focused** and **Current state** describe the observation time; selecting a viewer row does not change the target's focus, checkbox, selection or text. The lower details pane shows the complete element record, including parent ID, raw state and any property errors. IDs identify rows only within this snapshot; names and AutomationId are not guaranteed unique or stable.

### Options tab: supported operation candidates

Each row pairs a control with one supported operation: for example `activate`, `set value`, `toggle`, `select`, `expand`, `collapse`, or horizontal/vertical `scroll`. A control may produce no options, one option, or several. **39 controls / 27 options** therefore means 39 observed elements and 27 eligible element-operation pairs, not 27 selected controls or actions already performed.

Options require a control to be known enabled and onscreen, plus the relevant reported pattern and state. Writable values additionally require explicit non-password status. Disabled, offscreen, read-only or unknown-state controls may still appear in Controls without a corresponding option. See [the candidate rules](#candidate-rules-and-json-interface) for the exact mapping.

**Required input** describes information a future executor would need. `set value` needs explicit text; a scroll option needs a direction. This viewer has no input form or execution button for those operations. Selecting an option only displays the option and its target element below the table, marked `preview_only: true`. Even an unexpected provider-reported setter is only a capability description; no test action is attempted.

### Jev selection preview tab: a recommendation for your goal

First observe a window with at least one option, then enter a nonempty goal of at most 4,000 characters. **Preview selection with Jev** starts a live selection request using the optional [Jev installation](JEV_SELECTION.md#setup-and-use). Opening the tab or writing the goal makes no remote request.

The preview evaluates **all options in the snapshot**, regardless of the highlighted row, table sorting, or search filter. It sends your goal, window title, option descriptions, control names/roles/states, readable non-password values, and limited ancestor context to OpenRouter / TypeSafe. It does not send a screenshot, desktop bounds, HWND/PID or credentials as model evidence. Ordinary UI text in those descriptions can still contain target content.

Jev classifies options as promising, uncertain or unlikely, narrows large groups in bounded rounds, and compares finalists. It can return no match or stop at its request budget. **Rounds and progress** shows the stages and completed requests; **Result JSON** contains the result, trace and reported usage. The recommendation identifies an observed operation and control. It does not supply text to type, execute an action, or establish that the screen is still current.

Changing the goal, refreshing windows, changing targets or observing again invalidates the preview. **Cancel selection** stops the local selection helper; already dispatched remote requests may still finish and incur charges. **Export selection JSON** explicitly saves the current result separately from the UIA snapshot. The [selection guide](JEV_SELECTION.md) covers the algorithm, data flow, budgets and offline CLI preview.

### Search, export, counts and incomplete results

**Search** matches a case-insensitive literal substring across the displayed columns of both tables. It does not search windows, take another observation, or limit Jev's input. With a filter active, a tab such as **Controls (8 / 39)** means 8 visible matches out of 39 captured elements. The summary above the tables continues to show the full snapshot totals.

**Export snapshot JSON** saves the complete current snapshot, including both elements and options, even when search hides rows. It does not include the separately exported Jev result. Normal observations stay in memory; exports can include ordinary target text. The backend never requests password values.

**Element limit** caps the next observation: the viewer defaults to 1,000 and allows 100–5,000. A higher limit does not discover content that the application does not expose, and the observation still has a time budget. **PARTIAL / TRUNCATED** means controls may be missing; warnings describe the available cause. **COMPLETE** means traversal finished within its limits, not that every visual item or hidden page control was found. Collapsed/virtualized content, inactive tabs, custom canvas widgets and inaccessible or elevated targets can be absent or incomplete. The viewer never scrolls or expands the target to reveal more.

The snapshot is a point-in-time approximation: providers can change or report stale information during reads. Observe again after manually switching browser tabs, expanding content or changing target values. More [coverage and validation limits](#validation-and-limits) appear below.

## Install and run

Use Windows x64 in the logged-in interactive desktop, PowerShell 7.2+, CPython **3.12.13 x64**, and uv **0.11.26**. This host already has the interpreter at the path below. Setup creates a separate `.runtime/uia-venv`; it does not modify the original checkout's environments.

```powershell
Set-Location -LiteralPath 'C:/Users/Jonat/ctrl'
pwsh -NoProfile -File ./scripts/install-uia-viewer.ps1 `
  -Python 'C:/Users/Jonat/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe'
pwsh -NoProfile -File ./scripts/start-uia-viewer.ps1
```

Dependencies are five hash-pinned wheel distributions: PySide6, its addons/essentials and shiboken6 **6.11.2**, plus **comtypes 1.4.13**. The Windows UIAutomationCore COM library comes from Windows. No .NET SDK, FlaUI, CUA executable, detector weights, cloud key or account is required. Setup checks the native typelib and keeps its generated Python bindings inside the new venv. Runtime launch is isolated (`-I`) and does not install anything.

The existing UIA worktree can be launched with its installed runtime:

```powershell
pwsh -NoProfile -File 'C:\Users\Jonat\ctrl\worktrees\uia-control-options\scripts\start-uia-viewer.ps1'
```

Each launcher loads the source and runtime from its own checkout. Merging a change into `main` does not automatically update another worktree; update that worktree to the desired commit too. To launch directly from the main checkout after its one-time installation above, use `pwsh -NoProfile -File 'C:\Users\Jonat\ctrl\scripts\start-uia-viewer.ps1'`. Runtime directories are local to each checkout and are not included in Git merges.

The viewer never calls Invoke, SetValue, Toggle, Select, Expand, Scroll, ScrollIntoView, Realize, or input APIs. The option list is descriptive, with no dispatch endpoint or executable command stored in it.

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

The optional [Jev selection preview](JEV_SELECTION.md) consumes this JSON and recommends an existing option. Any future executor would still need an independently designed re-observation, authorization and execution boundary; the current viewer provides no execution stage.

## Validation and limits

### Current scrolling and guide checks

The scrolling change was verified in task **# 3 UIA viewer scrolling and guide** with fresh UIA and Jev environments installed offline from the pinned dependency caches; both dependency checks and imports passed. **88 offline tests passed**: 64 UIA/backend/CLI/Qt tests and 24 selection/CLI tests. That checkout's PowerShell launch command opened the expected Qt viewer and the owned test window closed cleanly.

A native Qt **Windows / windows11** rendering check exercised an eleven-window list and an eighty-window list. The eleven-window thumb occupies **327 of 360 pixels**, and an actual mouse drag reaches **17 of 36 pixels** of overflow. The long list reaches its middle and final row. The check also renders Controls, Options, and a selected Jev result using a synthetic helper, with **zero model requests and zero target input actions**.

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_uia*.py'
& ./.runtime/uia-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_selection*.py'
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/test-uia-window-picker.py
```

The rendering command writes evidence to `.ctrl/uia-picker-check/summary.json` and adjacent `picker-11-*.png`, `picker-80-*.png`, `controls.png`, `options.png`, and `jev-preview.png` in the checkout where it runs. All contain synthetic data. These generated files are not included in Git merges. The original-checkout native/browser evidence below was not copied or rerun for this scrollbar change.

### Historical observation checks from the original checkout

The remainder of this subsection records the original checkout's observation milestone, not new live provider validation in this worktree.

The original observation milestone's installer and hash sync ran successfully. Its **46 UIA tests passed** (22 backend, 7 CLI process, 17 Qt/process; combined run 7.730 seconds). These historical deterministic results are separate from live provider evidence; they exercise password exclusion, unknown capability states, duplicate labels/IDs, budgets, missing targets, stale results, hung/crashed helpers, repeated refresh, cancel, close, filtering and explicit export. The production CLI watchdog terminated fake 10-second initialization/observation hangs with one generic JSON error; the real native reader also returned exactly 5 controls with explicit partial/truncated status under a 5-element cap. The selection preview adds separate [offline selector, CLI and Qt tests](JEV_SELECTION.md#offline-verification). The full older capture/detector dependency suite was not reprovisioned for this independent environment.

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

Historical evidence in the original checkout: `.ctrl/uia/native-observation.json`, `browser-observation.json` (direct backend); `native-viewer-export.json`, `browser-viewer-export.json` (GUI exports); `viewer-summary.json` (repeatable GUI result); `viewer.png`, `native-viewer.png`, `browser-viewer.png` (actual Qt-rendered viewer images). These fixture-only artifacts are intentionally ignored by Git and are not present in this new worktree. No personal target was used for that observation validation.

### Coverage limits

- A completed traversal means the currently exposed control view was walked within budget, **not** that every application element was found. Canvas/WebGL, inaccessible custom widgets, collapsed/virtualized children, inactive tabs and other windows can be absent. Nothing is expanded, scrolled or realized to improve coverage.
- Browser accessibility can activate internally on observation. No browser focus/input/flags are used by the reader. Only the selected browser window's exposed tree is read; inactive page DOM is outside scope.
- Provider metadata can be stale or inaccurate. A UIA snapshot is not atomic; values and flags may change between reads. HWND/PID checks catch common closure/reuse but do not make identities permanent.
- Password protection depends on the provider correctly exposing `IsPassword`; the reader withholds unknowns and never requests password values. Ordinary non-password UI text can still be sensitive, and explicit exports contain that text. Observation alone does not upload or automatically log target text; explicitly starting Jev sends the reduced control descriptions described above.
- Elevated/protected apps, locked/noninteractive desktops, and provider failures may return an error or incomplete tree. Launch on the normal desktop at the target's accessible integrity level. The sandbox desktop produced hidden controls and slow calls here; this does not represent the normal-desktop result. No system/security settings were changed.
- Validation covers one native framework and one Chrome page on this PC, not every browser/application. Standalone packaging and clean-machine installation are not verified.

Generated environments, the isolated browser profile, JSON observations and images remain ignored under `.runtime/` and `.ctrl/`. Implementation, launchers, pinned dependency definitions, fixtures, tests and documentation are the source-controlled files.
