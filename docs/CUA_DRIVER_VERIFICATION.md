# CUA Driver installation verification

Verified **19 September 2026, 17:38 PDT** (`2026-09-20T00:38:56Z`). Result: **PASS** for installation, native MCP startup/response, and read-only capture of the dedicated fixture. No remaining blocker for this first dependency.

## Versions and provenance

- CUA Driver: **0.28.2**, native `windows-x86_64` release.
- Git tag: `cua-driver-rs-v0.28.2`.
- Source revision: `fc188250b4ca8549b8e61f937fdb1fb560770e86`.
- Pinned archive and executable SHA-256 values: [dependency lock](../dependencies/cua-driver.lock.json).
- Authenticode: **Valid**, signer `Cua AI, Inc.`, certificate thumbprint `FE46633F8C3044A26F237F1E4BCB0FC14BE17637`.
- Host: Windows x64, build **26200.9457**, display version **25H2**; API OS version `10.0.26200.0`.
- Existing PowerShell: **7.6.3**; existing Windows PowerShell fixture host: **5.1.26100.9444**.
- MCP: server `cua-driver` **0.28.2**, protocol **2025-06-18**, **57** advertised tools.
- New system prerequisites installed: **none**.

## Commands and outcomes

The checked-in entry points run from the worktree root:

```powershell
pwsh -NoProfile -File ./scripts/install-cua-driver.ps1
pwsh -NoProfile -File ./scripts/cua-driver-info.ps1
pwsh -NoProfile -File ./scripts/test-cua-driver.ps1
```

The install entry point was exercised both against the worktree's verified archive/install and from an empty ignored staging directory, `.runtime/install-reproduction`, containing only copies of the installer and lock file. The clean run downloaded and extracted the official ZIP successfully; an immediate repeat verified the existing files successfully. No upstream installer script was executed.

The information commands and smoke launcher use these native commands with telemetry and update checks disabled in the child environment:

```text
cua-driver.exe --version
cua-driver.exe telemetry status --json
cua-driver.exe doctor --json
cua-driver.exe mcp --direct --no-overlay
```

The smoke check successfully made only these MCP requests, then closed stdin:

```text
initialize (protocolVersion: 2025-06-18)
notifications/initialized
tools/list
tools/call list_windows {pid: <owned fixture PID>}
tools/call get_window_state {
  pid: <owned fixture PID>, window_id: <owned fixture HWND>,
  include_accessibility_tree: false, include_screenshot: true
}
```

| Check | Observed result |
|---|---|
| Version and hashes | Match the pin; archive matches official release checksum and asset digest |
| Telemetry | `enabled:false`, source `environment`; no installation ID or registration recorded |
| Doctor | `ok:true`; attached interactive desktop and UI Automation probes pass |
| Doctor warning | Default user `.cua-driver` home does not exist; deliberately not created by this setup |
| Fixture | PerMonitorV2, PID `18292`, HWND `2819372`; driver enumeration and capture match both and the title |
| Screenshot | PNG **642 × 405**; metadata matches decoded image; visibly correct label and magenta/cyan panels |
| Pixel checks | 3,604 sampled pixels for each colored panel; black fraction 0.01839 (below padding threshold) |
| Window metadata | Bounds `{x:89, y:80, width:644, height:407}`; retained separately from PNG dimensions |
| Input actions | **0** |
| Shutdown | Driver and fixture both exited **0**, gracefully, without forced termination |
| Driver stderr | Empty |
| Git hygiene | `.runtime/.../fixture.png` confirmed ignored; `git diff --check` passes |

Screenshot SHA-256: `a6e186efe7b1e8c29769ec7bc7c3bccd62f3eac288bbd1935252ccab57d7cb7a`.

Full local evidence is retained in `.runtime/cua-driver/smoke/20260920T003856708Z/`: `result.json`, `doctor.json`, `windows.json`, `capture.json`, `fixture.png`, and process logs. The final PNG was also visually inspected. These generated files are intentionally untracked. No driver or fixture was left running. Neither the user `.cua-driver` directory nor `%LOCALAPPDATA%/cua-driver` was created.

## Resolved issues and limits

- The isolated Codex desktop produced a UI Automation timeout. Repeating the focused test in the normal interactive desktop passed. Run future captures in that session.
- A DPI-unaware WinForms fixture initially produced black padding. Enabling PerMonitorV2 for the fixture removed that artifact; the automated check now rejects substantial black padding. This is not proof that every application's DPI behavior is correct.
- `doctor`'s missing-default-home warning is expected for this manual, local archive installation. Upstream may still read pre-existing user configuration on another machine; the scripts do not mutate that configuration.
- The two-pixel difference between window bounds and PNG dimensions is recorded, not silently equated. No action-coordinate transform has been implemented or validated.
- This verifies one native fixture only. Mouse/keyboard delivery, minimized/occluded applications, future perception, Windows ML, JEV, and the complete assistant remain untested. No paid model call, push, or merge was performed.

See [setup and primary sources](CUA_DRIVER.md) for the installation route and runtime boundaries.
