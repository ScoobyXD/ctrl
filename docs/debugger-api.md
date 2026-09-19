# Native debugger API contract

The desktop debugger uses the existing loopback API and in-memory X-CTRL-Token. It never receives the OpenRouter credential. The browser agent remains Python/Jev/Browser Harness and controls normal Chrome.

`GET /api/state` retains the existing fields and adds `trace_summaries` and `latest_trace_id`. A summary is `{id, sequence, status, operation, target_label, latency_ms, timestamp}`. The selected/most recent trace is loaded with authenticated `GET /api/traces/{id}`. Unknown IDs return 404. Traces live in memory for the current task; cancel preserves them, starting/resetting a task replaces them.

Trace detail:

```json
{
  "id": "opaque-id", "sequence": 1, "timestamp": "ISO8601 UTC",
  "observation": {"title":"", "url":"", "text":"", "actions":[], "elements":[], "fingerprint":"", "task_browser_scope":{}},
  "questions": [{
    "id":"operation", "label":"Next operation", "applies":true,
    "selected_id":"CLICK", "confidence":0.9, "validation":"valid",
    "options":[{"id":"CLICK","label":"Click an observed control","probability":0.8,"selected":true,"used":true,"allowed":null,"validation_note":"Browser validation pending"}]
  }],
  "selected":{"operation":"CLICK","action_id":"e1","target_id":"2","target_label":"Search"},
  "validation":{"status":"pending","message":"Browser validation pending"},
  "execution":{"status":"pending","message":"Decision preview; no action executed"},
  "model":"typesafe/jev-1.13", "latency_ms":200, "request_bytes":7000
}
```

Probabilities come from Jev. `selected` means a head answered that option; `used` means that answer belongs to the actual selected operation. Unused speculative target/reason heads are not browser actions. `allowed` is null until runtime validation, true after the selected branch passed, false if rejected; it stays null for unused options. Invalid/missing provider numbers are null, never invented. The UI must label ignored/non-evaluated branches clearly. This is an observable decision audit, not generated reasoning text or a hidden reasoning transcript.

Execution status is one of `pending`, `executed`, `terminal`, `paused`, `cancelled`, `rejected`, `error`. Validation is `pending`, `passed`, `rejected`, `stale`, or `error`. Cancel/Stop never automatically retries an action. Trace summaries reflect those statuses and preserve step order.

`POST /api/cancel {}` cancels the current task, invalidates a pending preview, preserves history/traces, and leaves the application running. The status becomes `cancelling` while an in-flight operation finishes, then `cancelled`. A cancelled task requires Start or explicit Restart task. Run/Step/Choose cannot resume it. Existing Pause remains resumable. Shutdown turns the app off and requires explicit app restart.

Native controls call existing `start` with `{goal,target_id,values,max_steps,run}`, `run`, `step`, `predict`, `act {fingerprint}`, `pause`, `reset`, and `shutdown`. An edited command/target/budget invalidates a preview. Restart task first cancels and waits until busy=false, then starts the edited/current inputs once. Text fields never auto-submit on polling or reopening the window.
