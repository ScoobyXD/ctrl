# Verification

## Current reliability and native debugger update (2026-09-19)

- Native Windows debugger implemented with task entry, Run/Step/Choose/Execute, Pause, Cancel, Restart task, connection retry controls, a page/action tree, trace timeline, and all offered Jev probabilities. No embedded browser is used.
- Its injected synthetic-API test renders the actual WinForms form at normal and minimum sizes. Selection versus usage, tri-state validation, cancellation/restart, empty starting tab, no automatic task submission, and repeatable Stop routing pass. This verifies the native controls and rendering; it is not a live browser task.
- 302 offline Python tests and 18 frontend regressions pass. Ruff, TypeScript checks, compiled assets, source distribution, and wheel build pass. GitHub Windows CI also checks the native debugger with synthetic data.
- Internal freshness guards stay inside the worker; authenticated debugger responses expose only the observed page fields needed by the UI. Cancelled native startup requests are checked before and after UI compilation.
- The reported alternating-tab loop was reproduced from a prior run's 153 navigation-only actions. Task-site filtering, neutral bootstrap, bounded request history, navigation-cycle stopping, and an independent emergency panel were added. The navigation guard rejects a reproduced A/B/A/B cycle before its fourth switch.
- The independent Stop script was exercised against the owned idle local server. The server stopped and its health endpoint became unavailable. The script does not target Chrome. Its native self-test also registered the global emergency shortcut and checked button/shortcut routing with a stub launcher.
- A live Jev task on the synthetic catalog opened the destination in normal Chrome, then failed before text insertion: `The observed field did not receive focus; no text was inserted.` Independent fixture evidence confirmed no search submission and no product detail opened. This run **failed**.
- The executor now brings the selected tab forward before actual page input, then rechecks cancellation and target geometry. Offline focus/cancellation tests pass. The corrected live task is **not yet verified**: after app restart, AWS SSO reported expired sign-in and Chrome requires its native connection approval again.
- The app is installed in `C:\Users\Jonat\ctrl`; the native debugger and emergency panel are available through Start CTRL.cmd and desktop shortcuts. No previous task starts automatically.

Sanitized evidence is in [docs/reliability-verification.json](docs/reliability-verification.json). The older checks below are historical and do not establish that the latest multi-site or 100-message tasks work.

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
