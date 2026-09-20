# Multi-stage Jev selection preview

This preview lets several Jev decisions contribute to one recommended computer action. It consumes a fixed UIA snapshot and a user goal. It does not perform clicks, typing, window switching, or other target input.

The user still chooses the window to observe. Automatic app/window/tab selection and an execution loop are separate future work. Classification cannot find controls the accessibility provider did not expose, including inactive tabs or unexpanded content. Partial observations stay explicitly marked in selection results.

## Flow

1. **Classify every observed option.** Bounded batches of options go to Jev with the goal, snapshot context, and control descriptions. Each option receives its own three-way Choice: `promising`, `uncertain`, or `unlikely`. These are judgments about advancing the user's goal, not keyword matches or detector-confidence thresholds. No goal-based local sort drops candidates before this stage.
2. **Preserve alternatives.** Promising and uncertain options remain active. Unlikely options are retained as a reserve. If every option is unlikely, the search considers that reserve instead of manufacturing a winner.
3. **Narrow large sets in parallel.** When the active set exceeds a single Choice's candidate or request-size budget, each group gets a Choice among its options plus `__none__`. The best few non-none candidates within each group advance. Additional rounds repeat as needed. Group sizes and retained counts are resource settings, not claims of optimal model accuracy.
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

The viewer's live preview **does retrieve the OpenRouter API key from AWS Secrets Manager**, using the existing secret ARN, profile `mangorts-dev` and region `us-west-1` by default. AWS stores the credential; OpenRouter / TypeSafe handles the model request. You do not need to move the key into the application or an environment variable. The existing [Jev setup](JEV.md) describes the lookup, nonsecret overrides and pinned provider route. Importing the selector, observing a window, or running an offline preview does not load credentials or contact AWS/OpenRouter. The viewer uses a separate helper process and sends its snapshot through stdin, without automatically exporting target text to disk.

The GUI selection button uses live mode. For an exported snapshot, inspect the initial requests offline with the CLI instead:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe -I ./scripts/jev-select.py `
  --snapshot ./.ctrl/uia/browser-viewer-export.json --goal 'Save the draft'
```

Add `--live` to run the remote selection process. Optional `--output <path>` explicitly saves its plan/result, which can contain supplied UI text. The command emits newline-delimited JSON events: `request` with a body about to be dispatched, `progress`, `result`, or a sanitized `error`. Stdin mode accepts an object with `snapshot` and `goal`, plus an optional caller `run_id` echoed as `client_run_id`.

## Inspect and export request bodies

After observing a window and entering a goal, click **Inspect request JSON**. This builds the initial classification bodies locally and opens a resizable inspector window, with no AWS lookup or model call. Closing that window returns its contents to the **Request JSON** tab; **Open larger view** opens it again. The dropdown identifies each request's stage, round, batch, option count and exact serialized byte count. These records are marked **Planned — not sent**. Later reduction/final requests depend on model answers and are not available in this initial plan.

Starting **Preview selection with Jev** replaces that plan with records for the live run, including adaptive rounds. **Dispatch attempted** means the helper was about to call the provider; it does not establish successful delivery. Input/output token counts appear only when the provider reports usage. Request records remain available after failure or cancellation. Changing the goal or snapshot, or starting another inspection/run, clears them.

The default body view is indented for reading. **Exact wire JSON** shows the compact serialization. **Copy body** always copies that compact body; **Export body JSON…** writes its exact UTF-8 bytes with no trailing newline. **Export all requests…** writes a local archive containing request metadata and each `payload`; the archive itself is not one provider request. Bodies can contain ordinary observation text. They remain in memory until you explicitly copy or export them, and never include the Authorization header or API key.

## Request format and size

The outgoing body is JSON with four top-level fields: `model`, `provider`, `state` and `questions`. **The goal is `state.goal`; this application does not send a `problem` field.** `state` also contains the window title, stage/round/batch, snapshot ID, recent history, observation-coverage warning and candidate descriptions. Each candidate includes its operation/label, element description and up to four ancestors. `questions` contains typed Choice questions: classification uses one question per candidate with `promising`, `uncertain` and `unlikely` criteria; reduction/final comparison uses one question with candidate IDs and `__none__` as criteria. The OpenRouter key is an HTTP Authorization header and is excluded from the body and exports.

JSON structures the API request; it does not make the text free of model tokens. In an offline reconstruction of the earlier synthetic 64-option/four-ancestor diagnostic, the compact body was **115,213 bytes**, although the goal itself was only **25 bytes**. Its `state` object occupied **78,879 bytes** and its 64-question object **36,205 bytes**. Repeating four ancestors for each option produced 256 ancestor descriptors using **52,224 bytes**, and each classification question repeated criteria/instructions using about 550 bytes. The current first batch for that same synthetic fixture contains 13 candidates and uses **23,719 bytes**. This is a reconstructed example, not the original failed screen request or captured provider traffic.

**Bytes are not tokens.** The exact provider token count cannot be recovered from serialized size alone; the provider's interpretation of the structured questions also matters. The local byte cap is conservative, not a tokenizer. Reported usage summed across many requests can exceed the model's per-request context limit without any individual request exceeding it. Offline plans expose the exact initial `payload` objects; later reduction/final bodies depend on model answers and cannot all be planned in advance.

## Budgets, trace and validation

