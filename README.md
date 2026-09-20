# CTRL Loop

CTRL now has a Windows desktop **Capture** viewer. Click once to capture the whole primary monitor, analyze that fixed screenshot with the local UI-trained YOLO detector, and display green outlines inside the app. CTRL briefly hides during acquisition, then restores. A new capture occurs only when you click again.

```powershell
pwsh -NoProfile -File ./scripts/install-capture-viewer.ps1 -Python '<path to CPython 3.12.13 x64 base/python.exe>'
pwsh -NoProfile -File ./scripts/start-capture-viewer.ps1
```

See [capture viewer setup, architecture, use and validation](docs/CAPTURE_VIEWER.md), including the optional verified-artifact reuse command for a new worktree. This milestone requires no cloud account or credentials. It has no OCR, English detection labels, JEV decisions, probability panel, input automation, periodic capture or desktop overlay.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the current milestone and future assistant stages. The previous implementation's recovery history and dependency provenance remain preserved.

## Dependency verification and future components

Native Windows CUA Driver **0.28.2** provides capture. The older selected-window fixture is useful dependency evidence, separate from the new primary-screen viewer:

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

Models and environments stay under ignored `.runtime/`; explicit verification evidence stays under ignored `.ctrl/`. Normal viewer captures remain in memory. OCR, input delivery, and the full assistant loop remain future work. The selected detector weights are **AGPL-3.0**, separately from this repository's MIT license.
