"""One local JSON response, no target-content logs or action endpoints."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.uia_contract import new_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["windows", "observe"])
    parser.add_argument("--hwnd", type=int, default=0)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--max-elements", type=int, default=1000)
    parser.add_argument("--max-depth", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    if not 1 <= args.max_elements <= 5000 or not 1 <= args.max_depth <= 64 or not 0.1 <= args.timeout <= 30:
        parser.error("budgets: 1–5000 elements, 1–64 depth, 0.1–30 seconds")
    sys.stdout.reconfigure(encoding="utf-8")
    lock = threading.Lock()
    emitted = False
    def emit(result):
        nonlocal emitted
        with lock:
            if emitted:
                return False
            emitted = True
            sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False) + "\n")
            sys.stdout.flush()
            return True
    def watchdog():
        result = new_snapshot(args.hwnd, args.pid)
        result.update(status="error", error="UIA provider exceeded the hard deadline. Try a fresh observation.", truncated=True)
        if args.command == "windows":
            result = {"windows": [], "error": "Window enumeration exceeded the deadline."}
        if emit(result):
            os._exit(2)
    timer = threading.Timer(args.timeout + 1.5 if args.command == "observe" else 6, watchdog)
    timer.daemon = True
    timer.start()
    try:
        from ctrl_loop.uia_backend import UIAReader, list_windows
        if args.command == "windows":
            result = list_windows()
        else:
            result = UIAReader().observe(args.hwnd, args.pid, args.max_elements, args.timeout, args.max_depth)
        # Keep IPC bounded even for providers that repeat very long labels everywhere.
        while len(json.dumps(result, ensure_ascii=True)) > 7_500_000 and result.get("elements"):
            result["elements"] = result["elements"][:len(result["elements"]) // 2]
            ids = {e["id"] for e in result["elements"]}
            result["options"] = [o for o in result["options"] if o["element_id"] in ids]
            result.update(truncated=True, status="partial")
            result["warnings"].append("Serialized output budget reached.")
        emit(result)
        timer.cancel()
    except Exception:
        timer.cancel()
        result = new_snapshot(args.hwnd, args.pid)
        result.update(status="error", error="UIA helper initialization failed. Check the local installation and interactive desktop.")
        if args.command == "windows":
            result = {"windows": [], "error": result["error"]}
        emit(result)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
