# Verification

## Current baseline and isolated hybrid update (2026-09-19)

- The native Windows mode chooser opens Ultra-fast / Jev only or the separate hybrid installation. Each has its own environment, API port, task state, native debugger, and Stop controls. No task starts from choosing a mode.
- Baseline: 371 offline Python tests pass (363 in the full core run plus eight isolated shutdown regressions); Ruff and source/wheel builds pass. Native debugger and chooser tests render the actual forms using synthetic callbacks. The debugger also decoded and rendered a real recorded synthetic-inbox trace: blocked status, four actions, all 30 offered options. That rendering submitted no commands.
- The catalog live test passed in normal Chrome with real Jev: six actions in 3.28 seconds entered `trail`, selected Outdoors, enabled In stock only, applied the search, and opened Alpine Trail Mug. Independent fixture evidence confirmed the filters and rendered product body.
- The 12-message synthetic inbox passed: 27 actions in 12.05 seconds, all 12 unique bodies rendered, and all three list pages visited. Its 35 traces contained 27 executed actions, seven stale decisions rejected without replay, and one terminal decision. Internal freshness fields were absent from the API projection.
- The 100-message synthetic inbox failed. Three dispatched clicks had no visible effect. Fixture instrumentation then proved the page was focused but hidden and received no mouse events. These runs are failures, not evidence of 100-item capability.
- The executor now restores only the selected minimized Chrome window, activates the tab once, and waits for actual visibility, focus, and two animation frames before page input. A bounded timeout stops before input if Chrome remains hidden. Freshness is rechecked afterward. After the user approved Chrome, the fresh catalog retest stopped before input because the target still did not become visible. This is a failed task, with zero submitted filters or opened products. Native foreground handoff and explicit navigation activation are now implemented and pass regression tests. Repeated launcher commands restore the existing native windows. The backend has been reloaded and is idle, awaiting Chrome's native Allow prompt; live verification remains incomplete.
- Stop now attempts authenticated graceful shutdown even with an absent, invalid, or reused PID file. Force fallback checks exact installation ownership and process creation time; it reports success only after no owned process or local listener remains. Eight copied-script regressions cover these cases without stopping either installed app.
- A process-lifetime lease prevents both experiments from owning Chrome at once. It includes approval and disconnect cleanup; delayed cancellation cannot release ownership before transport cleanup finishes. Offline cross-process and race regressions pass.
- Actual two-backend verification passed: the hybrid at port 8767 remained idle and could not connect while baseline owned Chrome. The hybrid's actual Stop script made its health endpoint unavailable while baseline at port 8766 stayed idle and running. Chrome and its tabs were preserved.
- The baseline is still on the reliability branch. It has not been merged to main because the corrected live browser retest is incomplete.

The hybrid's own test record and limitations are in its separate repository, `ctrl-tiptour-experiment`, under `windows-browser/VERIFICATION.md`. Its planner-plus-Jev API compatibility on synthetic supplied observations is not proof of a completed live browser task. Baseline remains Jev-only.

Sanitized baseline evidence is in [docs/reliability-verification.json](docs/reliability-verification.json). Older checks below are historical.

## Earlier setup checks

Verified on this Windows laptop during setup:

- AWS SSO renewed successfully for `mangorts-dev`.
- The designated AWS secret was retrieved directly into runtime memory.
- OpenRouter Decisions returned a valid choice from `typesafe/jev-1.13-20260917`. The app's separate live check also passed (826 ms for its minimal request; not a browser-task benchmark).
- 87 offline Python tests pass, including model contract, exact supplied strings, cancellation, stale-page checks, no replay after partial mutation, existing-tab preservation, credential redaction, and localhost API boundaries.
- TypeScript type checking, interface build, page-reader compilation, Ruff, Python source distribution, and wheel build pass.
- The local server and static assets respond at `http://127.0.0.1:8766`.

Live browser verification:

- Connected to the normal running Google Chrome 152.0.7977.83 through its approved native remote-debugging endpoint.
- Executed the goal: `Search Wikipedia for "Ada Lovelace", open the article, and stop when the article is visible.`
- Jev chose three browser actions: click Search, type the exact supplied `Ada Lovelace` string, and click the observed article suggestion.
- Independently checked the final observed URL (`https://en.wikipedia.org/wiki/Ada_Lovelace`), title (`Ada Lovelace - Wikipedia`), and visible article heading/body. The result tab remains open.
- Recorded agent elapsed time: 5.835 seconds, beginning after initial observation. This is one observed run, not a general latency benchmark.
- The task ran through the local application's API with the real AWS-loaded OpenRouter credential. No other model or prepared website action sequence was used.
- GitHub's Windows CI passed the tests, TypeScript builds, lint checks, and package build for the implementation commit.

A compact record is in [docs/live-verification.json](docs/live-verification.json). The run verifies the tested task. Visual interface automation remains unverified because the available computer-use runtime failed on the original bracketed workspace path; the interface's HTML, JavaScript, and CSS serve successfully.


Navigation and stopped-run fix:

- Reproduced the reported Gmail/X/Reddit task as `blocked` with zero browser actions while the selected tab remained the Ada Lovelace article. Chrome and the AWS/OpenRouter connection were both healthy. The original executor had no cross-site navigation operations.
- Added finite requested URL destinations, observed-tab switching, Back, visible terminal reasons, and a configurable 1–500 action budget (default 200).
- 143 offline Python tests and 14 frontend regression checks pass. Type checking, production asset build, Ruff, and source/wheel builds pass. Navigation tests cover stale/closed destinations, cancellation, preservation of tabs, loading readiness, and no replay after unconfirmed mutations.
- A real Jev/OpenRouter decision check using the exact reported task and a synthetic public Wikipedia page selected `OPEN_URL` with the offered Gmail destination. This verifies model/API compatibility for the new operation; it does not verify live browser navigation or reading emails.
- The updated local app is running. A live public-site navigation test is waiting for Chrome's renewed native remote-debugging approval after the app restart. No email-reading, X, or Reddit interaction was performed during this fix.
