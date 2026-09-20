# CTRL Loop

A fresh Windows visual assistant project. The previous local implementation has been moved into a recovery backup; Git history and the configured remote are preserved.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the agreed design, component responsibilities, remaining choices, and implementation stages.

The intended loop is:

1. CUA Driver captures the selected window.
2. Local UI-trained YOLO and a separate text reader identify candidate controls.
3. Application code builds English action descriptions inside structured data.
4. Online JEV chooses among those actions.
5. Application code validates the target, sends the action through CUA Driver, and observes the result.

Native Windows CUA Driver **0.28.2** is the first dependency. Install it locally and run the read-only fixture capture with PowerShell 7.2+:

```powershell
pwsh -NoProfile -File ./scripts/install-cua-driver.ps1
pwsh -NoProfile -File ./scripts/test-cua-driver.ps1
```

See [CUA Driver setup](docs/CUA_DRIVER.md) and [verification results](docs/CUA_DRIVER_VERIFICATION.md). Runtime files and captures stay under Git-ignored `.runtime/`. The application, perception components, JEV integration, and input delivery are not implemented or validated yet.
