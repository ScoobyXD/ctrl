"""Deliver real native CUA input only to an owned, synthetic Windows fixture.

Uses real UIA observations, SelectionSearch and the pinned CUA Driver. Jev's
responses are deterministic local fixtures: no credentials, network or paid
model requests are used. Result evidence stays in Git-ignored .runtime/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctrl_loop import jev
from ctrl_loop.selection import SelectionSearch


class FixtureDecider:
    """Synthetic Jev response envelope, bound to every actual selector request."""

    def __init__(self, option_id):
        self.option_id = option_id
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, payload, *, request_id, capture_id):
        with self.lock:
            self.calls += 1
            sequence = self.calls
        answers = {}
        for question_id, question in payload["questions"].items():
            criteria = question["criteria"]
            if question_id.startswith("classify_"):
                index = int(question_id.removeprefix("classify_"))
                candidate = payload["state"]["candidates"][index]
                choice = "promising" if candidate["id"] == self.option_id else "unlikely"
            else:
                choice = self.option_id if self.option_id in criteria else "__none__"
            probabilities = {key: (1.0 if key == choice else 0.0) for key in criteria}
            answers[question_id] = {"type": "choice", "choice": choice,
                "confidence": 1.0, "probabilities": probabilities}
        result = {"model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
            "provider_request_id": f"gen-fixture-offline-{sequence}", "latency_ms": 0.0,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
            "answers": answers,
            "local_request": jev.request_binding(payload, request_id=request_id, capture_id=capture_id)}
        return jev.validate_decision(result, payload["questions"])


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def wait_json(path, predicate=lambda value: True, *, timeout=10, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read_json(path)
        if value is not None and predicate(value):
            return value
        if process is not None and process.poll() is not None:
            raise RuntimeError("Owned fixture exited; inspect fixture.stderr.log.")
        time.sleep(0.05)
    raise RuntimeError(f"Fixture evidence timed out: {path.name}")


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
        default=ROOT / ".runtime" / "cua-execution" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run in a logged-in Windows interactive desktop with the UIA Python environment.")
    from ctrl_loop.cua_driver import NativeCuaExecutor
    from ctrl_loop.execution import ExecutionLoop
    from ctrl_loop.execution_runtime import observe_target

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    ready, state_path, close = (output / name for name in ("fixture.json", "state.json", "close-fixture"))
    report = {"passed": False, "fixture_only": True, "model_requests": 0,
        "selection": "Actual SelectionSearch with deterministic offline Jev responses",
        "transport": "Pinned native CUA Driver MCP stdio --direct", "checks": {}}
    fixture = None
    executor = None
    native_calls = []
    with (output / "fixture.stdout.log").open("w", encoding="utf-8") as stdout, \
            (output / "fixture.stderr.log").open("w", encoding="utf-8") as stderr:
        try:
            host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            fixture = subprocess.Popen([str(host), "-NoProfile", "-NonInteractive", "-STA",
                "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/cua-execution-fixture.ps1"),
                "-ReadyFile", str(ready), "-StateFile", str(state_path), "-CloseFile", str(close)],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW, cwd=ROOT)
            identity = wait_json(ready, process=fixture)
            assert identity["pid"] == fixture.pid, "Fixture PID differs from owned process"
            target = {"hwnd": identity["window_id"], "pid": identity["pid"], "title": identity["title"]}
            report["target"] = target
            initial = wait_json(state_path, process=fixture)
            assert initial["selected_tab"] == "Home tab" and initial["bookmark_clicks"] == 0
            assert initial["note_text"] == "Original note"
            executor = NativeCuaExecutor(root=ROOT)
            native_call = executor.transport.call

            def record_native_call(name, arguments, cancelled=None):
                result = native_call(name, arguments, cancelled)
                native_calls.append({"tool": name, "arguments": arguments, "result": result})
                return result

            executor.transport.call = record_native_call
            fake_calls = []

            def selector(snapshot, goal, history):
                if goal == "Set the fixture note to the supplied explicit text.":
                    name, operation = "Fixture note", "set_value"
                elif goal == "Return to Home tab, then stop before another action.":
                    name, operation = "Home tab", "select"
                else:
                    x_tabs = [e for e in snapshot["elements"]
                              if e.get("name") == "X tab" and e.get("role") == "TabItem"]
                    assert len(x_tabs) == 1, "Expected exactly one owned X tab"
                    name, operation = ("Open Bookmarks", "activate") if x_tabs[0]["state"]["selected"] else ("X tab", "select")
                options = [o for o in snapshot["options"] if o["label"] == name and o["operation"] == operation]
                assert len(options) == 1, f"Fixture action missing or ambiguous: {name} / {operation}"
                decider = FixtureDecider(options[0]["id"])
                selected = SelectionSearch(snapshot, goal, history=history).run(decider)
                fake_calls.append(decider.calls)
                return selected

            def run(name, goal, *, steps, arguments=None, cancel_after_observation=False):
                events = []
                stopped = False

                def record_event(event, data):
                    nonlocal stopped
                    events.append({"event": event, "data": data})
                    if cancel_after_observation and event == "action" and "observed_change" in data:
                        stopped = True

                request = {"target": target, "goal": goal, "run_id": name,
                    "max_steps": steps, "max_seconds": 45, "max_elements": 1000,
                    "arguments": arguments or {"bindings": []}}
                loop = ExecutionLoop(observer=lambda target: observe_target(target, cancelled=lambda: stopped),
                    selector=selector, executor=executor, emit=record_event, cancelled=lambda: stopped)
                result = loop.run(request)
                save(output / (name + ".json"), {"request": request, "result": result, "events": events})
                return result

            navigation = run("navigation", "Select the existing X tab and open its bookmarks.", steps=2)
            assert navigation["status"] == "limit_reached", navigation
            assert navigation["input_actions"] == 2 and navigation["attempted_actions"] == 2
            assert navigation["goal_completed"] is False
            assert all(action["observed_change"] for action in navigation["actions"])
            navigated = wait_json(state_path, lambda s: s["bookmarks_open"], process=fixture)
            assert navigated["selected_tab"] == "X tab"
            assert navigated["tab_selections"] == 1 and navigated["bookmark_clicks"] == 1
            assert [event["name"] for event in navigated["events"]][:2] == ["tab:X tab", "bookmarks:click"]
            report["checks"]["two_step_navigation"] = {"passed": True, "result_status": navigation["status"],
                "tab_selections": navigated["tab_selections"], "bookmark_clicks": navigated["bookmark_clicks"]}

            note_goal = "Set the fixture note to the supplied explicit text."
            missing = run("missing-text", note_goal, steps=1)
            assert missing["status"] == "needs_arguments", missing
            assert missing["input_actions"] == 0 and missing["attempted_actions"] == 0
            unchanged = wait_json(state_path, process=fixture)
            assert unchanged["note_text"] == "Original note" and unchanged["text_changes"] == 0
            report["checks"]["missing_text_no_input"] = {"passed": True, "result_status": missing["status"]}

            explicit_text = "CUA delivered this explicit fixture text."
            binding = {"operation": "set_value", "automation_id": "fixture_note", "value": explicit_text}
            typed = run("explicit-text", note_goal, steps=1, arguments={"bindings": [binding]})
            assert typed["status"] == "limit_reached", typed
            assert typed["input_actions"] == 1 and typed["attempted_actions"] == 1
            assert typed["actions"][0]["observed_change"] is True
            final = wait_json(state_path, lambda s: s["note_text"] == explicit_text, process=fixture)
            assert final["text_changes"] > 0
            observed = observe_target(target)
            notes = [e for e in observed["elements"] if e.get("name") == "Fixture note"]
            assert len(notes) == 1 and notes[0]["value"] == explicit_text
            assert any(e.get("name") == "Bookmarks opened: saved fixture page" for e in observed["elements"])
            save(output / "final-observation.json", observed)
            save(output / "final-fixture-state.json", final)
            report["checks"]["explicit_text_delivery"] = {"passed": True, "result_status": typed["status"],
                "fixture_text_matches": True, "fresh_uia_value_matches": True, "text_changes": final["text_changes"]}

            cancelled = run("cancel-between-steps", "Return to Home tab, then stop before another action.",
                steps=2, cancel_after_observation=True)
            assert cancelled["status"] == "cancelled", cancelled
            assert cancelled["input_actions"] == 1 and cancelled["attempted_actions"] == 1
            stopped_state = wait_json(state_path, lambda s: s["selected_tab"] == "Home tab", process=fixture)
            assert stopped_state["tab_selections"] == 2 and stopped_state["bookmark_clicks"] == 1
            assert stopped_state["text_changes"] == 1 and stopped_state["note_text"] == explicit_text
            save(output / "cancel-fixture-state.json", stopped_state)
            report["checks"]["cancel_between_steps"] = {"passed": True, "result_status": cancelled["status"],
                "delivered_actions": cancelled["input_actions"], "step_limit": 2}
            report["offline_response_count"] = sum(fake_calls)
            report["delivered_actions"] = navigation["input_actions"] + typed["input_actions"] + cancelled["input_actions"]
            report["passed"] = True
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            save(output / "native-calls.json", native_calls)
            if executor is not None:
                driver_process = executor.transport.process
                try:
                    executor.close()
                    if driver_process is not None:
                        report["driver_exit_code"] = driver_process.poll()
                        if report["driver_exit_code"] is None:
                            report["passed"] = False
                            report["driver_cleanup_error"] = "Owned driver is still running."
                except Exception as error:
                    report["passed"] = False
                    report["driver_cleanup_error"] = f"{type(error).__name__}: {error}"
            if fixture is not None:
                close.touch()
                try:
                    fixture.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.wait(timeout=5)
                    report["passed"] = False
                    report["fixture_forced_cleanup"] = True
                report["fixture_exit_code"] = fixture.returncode
                if fixture.returncode != 0:
                    report["passed"] = False
            save(output / "result.json", report)
    print(json.dumps(dict(report, output_dir=str(output))), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
