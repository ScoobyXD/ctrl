# Verification

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
