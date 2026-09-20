"""Launch only the capture application in this checkout, independent of cwd."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ctrl_loop.capture_app import main

if __name__ == "__main__":
    raise SystemExit(main())
