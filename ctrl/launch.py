"""Start the local server without a console window, including paths containing brackets."""

import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    state = root / ".ctrl"
    state.mkdir(exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (state / "server.log").open("w", encoding="utf-8") as stdout:
        with (state / "server-error.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-m", "ctrl.server"], cwd=root,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=flags, start_new_session=os.name != "nt",
            )
    (state / "server.pid").write_text(str(process.pid), encoding="utf-8")
    print(process.pid)


if __name__ == "__main__":
    main()
