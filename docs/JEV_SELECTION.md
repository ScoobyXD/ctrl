# Multi-stage Jev selection preview

This preview lets several Jev decisions contribute to one recommended computer action. It consumes a fixed UIA snapshot and a user goal. It does not perform clicks, typing, window switching, or other target input.

The user still chooses the window to observe. Automatic app/window/tab selection and an execution loop are separate future work. Classification cannot find controls the accessibility provider did not expose, including inactive tabs or unexpanded content. Partial observations stay explicitly marked in selection results.

## Flow

1. **Classify every observed option.** Bounded batches of options go to Jev with the goal, snapshot context, and control descriptions. Each option receives its own three-way Choice: `promising`, `uncertain`, or `unlikely`. These are judgments about advancing the user's goal, not keyword matches or detector-confidence thresholds. No goal-based local sort drops candidates before this stage.
2. **Preserve alternatives.** Promising and uncertain options remain active. Unlikely options are retained as a reserve. If every option is unlikely, the search considers that reserve instead of manufacturing a winner.
3. **Narrow large sets in parallel.** When the active set exceeds a single Choice's capacity, each group gets a Choice among its options plus `__none__`. The best few non-none candidates within each group advance. Additional rounds repeat as needed. Group sizes and retained counts are resource settings, not claims of optimal model accuracy.
4. **Compare finalists together.** A shared final Choice compares the remaining options plus `__none__`. Raw Choice probabilities from unrelated groups are never used to rank those groups against each other.
5. **Recover or stop.** If the final Choice selects none, a bounded recovery pass can examine candidates set aside earlier. A no-match, cancellation, or exhausted budget returns no selected action. Recovery does not guarantee retention of the correct candidate or correct model judgment.

An operation candidate already pairs an element with a capability, such as activating a button or setting a field. The existing UIA capability checks remain: an unsupported, disabled, or offscreen operation is not invented. These checks describe what can be attempted; Jev supplies the task-relevance judgments.

Every model question sees the goal and observation context directly. Questions in one request do not consume each other's answers. A later round is a new request built after the preceding answers are validated. Parallel calls can overlap; dependent rounds still add latency and cost. Repeated calls to the same model are not independent correctness votes.

## Setup and use

Install the viewer as described in [UIA_VIEWER.md](UIA_VIEWER.md), then install the existing isolated Jev runtime from the repository root:

```powershell
pwsh -NoProfile -File ./scripts/install-jev.ps1 -Python '<CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/start-uia-viewer.ps1
```

Observe a window, open **Jev selection preview**, enter the goal, and click **Preview selection with Jev**. That GUI button starts live remote requests; merely opening the tab or entering a goal does not. The preview considers every option in the snapshot, regardless of the table's highlighted row, search filter or sorting. It displays progress and the final recommendation; nothing is executed. Changing the goal, refreshing windows, selecting another window or taking a new observation invalidates results. **Cancel selection** stops the local helper; already submitted remote requests may still complete and incur charges.

The remote input is a bounded text description: your goal, the observed window title, option operation/label, control name/role/flags, selected state fields, readable non-password values, and up to four ancestors per candidate. Desktop bounds, HWND/PID, AutomationId and screenshots are absent. A `set_value` recommendation identifies the operation and control; it does not generate replacement text or type it. **Rounds and progress** shows completed requests; **Result JSON** and **Export selection JSON** expose the current result and trace.

The existing [Jev setup](JEV.md) describes the AWS credential lookup and pinned provider route. Importing the selector, observing a window, or running an offline preview does not load credentials or contact AWS/OpenRouter. The viewer uses a separate helper process and sends its snapshot through stdin, without automatically exporting target text to disk.

The GUI selection button uses live mode. For an exported snapshot, inspect the initial requests offline with the CLI instead:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe -I ./scripts/jev-select.py `
  --snapshot ./.ctrl/uia/browser-viewer-export.json --goal 'Save the draft'
```

Add `--live` to run the remote selection process. Optional `--output <path>` explicitly saves its plan/result, which can contain supplied UI text. The command emits newline-delimited JSON events: `progress`, `result`, or a sanitized `error`. Stdin mode accepts an object with `snapshot` and `goal`, plus an optional caller `run_id` echoed as `client_run_id`.

## Budgets, trace and validation

Defaults are 64 options per group, four retained candidates per group, four concurrent requests, at most 64 requests per search, and four narrowing rounds per pass. The final Choice is bounded by 254 candidate options plus none. CLI flags expose the corresponding batch, retention, concurrency, request, and round settings. A resource limit is reported explicitly instead of silently calling an arbitrary surviving candidate correct.

The result is a recommendation over the original snapshot, with `preview_only: true` and `input_actions: 0`. It records source snapshot/run identity, candidate coverage, per-request classifications and retained IDs, stage/round, validated distributions, request count, reported token/cost usage, and provider latency. The viewer validates that a returned selection belongs to the captured option set before displaying it. Labels, focus and state are evidence for Jev; they are not local relevance scores.

Each provider result is checked against its exact request and snapshot binding and the existing pinned model/provider contract. Invalid or foreign IDs, distributions, and bindings are rejected. Input is copied, and password or unknown-password values are withheld. Normal non-password UI text can still be sensitive. Provider exception bodies and credentials are not exposed in helper diagnostics.

The trace explains how the model classified candidates; it is not proof of correctness. The screen may change during selection. A future action executor must obtain fresh evidence and verify target identity/state/capability immediately before executing; this preview contains no executor and makes no freshness guarantee.

## Offline verification

The deterministic tests use fake model responses, including a large candidate set with a late target, uncertain candidates, none/recovery, budget exhaustion, foreign bindings, cancellation and concurrency. Separate CLI and Qt subprocess tests cover explicit live opt-in, sanitized errors, asynchronous cancellation and stale-result rejection. They measure plumbing and invariants, not real Jev accuracy.

```powershell
& ./.runtime/jev-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_selection*.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_uia_*.py' -v
```

Live model accuracy, latency/cost across many controls, and the best group/retention sizes require task-based evaluation. No performance improvement over a single Choice is assumed.

The design is informed by TypeSafe's [hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification) and [Choice](https://docs.typesafe.ai/primitives/choice) documentation. This implementation uses candidate classification and a tournament with recovery; it does not claim to reproduce the cookbook's hierarchy or beam-score formula.
