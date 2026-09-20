"""Explicitly start bounded live UIA → Jev → native CUA execution.

Input: one JSON line on stdin. Further {cancel:true,run_id:...} lines cancel.
Output: bounded JSON-line events, each bound to the user's execution run ID.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctrl_loop.execution import ExecutionError, ExecutionLoop, validate_request
from ctrl_loop.execution_runtime import observe_target


def parse_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def read_line(limit):
    # A daemon waiting on BufferedReader.readline can retain its Python I/O lock
    # during interpreter shutdown while the GUI deliberately keeps stdin open.
    # Raw OS reads let the helper exit cleanly without waiting for another line.
    line = bytearray()
    while len(line) < limit:
        byte = os.read(sys.stdin.fileno(), 1)
        if not byte:
            break
        line.extend(byte)
        if byte == b"\n":
            break
    return bytes(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin", action="store_true", required=True)
    parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    run_id, loop = None, None
    lock = threading.Lock()
    done = threading.Event()
    terminal_sent = threading.Event()
    cancel = threading.Event()

    def emit(event, data):
        with lock:
            if done.is_set() or terminal_sent.is_set():
                return
            if event in ("result", "error"):
                terminal_sent.set()
            record = {"event": event, "run_id": run_id, "data": data}
            wire = json.dumps(record, ensure_ascii=True, allow_nan=False)
            if len(wire) > 8 * 1024 * 1024:
                raise ExecutionError("Execution event exceeded the output budget.")
            print(wire, flush=True)

    def hard_stop(status):
        if done.is_set():
            return
        cancel.set()
        # Even a disconnected/blocked output consumer cannot extend the limit.
        kill = threading.Timer(1, lambda: os._exit(2))
        kill.daemon = True
        kill.start()
        result = {"status": status, "reason": "The execution helper reached its hard stop. In-flight input may have occurred; inspect the target before restarting.",
                  "goal_completed": False, "input_actions": loop.input_actions if loop else 0,
                  "attempted_actions": loop.attempted_actions if loop else 0,
                  "actions": loop.actions if loop else [], "in_flight_outcome": "unknown"}
        emit("result", result)
        os._exit(2)

    def read_cancel():
        while not done.is_set():
            raw = read_line(1024)
            if not raw:
                return
            try:
                message = parse_json(raw)
                if message == {"cancel": True, "run_id": run_id}:
                    cancel.set()
                    guard = threading.Timer(1, lambda: hard_stop("cancelled"))
                    guard.daemon = True
                    guard.start()
                    return
            except (ValueError, UnicodeError):
                continue

    timer = None
    try:
        raw = read_line(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ExecutionError("Execution request exceeds the input limit.")
        parsed = parse_json(raw)
        # Bind validation diagnostics to the caller too, so the GUI can show
        # missing operands instead of mistaking the error for an old run.
        if (type(parsed) is dict and type(parsed.get("run_id")) is str
                and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", parsed["run_id"])):
            run_id = parsed["run_id"]
        request = validate_request(parsed)
        run_id = request["run_id"]
        from ctrl_loop.cua_driver import NativeCuaExecutor
        from ctrl_loop.credentials import CredentialError, get_openrouter_key
        from ctrl_loop.jev import DecisionError, decide
        from ctrl_loop.selection import SelectionError, SelectionSearch

        threading.Thread(target=read_cancel, daemon=True).start()
        timer = threading.Timer(request["max_seconds"] + 1, lambda: hard_stop("limit_reached"))
        timer.daemon = True
        timer.start()
        key = None

        def select(snapshot, goal, history):
            nonlocal key
            try:
                search = SelectionSearch(snapshot, goal, history=history)
                if key is None:
                    if loop.should_stop():
                        raise InterruptedError
                    emit("progress", {"stage": "credentials", "status": "Reading the existing OpenRouter key from AWS Secrets Manager."})
                    key = get_openrouter_key()
                return search.run(lambda payload, **ids: decide(payload, key, **ids),
                                  cancelled=loop.should_stop,
                                  on_progress=lambda data: emit("progress", data))
            except CredentialError as error:
                raise ExecutionError(str(error)) from None
            except DecisionError as error:
                raise ExecutionError("Jev selection failed (" + error.category + "). No action was dispatched for this selection.") from None
            except SelectionError:
                raise ExecutionError("Jev selection rejected the current snapshot or request bounds.") from None

        with NativeCuaExecutor(root=ROOT) as executor:
            loop = ExecutionLoop(
                lambda target: observe_target(target, max_elements=request["max_elements"], cancelled=loop.should_stop),
                select, executor, emit=emit, cancelled=cancel.is_set)
            result = loop.run(request)
        key = None
        return 1 if result["status"] in ("failed", "stale_target") else 0
    except (ExecutionError, ValueError, UnicodeError) as error:
        message = str(error) if isinstance(error, ExecutionError) else "Invalid execution input JSON."
        emit("error", {"message": message})
    except Exception:
        emit("error", {"message": "Execution could not start. Check the pinned driver and local UIA/Jev installations."})
    finally:
        done.set()
        if timer:
            timer.cancel()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
