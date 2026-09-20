# Native CUA execution verification

The executable smoke check uses a dedicated Windows WPF fixture with synthetic
content. It runs the real observation, selection, target revalidation, input
delivery and fresh-observation loop against the pinned native CUA Driver. Jev
responses are deterministic local test data in the real bound response format;
this check does not exercise model quality, credentials, or a live model service.

## Reproduce

Use a logged-in Windows interactive desktop, the repository's UIA Python
environment, and the [pinned native driver setup](CUA_DRIVER.md):

```powershell
& .\.runtime\uia-venv\Scripts\python.exe -I .\scripts\test-cua-execution.py
```

The script starts its own visible `CTRL CUA Execution Fixture`, validates the
published PID against the owned child process, and uses only that PID/HWND for
observation and input. The console is hidden. The fixture closes via an owned
sentinel file, with a three-minute fallback lifetime; the private direct-mode
driver closes at the end. No external site, account, persistent document,
clipboard, credentials, or paid model calls are involved.

## Assertions

1. The first fresh UIA observation exposes an unselected `X tab`; the fixture
   initially has `Home tab` selected. Actual `SelectionSearch` classification and
   final-choice requests receive deterministic responses bound to that snapshot.
2. The execution loop re-observes and validates the selected target before the
   driver delivers tab input. The resulting observation exposes `Open Bookmarks`.
3. A second selection and validation deliver the Bookmarks button action. The
   fixture's independent event ledger must contain exactly one tab selection,
   then exactly one button click. Its visible marker and state record must show
   Bookmarks opened. The loop must stop at its two-action limit.
4. Selecting the note's `set_value` operation without explicit arguments must
   stop with `needs_arguments`, zero attempted actions and no text changes.
5. Supplying a text binding for the note's AutomationId must deliver exactly one
   action. Both the fixture's event/state record and a fresh UIA read must match
   the explicit text. The loop must stop at its one-action limit.
6. A final two-step run selects Home tab, then cancellation is signaled after the
   first action's fresh observation. The run must stop as `cancelled` with one
   action delivered, no second step, and unchanged Bookmarks/text event counts.

Every delivered action must have a separate fresh observation showing a state
change. A selection probability or driver receipt alone cannot satisfy the
fixture assertions. The loop deliberately keeps `goal_completed: false`; the
known fixture assertions supply the narrower test evidence.

## Evidence and scope

Each run writes Git-ignored evidence under
`.runtime/cua-execution/<UTC timestamp>/`: the summary `result.json`, per-run
requests/results/events, raw native tool calls, independent fixture event state,
fresh UIA observation, and fixture process logs. Both owned processes must exit.
The navigation case is a deterministic local smoke
test of the existing-tab then bookmarks flow. It does not establish behavior in
a logged-in browser, arbitrary applications, changed websites, or concurrent
human interaction.

## Measured interactive run

On 2026-09-20 at 22:40 UTC, run `20260920T224021186167Z` passed on this Windows
interactive desktop at 150% scaling, using CUA Driver 0.28.2.

| Check | Observed result |
|---|---|
| Two-step navigation | One X-tab selection followed by one Bookmarks click; stopped at the two-action limit |
| Missing text argument | `needs_arguments`, zero attempted/delivered actions, original text unchanged |
| Explicit text | One text-change event; independent fixture record and fresh UIA value match exactly |
| Cancel between steps | One Home-tab selection, then `cancelled`; no second action |
| Request/input counts | 10 offline Jev responses through real SelectionSearch; 4 native actions; 0 live model requests |
| Process cleanup | Native driver exit 0; owned fixture exit 0 |

The native receipts reported the accessibility route and `effect: unverifiable`.
The tab operations used `SelectionItem.Select`, the button used `Invoke`, and the
text operation used `ValuePattern`. The independent fixture event ledger and
fresh observations provide the evidence of resulting state; the driver receipt
alone does not.

The desktop check also exposed two validation requirements. Standard legacy
WinForms tab items on this machine returned neither a cached runtime ID nor a
direct `GetRuntimeId()` value; execution correctly refused them before input.
The WPF fixture supplies real runtime identities. Initially the observer was
DPI-unaware and its bounds differed from the driver's physical pixels by 1.5×;
the observer now enables PerMonitorV2 awareness before initializing UIA. The
successful run retained strict target matching without adding a scale tolerance.

## Offline regression and viewer checks

The final combined suite passed **189 tests** on 2026-09-20 (35.737 seconds):
execution/controller, actual helper pipe protocol and watchdogs, native driver
mapping/transport/lifetime boundaries, UIA backend/CLI/viewer, and the unchanged
Jev selection/adapter contracts. These tests make no live model requests.

The suite covers cancellation during blocked credential lookup, a hard deadline
with stdout deliberately undrained, clean helper exit while stdin stays open,
stale targets, no-progress detection, missing/ambiguous operands, and retaining
uncertain or pre-input-refused receipts. Viewer regressions cover numeric overflow
and invalid Unicode before process creation, explicit Start, Stop/Escape, target
and goal changes, nonzero helper exits, and export of actual outcomes.

The viewer was also rendered and inspected at a 900 × 650 logical window on the
150% display. The whole page scrolls, the execution controls remain accessible,
and the preview contains Rounds/Request/Result without a Summary tab. Local render
evidence is under `.ctrl/execution-viewer-check/`.
