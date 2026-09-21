"""Disposable, bounded window inventory and UIA observation processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .execution import ExecutionError


ROOT = Path(__file__).resolve().parents[2]


def _read_ui_request(arguments, *, root=None, python=None, timeout=8, cancelled=None):
    root = Path(root) if root else ROOT
    python = str(python or root / ".runtime/uia-venv/Scripts/python.exe")
    if not Path(python).is_file():
        raise ExecutionError("Install the UIA viewer runtime before starting execution.")
    cancelled = cancelled or (lambda: False)
    if cancelled():
        raise InterruptedError
    # The observer is killed along with its parent even if a provider wedges.
    from .cua_driver import KillOnCloseJob
    job = KillOnCloseJob()
    process = None
    try:
        process = subprocess.Popen([python, "-I", str(root / "scripts/uia-observe.py"), *arguments],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        job.assign(process)
        deadline = time.monotonic() + timeout + 3
        while True:
            if cancelled():
                raise InterruptedError
            if time.monotonic() >= deadline:
                raise ExecutionError("UIA observation exceeded its hard deadline.")
            try:
                output, _ = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode or len(output) > 8 * 1024 * 1024:
            raise ExecutionError("UIA observation failed or exceeded the output budget.")
        try:
            return json.loads(output.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ExecutionError("UIA observation returned invalid data.") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
        job.close()


def observe_target(target, *, root=None, python=None, max_elements=1000, timeout=8, cancelled=None):
    return _read_ui_request(["observe", "--hwnd", str(target["hwnd"]), "--pid", str(target["pid"]),
                             "--max-elements", str(max_elements), "--timeout", str(timeout)],
                            root=root, python=python, timeout=timeout, cancelled=cancelled)


def observe_windows(*, root=None, python=None, cancelled=None):
    # Use the same disposable process, job ownership, output ceiling and cancel
    # polling as control observation. This command never initializes a UIA tree.
    return _read_ui_request(["windows"], root=root, python=python, timeout=6, cancelled=cancelled)
