# CUA Driver: first dependency

This installs **native Windows CUA Driver 0.28.2** from `trycua/cua`. It supplies local capture and input delivery. The separate [UIA + Jev execution adapter](CUA_EXECUTION.md) now uses its pinned native action tools. This dependency installer itself uses no CUA Cloud Fleet, VM, account, API key, model API, Python SDK, or npm package, and does not install YOLO, OCR, Windows ML, or Jev.

## Reproduce

Use Windows x64 in a logged-in interactive desktop session and PowerShell **7.2 or newer** (`pwsh`). The smoke fixture uses the built-in Windows PowerShell 5.1 and .NET Windows Forms. This PC already had these prerequisites; no system installation was required. Upstream's prebuilt Windows binaries statically link the C runtime, so a Rust toolchain and Visual C++ Redistributable are unnecessary.

From this worktree:

```powershell
pwsh -NoProfile -File ./scripts/install-cua-driver.ps1
pwsh -NoProfile -File ./scripts/cua-driver-info.ps1
pwsh -NoProfile -File ./scripts/test-cua-driver.ps1
```

The installer downloads the exact release ZIP over HTTPS, verifies its pinned SHA-256 before extraction, and verifies every installed file against that archive on every run. A mismatched or partial cache fails closed with a diagnostic. It never selects `latest`. See [the dependency lock](../dependencies/cua-driver.lock.json) for the complete pin.

| Item | Pin |
|---|---|
| Release | `cua-driver-rs-v0.28.2` |
| Source revision | `fc188250b4ca8549b8e61f937fdb1fb560770e86` |
| Archive | `cua-driver-rs-0.28.2-windows-x86_64.zip` |
| Archive SHA-256 | `3c1fcf10ff9513b94e4af78ad6a216ab62aa95b2c9a3b70dfbdba9f04e021533` |
| Executable SHA-256 | `dbbd52d75759900155fbf3d5f0a13c759a12d06ef17338b88b3f2b8b9c1ef8dc` |

The archive checksum agrees with the official release notes, GitHub asset digest, and release manifest. The executable hash was derived from that verified archive. Authenticode verification on this PC reported `Valid`, signed by `Cua AI, Inc.`. Upstream describes this plain SemVer release as stable; the GitHub prerelease label keeps this monorepo's repository-wide Latest pointer from switching products.

## Local layout and lifecycle

All downloaded binaries, cache files, test results, logs, and screenshots live beneath the already Git-ignored `.runtime/` directory:

```text
.runtime/cua-driver/
  downloads/cua-driver-rs-0.28.2-windows-x86_64.zip
  packages/cua-driver-rs-0.28.2-windows-x86_64/
    cua-driver.exe
    cua-driver-uia.exe
    cua-cursor-theme.exe
    cua_driver_sdk.dll
    cua_driver_node_runtime.node
    cua_driver_abi.h
  smoke/<UTC run timestamp>/
    result.json, doctor.json, windows.json, capture.json, fixture.png, *.log
```

The companion files are the contents of the official archive; no language SDK was installed. No PATH change, scheduled task, registry change, global package environment, or agent MCP registration is made.

The official installer route is `https://cua.ai/driver/install.ps1`. Inspection of the pinned installer found that it repairs old daemons and can retarget an existing `cua-driver-serve` scheduled task even with `-NoAutoStart`. This project's small installer therefore extracts the **same official release archive** without executing the upstream system-install workflow. This is a project-local adaptation of the upstream archive route, not a separate CUA distribution.

Verification starts the documented `cua-driver.exe mcp --direct --no-overlay` interface through redirected standard input/output. This owns a private runtime with no listening network port or shared named pipe. `initialize`, `tools/list`, and `tools/call` use newline-delimited JSON-RPC. The test closes stdin and checks a clean process exit; nothing is left running. To start and exercise the driver again, rerun the smoke script.

The helper applies these settings **only to child driver processes**:

