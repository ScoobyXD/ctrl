"""Start this checkout's observation-only UIA viewer without content logging."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


if __name__ == "__main__":
    try:
        from ctrl_loop.uia_viewer import main

        raise SystemExit(main())
    except Exception:
        # pythonw has no console. Do not leak exception text that could contain
        # observed target contents into persistent logs or diagnostic dialogs.
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "The UIA viewer could not start or encountered an unexpected error. "
            "Check the local installation and docs/UIA_VIEWER.md. "
            "No UI observation contents were logged.",
            "CTRL UIA viewer",
            0x10,
        )
        raise SystemExit(1)