Defaults are at most 64 options per group, up to four retained candidates per group, four concurrent requests, at most 64 requests per search, and four narrowing rounds per pass. Every complete serialized JSON request also has a 24,000-byte budget, including the goal, snapshot context, descriptions, questions and answer choices. Groups shrink to fit that budget; narrowing continues when finalists fit the 254-candidate Choice limit but their descriptions are still too large. Groups of two or more retain at most half their candidates, rounded down, so small groups still narrow. A single candidate that cannot fit fails locally with a specific context-size message; a round that cannot reduce the active set stops explicitly. This conservative byte budget is not a tokenizer or a guarantee about provider token accounting. CLI flags expose the batch, retention, concurrency, request, and round settings. A resource limit is reported explicitly instead of silently calling an arbitrary surviving candidate correct.

The result is a recommendation over the original snapshot, with `preview_only: true` and `input_actions: 0`. It records source snapshot/run identity, candidate coverage, per-request classifications and retained IDs, stage/round, validated distributions, request count, reported token/cost usage, and provider latency. The viewer validates that a returned selection belongs to the captured option set before displaying it. Labels, focus and state are evidence for Jev; they are not local relevance scores.

Each provider result is checked against its exact request and snapshot binding and the existing pinned model/provider contract. Invalid or foreign IDs, distributions, and bindings are rejected. Input is copied, and password or unknown-password values are withheld. Normal non-password UI text can still be sensitive. Provider exception bodies and credentials are not exposed in helper diagnostics.

The trace explains how the model classified candidates; it is not proof of correctness. The screen may change during selection. A future action executor must obtain fresh evidence and verify target identity/state/capability immediately before executing; this preview contains no executor and makes no freshness guarantee.

## If selection fails

The earlier generic message, "Check provider access, credits, connection, and the pinned model," covered several different failures. The helper now distinguishes context size, authentication/access, insufficient credits, rate limiting, connection failures, and invalid provider responses. It includes a safe HTTP status when available, without exposing raw provider bodies, target text or credentials.

- **AWS sign-in or secret lookup:** This happens before a model request. For an expired default SSO session, run `aws sso login --profile mangorts-dev`, then retry. Missing profiles, secret permissions and malformed secret values have separate messages; see [credential setup](JEV.md#preview-and-live-smoke-test).
- **Context limit:** The model input was too large. The current selector splits requests by both count and serialized size. If even one candidate cannot fit, shorten the goal or observe a smaller target. Retrying the same oversized input without a change will not fix it.
- **HTTP 401/403, 402 or 429:** These respectively indicate credential/access rejection, insufficient credits, or rate limiting. They are different from a successful AWS lookup. The displayed message identifies the relevant check.
- **Response validation:** The model/provider identity, answer IDs or probability data did not match the expected contract. The preview refuses the result; it does not act on it.

During the September 2026 investigation, the default AWS secret lookup succeeded, OpenRouter's key check returned HTTP 200 without an exhausted key limit, and small live synthetic decisions succeeded. A 64-candidate synthetic request succeeded without ancestors but exceeded the model context limit after four browser-style ancestor descriptions were added per candidate. OpenRouter lists a [32,000-token context window for Jev 1.13](https://openrouter.ai/typesafe/jev-1.13). The previous count-only batching could therefore exceed the provider limit within its 64-option default; its shared final comparison also lacked a size bound. This reproduces a failure consistent with the reported 132-option browser snapshot. The exact failed snapshot/request was not retained, so it does not prove that original request had the same cause.

## Offline verification

The deterministic tests use fake model responses, including a large candidate set with a late target, uncertain candidates, none/recovery, budget exhaustion, foreign bindings, cancellation and concurrency. Separate CLI and Qt subprocess tests cover explicit live opt-in, sanitized errors, asynchronous cancellation and stale-result rejection. They measure plumbing and invariants, not real Jev accuracy.

```powershell
& ./.runtime/jev-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_selection*.py' -v
& ./.runtime/uia-venv/Scripts/python.exe -I -m unittest discover -s tests -p 'test_uia_*.py' -v
```

Live model accuracy, latency/cost across many controls, and the best group/retention sizes require task-based evaluation. No performance improvement over a single Choice is assumed.

## Repeatable synthetic browser check

The check below builds 132 synthetic options with four browser-style ancestors per option and the goal "Open a new YouTube video." The default mode reports the initial request count and serialized sizes without AWS access, model calls or opening the viewer:

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/test-jev-selection.py
```

Adding `--live` explicitly makes paid model requests through the real Qt viewer, its selection helper and the existing AWS credential lookup. This requires both the UIA and Jev runtimes. The check populates a synthetic snapshot directly; it does not read another application or execute any selected action. Successful runs write `.ctrl/jev-selection/summary.json` and `live-viewer.png`, containing synthetic evidence only. Use `--output-dir <path>` to choose another evidence directory.

```powershell
& ./.runtime/uia-venv/Scripts/python.exe -I ./scripts/test-jev-selection.py --live
```

The September 2026 fix verification completed this live Qt check: all **132 options** were considered in **11 initial requests and 12 total model requests**. The largest initial request was **23,684 bytes**, within the **24,000-byte cap**. Jev selected the expected synthetic target, **zero input actions** occurred, and reported cost was **$0.004784388**. The real Windows Qt viewer produced the evidence above. This validates the repaired request flow on synthetic browser-sized input; it is not an accuracy benchmark or a replay of the unretained original failed request.

The accompanying **159 offline tests passed**: 64 UIA, 37 selection/CLI, 21 Jev adapter, 12 credentials, 20 decisions and 5 preview tests. They include long browser descriptions, size-bounded classification/final/recovery requests, shrinking small groups, and sanitized failure categories.

The design is informed by TypeSafe's [hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification) and [Choice](https://docs.typesafe.ai/primitives/choice) documentation. This implementation uses candidate classification and a tournament with recovery; it does not claim to reproduce the cookbook's hierarchy or beam-score formula.