```text
CUA_DRIVER_RS_TELEMETRY_ENABLED=false
CUA_DRIVER_RS_UPDATE_CHECK=false
CUA_DRIVER_TELEMETRY_HOME=<worktree>/.runtime/cua-driver/telemetry
CUA_DRIVER_PERMISSION_MODE=standard
```

Telemetry and update checks are separate controls. Both are disabled before the first driver invocation. No paid API is used. These are verified configuration controls, not a packet-capture audit of every possible driver feature.

Upstream 0.28.2 still **reads** general defaults from the user's `.cua-driver/config.json`; `CUA_DRIVER_RS_HOME` does not relocate that configuration. This workflow never calls a configuration setter. Existing global image-size defaults can affect smoke results; diagnose them without silently changing them. The directory was absent on this PC before and after verification. Direct MCP also avoids `serve`'s shared `%LOCALAPPDATA%/cua-driver/cua-driver.pid` file. Computer History is not enabled.

## What the smoke check verifies

1. The pinned executable starts, reports its version, and reports telemetry disabled.
2. `doctor --json` checks desktop access and UI Automation; its full probe report is retained, including warnings.
3. MCP initializes, advertises `list_windows` and `get_window_state`, and returns the expected server version.
4. A dedicated, non-activating WinForms fixture publishes its own PID/HWND. `list_windows` filters to that PID and must return the same window identity.
5. `get_window_state` captures that exact PID/HWND with `include_accessibility_tree:false` and `include_screenshot:true`. The test never captures the desktop or another application.
6. The returned PNG has matching metadata, plausible dimensions, both known colored panels, and no large black padding. Driver and fixture must both stop cleanly.

No click, typing, key, focus, move, scroll, clipboard, or application-action tool is invoked. The fixture closes through its own timer after a local sentinel file is created; it also has a 90-second fallback lifetime. Captured images and full JSON results remain ignored by Git.

Run the capture check in the normal interactive desktop, not Session 0 or the isolated Codex sandbox desktop. The sandbox attempt reached the executable but its UI Automation enumeration timed out; the normal desktop run succeeded. This is an execution-environment requirement, not an additional dependency. The fixture explicitly enables PerMonitorV2 DPI awareness because the initial DPI-unaware fixture produced a smaller image inside a black-padded bitmap.

See [verification results](CUA_DRIVER_VERIFICATION.md) for the measured run. This check establishes this driver's startup, interface, and capture of one native fixture. It does not validate arbitrary apps, background mouse/keyboard delivery, coordinate transforms for actions, detector/OCR/Windows ML/JEV integration, performance, or the full assistant loop. The observed window bounds and PNG dimensions differ slightly at the frame; later adapter work must preserve and validate that geometry.

## Primary sources inspected

- [Official installation documentation](https://cua.ai/docs/how-to-guides/driver/install)
- [0.28.2 release, assets, checksums, and stable-channel explanation](https://github.com/trycua/cua/releases/tag/cua-driver-rs-v0.28.2)
- [Pinned Windows installer](https://github.com/trycua/cua/blob/fc188250b4ca8549b8e61f937fdb1fb560770e86/libs/cua-driver/scripts/install.ps1)
- [Pinned static C-runtime configuration](https://github.com/trycua/cua/blob/fc188250b4ca8549b8e61f937fdb1fb560770e86/libs/cua-driver/rust/.cargo/config.toml)
- [CLI interface](https://cua.ai/docs/reference/cua-driver/cli-reference), [Windows tool reference](https://cua.ai/docs/reference/cua-driver/mcp-tools-windows), [interface contracts](https://cua.ai/docs/reference/cua-driver/contracts)
- [Telemetry and update controls](https://cua.ai/docs/reference/cua-driver/telemetry)
- [Pinned general configuration resolver](https://github.com/trycua/cua/blob/fc188250b4ca8549b8e61f937fdb1fb560770e86/libs/cua-driver/rust/crates/pip-preview/src/lib.rs)
