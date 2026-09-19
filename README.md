# [CTRL]

Local browser agent, powered only by Jev through OpenRouter. Uses your normal running Chrome, with its existing profile and tabs. AWS stores the OpenRouter credential; the agent and interface run on your Windows computer.

## Start

On this laptop, double-click **Start CTRL.cmd**, or run `Start-CTRL.ps1`. The launcher starts a hidden local Python process and opens http://127.0.0.1:8766 in your normal Chrome. It reuses a running [CTRL] server.

1. Startup loads the OpenRouter key directly from AWS Secrets Manager into Python memory and verifies Jev. **Check access** retries that check if needed.
2. On first connection, open `chrome://inspect/#remote-debugging`, enable **Allow remote debugging for this browser instance**, and accept Chrome's connection prompt. [CTRL] connects automatically when the setting becomes available; **Connect Chrome** retries a failed connection. Keep Chrome open.
3. Select an existing web tab. Enter the outcome you want, quoting any exact text the agent should type, or enter those values under **Text to enter**.
4. Click **Run agent**. **Single step** executes one observe/decide/action cycle. **Choose only** previews a decision; **Execute** applies it if it is still fresh.
5. **Pause** stops further agent steps. The current action may finish. **Clear session** detaches the agent and leaves the page open.

Example: on a Wikipedia tab, enter `Search Wikipedia for "Ada Lovelace", open the article, and stop when it is visible.` This is a user goal, not a prepared website workflow.

Run `Stop-CTRL.ps1` to stop the background app. Chrome and its tabs remain open. Task state is in memory and is cleared when the server stops. No scheduled startup task or cloud backend is installed.

## Setup on another Windows computer

Install Python 3.12+ (or let uv provide it), [uv](https://docs.astral.sh/uv/), Node.js, Google Chrome, and AWS CLI. Then:

```powershell
git clone https://github.com/ScoobyXD/ctrl.git
cd ctrl
uv sync --locked --python 3.12
npm.cmd ci
npm.cmd run build
aws sso login --profile mangorts-dev
.\Start-CTRL.ps1
```

The AWS SSO profile must already be configured and permitted to read the OpenRouter secret. When sign-in expires, run `aws sso login --profile mangorts-dev`, then **Check access** again. [CTRL] does not copy a key into source, environment files, the frontend, or logs.

Nonsecret settings can be supplied through `CTRL_AWS_PROFILE`, `CTRL_AWS_REGION`, and `CTRL_OPENROUTER_SECRET_ID`. Defaults use the existing `mangorts-dev` profile and the designated `mangorts/dev/openrouterjev` secret in us-west-1. `uv run ctrl --port 8766` runs the server in a visible terminal.

## What Jev can do

Each observation reads visible page text and supported controls. One Jev request asks for an operation and its operation-specific targets. Code validates only the selected target, checks its freshness, executes it once, and observes again.

- Click observed controls and links; fill fields with exact supplied strings; select observed native options; scroll; wait; finish; or request missing input.
- Jev is the only model: `typesafe/jev-1.13` through OpenRouter's `/api/alpha/decisions`. No planning model or text-generation helper is present.
- Text entry is selection, not prose generation. Quoted task strings and supplied values are copied verbatim. If needed text is missing, the run stops at `needs_input`; add values and resume.
- Supports ordinary visible DOM controls. Password/file inputs, browser-internal pages, canvas-only controls, and controls inside inaccessible frames or shadow roots are not supported. There is no screenshot/vision fallback.
- At most 60 executed actions and 120 model decisions per run. At most 32 supplied strings; more than 255 field/string pairs produces an explicit error asking for fewer strings.
- The agent stays attached to the chosen tab. It does not automatically follow newly opened tabs. Choose a new target explicitly when needed.
- A model's `done` status is its judgment, not independent proof. Inspect the final page and action history.

The browser-control transport is local, but task text, supplied values, visible page text, and recent actions are sent to Jev through OpenRouter. Only the selected page is observed for model input. The API credential remains in the Python process.

## Development

```powershell
uv sync --locked
npm.cmd ci
npm.cmd run typecheck
npm.cmd run build
uv run ruff check .
uv run pytest -q
uv build
```

Tests use simulated model/browser responses and do not call paid APIs. For an explicit live connection check with your real credential: `uv run python -m ctrl.diagnostics`. For the running app: `uv run python scripts/check_app.py --credentials --connect`.

The TypeScript interface lives in `web/`; `browser/snapshot.ts` compiles to the page reader. Python `ctrl/` owns the local API, AWS credentials, and Chrome connection. `jev_ultrafast/` retains the adapted upstream loop and executor. See [UPSTREAM.md](UPSTREAM.md) for source attribution and the intentional Jev-only changes.

The API binds only to 127.0.0.1 and checks Host, Origin, and an in-memory session token on API reads/writes. Browser actions are serialized; stale or consumed decisions cannot execute again. An ambiguous browser mutation error stops the run rather than automatically replaying it.
