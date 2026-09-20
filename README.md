# CTRL Loop

## Separate UIA control-options viewer

The UIA control-options viewer is available through **separate** launchers. Choose an application window, click **Observe / Refresh**, and search its native accessible controls and supported operation candidates. Observation and row selection run locally. The optional **Jev selection** preview recommends an action without executing it. The separate **CUA execution** tab starts a bounded observe → Jev select → validate → native input → fresh observation loop, with Stop and concrete action receipts. Remote requests start only when you explicitly start a preview or execution run.

```powershell
pwsh -NoProfile -File ./scripts/install-uia-viewer.ps1 -Python '<CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/start-uia-viewer.ps1
# Optional owned native + Chrome fixtures and viewer:
pwsh -NoProfile -File ./scripts/start-uia-demo.ps1
```

See the [UIA viewer guide](docs/UIA_VIEWER.md) for the Window picker, tabs, counts, search, export, scrolling behavior, setup and validation. The existing capture implementation and its launchers below are preserved.

See [multi-stage Jev selection](docs/JEV_SELECTION.md) for setup, the classification and recovery algorithm, request budgets, and offline verification.

See [CUA execution](docs/CUA_EXECUTION.md) for setup, supported actions, explicit text/number bindings, freshness checks, cancellation, CLI use and desktop verification.

## Existing Capture viewer

CTRL now has a Windows desktop **Capture** viewer. Click once to capture the whole primary monitor, analyze that fixed screenshot with the local UI-trained YOLO detector, and display green outlines inside the app. CTRL briefly hides during acquisition, then restores. A new capture occurs only when you click again.

```powershell
pwsh -NoProfile -File ./scripts/install-capture-viewer.ps1 -Python '<path to CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/start-capture-viewer.ps1
```

See [capture viewer setup, architecture, use and validation](docs/CAPTURE_VIEWER.md), including the optional verified-artifact reuse command for a new worktree. This milestone requires no cloud account or credentials. It has no OCR, English detection labels, JEV decisions, probability panel, input automation, periodic capture or desktop overlay.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the current milestone and future assistant stages. The previous implementation's recovery history and dependency provenance remain preserved.

## Dependency verification and future components

Native Windows CUA Driver **0.28.2** provides capture and the UIA viewer's supported native input delivery. The older selected-window fixture remains capture-only dependency evidence:

```powershell
pwsh -NoProfile -File ./scripts/install-cua-driver.ps1
pwsh -NoProfile -File ./scripts/test-cua-driver.ps1
```

See [CUA Driver setup](docs/CUA_DRIVER.md) and [fixture verification](docs/CUA_DRIVER_VERIFICATION.md).

The local detector uses **Windows ML's Python projection and a UI-trained YOLO11m model**, with an evaluated CPU comparison against its original PyTorch checkpoint. See [local perception setup](docs/LOCAL_PERCEPTION.md) and [measured dependency verification](docs/LOCAL_PERCEPTION_VERIFICATION.md).

```powershell
pwsh -NoProfile -File ./scripts/install-local-perception.ps1 -Python '<path to unpackaged Python 3.12.13 x64/python.exe>'
pwsh -NoProfile -File ./scripts/test-local-perception.ps1
```

A separate **JEV decision-only preview through OpenRouter** remains available for future assistant work; it is not imported or called by the capture app. See [JEV setup and preview](docs/JEV.md) and [verification](docs/JEV_VERIFICATION.md). Its default preview is offline; `--live` makes one decision request with Choice and two Noul questions, using the existing AWS Secrets Manager credential. Neither mode executes input.

```powershell
pwsh -NoProfile -File ./scripts/install-jev.ps1 -Python '<path to unpackaged Python 3.12.13 x64/python.exe>'
& ./.runtime/jev-venv/Scripts/python.exe ./scripts/jev-preview.py
```

Models and environments stay under ignored `.runtime/`; explicit verification evidence stays under ignored `.ctrl/` or `.runtime/`. Normal viewer observations remain in memory. OCR and general goal-completion verification remain future work. The selected detector weights are **AGPL-3.0**, separately from this repository's MIT license.
