# [CTRL]

Local browser agent, powered only by Jev through OpenRouter. Uses your normal running Chrome, with its existing profile and tabs. AWS stores the OpenRouter credential; the agent and interface run on your Windows computer.

## Start

On this laptop, double-click **Start CTRL.cmd** or the **CTRL** desktop shortcut to choose **Ultra-fast / Jev only** or the separately installed **Hybrid experiment**. The chooser submits no task. **CTRL Debugger** opens the Jev baseline directly. From PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\Jonat\ctrl'
.\Start-CTRL.ps1
```

This opens a native Windows debugger and an independent emergency control panel. Python runs locally at `127.0.0.1:8766`; the debugger is a desktop window, with no embedded browser. It reuses a running [CTRL] server and never resumes a previous task automatically. The older web dashboard is optional: `Start-CTRL.ps1 -WebDashboard`.

The hybrid lives in its own TipTour worktree at `C:\Users\Jonat\ctrl-experiments\tiptour-windows\windows-browser`, with a separate environment, native interface, runtime state, and port 8767. It uses an OpenRouter reasoning planner plus Jev; no planner is added to this baseline. Its private repository is [ctrl-tiptour-experiment](https://github.com/ScoobyXD/ctrl-tiptour-experiment). The original TipTour app requires macOS; the Windows adaptation is explicitly experimental. Stop the current app before connecting Chrome from the other mode. A process-lifetime lease prevents simultaneous browser ownership, including while approval or disconnect cleanup is pending.

1. Startup loads the OpenRouter key directly from AWS Secrets Manager into Python memory and verifies Jev. **Check access** retries that check if needed.
2. On first connection, open `chrome://inspect/#remote-debugging`, enable **Allow remote debugging for this browser instance**, and accept Chrome's connection prompt. [CTRL] connects automatically when the setting becomes available; **Connect Chrome** retries a failed connection. Keep Chrome open.
3. Enter the outcome you want. Leave **Start tab** on Automatic for a command containing a destination, or select an existing web tab for work on that page. Quote exact text or put it in **Exact text to enter**. Explicit URLs/domains work; Gmail, Reddit, X/Twitter, YouTube, AWS console, Wikipedia, GitHub, and several Google apps also have address shortcuts. These are destinations, not prepared action sequences.
4. Click **Run task**. **Single step** executes one cycle. **Choose only** previews a decision; **Execute choice** applies it if the page and your inputs are still fresh.
5. **Pause** stops further steps and permits a fresh choice on resume. **Cancel task** discards the pending choice and preserves the trace. **Restart task** cancels, waits for the worker to stop, then starts the current command once. An in-flight browser action may finish before cancellation takes effect.
6. **STOP APP**, the independent red Stop panel, the **STOP CTRL Agent** desktop shortcut, or **Ctrl+Alt+Shift+S** turns the local agent off. Restarting the app is explicit. Closing the compact emergency panel also stops it; closing the debugger alone leaves the emergency panel available.

The debugger's bottom section shows observed page text, available controls, a decision tree, and the trace timeline. The right table displays every offered choice with Jev's returned probability. **Answered** means that head selected it; **Used** means it belongs to the selected operation. **Allowed** remains unevaluated until runtime checks pass or reject it. Speculative answers from unused heads are visible but never executed. Missing/invalid probabilities display as unavailable. These are recorded decisions and observations, not an invented reasoning transcript. Traces stay in memory for the current task and disappear when you start a new task or stop the app.

**Max actions** defaults to 200 and supports 1–500. The app also limits model calls and stops repeated navigation cycles and repeated actions with no visible progress.

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

- Click observed controls and links; fill fields with exact supplied strings; select observed native options; scroll; open a requested address; switch to an observed tab; go back; wait; finish; or request missing input.
- Jev is the only model: `typesafe/jev-1.13` through OpenRouter's `/api/alpha/decisions`. No planning model or text-generation helper is present.
- Text entry is selection, not prose generation. Quoted task strings and supplied values are copied verbatim. If needed text is missing, the run stops at `needs_input`; add values and resume.
- Supports ordinary visible DOM controls. Password/file inputs, browser-internal pages, canvas-only controls, and controls inside inaccessible frames or shadow roots are not supported. There is no screenshot/vision fallback.
- Up to the selected action limit (default 200, maximum 500) and twice that many model decisions per run. At most 32 supplied strings; more than 255 field/string pairs produces an explicit error asking for fewer strings.
- New tabs are preserved and created in the background. Before page input, the selected minimized Chrome window is restored, the tab is activated, and actual visibility, focus, and two rendered frames are checked. If Chrome stays hidden or unfocused, the task stops before sending that input. Merely selecting a tab does not pull focus. Links that open a separate task-site tab become available as observed SWITCH_TAB targets. There is no automatic tab closing.
- Jev selects actions and stop reasons; it does not write email summaries or arbitrary prose. Asking to "interact" with a feed allows browsing, opening, and scrolling; posting, messaging, voting, following, deleting, or archiving require a specific request.
- A model's `done` status is its judgment, not independent proof. Inspect the final page and action history.

The browser-control transport is local, but task text, supplied values, visible page text, and recent actions are sent to Jev through OpenRouter. Observations and tab choices are restricted to the requested sites, or the selected starting site's host when no destinations were given. An unrelated starting page becomes a neutral destination chooser without reading its body. Localhost destinations also retain their exact port. The API credential remains in Python memory.

## Development

```powershell
uv sync --locked
npm.cmd ci
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
uv run ruff check .
uv run pytest -q
uv build
```

Tests use simulated model/browser responses and do not call paid APIs. For an explicit live connection check with your real credential: `uv run python -m ctrl.diagnostics`. For the running app: `uv run python scripts/check_app.py --credentials --connect`.

The TypeScript interface lives in `web/`; `browser/snapshot.ts` compiles to the page reader. Python `ctrl/` owns the local API, AWS credentials, and Chrome connection. `jev_ultrafast/` retains the adapted upstream loop and executor. See [UPSTREAM.md](UPSTREAM.md) for source attribution and the intentional Jev-only changes.

The native Windows interface is `scripts/DebugConsole.cs`, launched by `scripts/Debug-CTRL.ps1`. Its offline self-test uses synthetic traces: `powershell -STA -File scripts/Debug-CTRL.ps1 -VerifyOnly`. `scripts/ControlPanel.cs` is the separate emergency panel. See [docs/debugger-api.md](docs/debugger-api.md) for the decision-audit contract.

Synthetic browser verification is available without opening private accounts. Start `python scripts/browser_lab.py serve`, connect Chrome normally, then explicitly run `python scripts/browser_lab.py smoke --run --scenario catalog` (or `inbox-small` / `inbox-100`). The driver supplies ordinary goals to the same app; independent fixture evidence checks filters and rendered message bodies. Rendering coverage does not prove comprehension, and these live runs are distinct from the offline unit tests.

The API binds only to 127.0.0.1 and checks Host, Origin, and an in-memory session token on API reads/writes. Browser actions are serialized; stale or consumed decisions cannot execute again. An ambiguous browser mutation error stops the run rather than automatically replaying it.
