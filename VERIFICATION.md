# Verification

Verified on this Windows laptop during setup:

- AWS SSO renewed successfully for `mangorts-dev`.
- The designated AWS secret was retrieved directly into runtime memory.
- OpenRouter Decisions returned a valid choice from `typesafe/jev-1.13-20260917`. The app's separate live check also passed (826 ms for its minimal request; not a browser-task benchmark).
- 87 offline Python tests pass, including model contract, exact supplied strings, cancellation, stale-page checks, no replay after partial mutation, existing-tab preservation, credential redaction, and localhost API boundaries.
- TypeScript type checking, interface build, page-reader compilation, Ruff, Python source distribution, and wheel build pass.
- The local server and static assets respond at `http://127.0.0.1:8766`.

Not yet verified:

- A live Chrome task and visual interface inspection are pending Chrome's one-time remote-debugging approval. Chrome's `DevToolsActivePort` file was absent at the last check. The app detects the setting once enabled and retains the native approval handshake.

These results distinguish a verified model/API integration from an end-to-end browser run. No claim of browser-task success is made before a real task is observed to finish.
