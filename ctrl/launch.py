"""Start the local server without a console window, including paths containing brackets."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def stop_generation(root):
    try:
        return (root / ".ctrl" / "stop-generation").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "initial"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-generation")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    state = root / ".ctrl"
    state.mkdir(exist_ok=True)
    generation = args.stop_generation if args.stop_generation is not None else stop_generation(root)
    if generation != stop_generation(root):
        raise SystemExit("Startup cancelled by Stop.")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (state / "server.log").open("w", encoding="utf-8") as stdout:
        with (state / "server-error.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-m", "ctrl.server"], cwd=root,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=flags, start_new_session=os.name != "nt",
                env={**os.environ, "CTRL_START_GENERATION": generation},
            )
    (state / "server.pid").write_text(str(process.pid), encoding="utf-8")
    # Stop may arrive between the first check and publishing the new PID. The
    # server independently watches the same generation and exits without resuming a task.
    if generation != stop_generation(root):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # Stop's process fallback can still use the published PID.
        else:
            pid_file = state / "server.pid"
            if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(process.pid):
                pid_file.unlink()
        raise SystemExit("Startup cancelled by Stop.")
    print(process.pid)


if __name__ == "__main__":
    main()
