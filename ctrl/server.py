"""Loopback-only [CTRL] app with a single interruptible agent worker."""

import argparse
import atexit
import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from jev_ultrafast.browser import StalePage, validate_navigation_urls
from jev_ultrafast.model import supplied_values
from jev_ultrafast.navigation import navigation_urls
from jev_ultrafast.questions import MAX_STEPS

from . import browser_connection
from .credentials import CredentialError
from .diagnostics import MODEL, check_openrouter

STATIC = Path(__file__).with_name("static")
TERMINAL = {"done", "blocked", "needs_input", "error", "paused", "cancelled"}


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.task_cancel = threading.Event()
        self.shutdown = threading.Event()
        self.closed = False
        self.agent = None
        self.worker = None
        self.busy = False
        self.running = False
        self.error = None
        self.credentials = {"status": "unchecked", "message": "Check the AWS credential and Jev connection."}
        self.tabs = []
        self.view = {"status": "idle", "page": None, "goal": "", "history": [], "decision": None, "values": []}

    def state(self, *, discover_tabs=True):
        browser = browser_connection.status()
        if discover_tabs and browser.get("connected") and not self.tabs:
            try:
                self.tabs = browser_connection.list_tabs()
            except RuntimeError:
                pass
        with self.lock:
            agent = self.agent
            return {
                **self.view,
                "status": "cancelling" if self.task_cancel.is_set() and self.busy else self.view["status"],
                **(agent.trace_state() if agent else {"trace_summaries": [], "latest_trace_id": None}),
                "busy": self.busy,
                "running": self.running,
                "error": self.error,
                "credentials": dict(self.credentials),
                "browser": browser,
                "tabs": list(self.tabs),
                "model": MODEL,
                "max_steps": self.view.get("max_steps", MAX_STEPS),
            }

    def control_state(self):
        """Small control-panel status; never reads Chrome or returns page/task text."""
        with self.lock:
            return {
                "status": "stopping" if self.shutdown.is_set() else self.view["status"],
                "busy": self.busy, "running": self.running,
                "action_count": len(self.view.get("history", [])),
                "current_host": urlsplit((self.view.get("page") or {}).get("url", "")).hostname or "",
            }

    def stop(self):
        # The latch prevents a concurrent/new Run from clearing the cancellation signal.
        with self.lock:
            self.shutdown.set()
            self.cancel.set()
            self.running = False
        self.pause()
        return self.control_state()

    def capture(self):
        if self.agent is None:
            return
        snapshot = self.agent.snapshot()
        # The inspector needs decision evidence, not the full model request or every historical response.
        snapshot.pop("decisions", None)
        for field in ("decision", "last_decision"):
            decision = snapshot.get(field)
            if decision:
                snapshot[field] = {
                    key: value for key, value in decision.items() if key not in {"request", "raw_answers", "audit"}
                }
                if field == "decision":
                    snapshot[field]["fingerprint"] = snapshot["page"]["fingerprint"]
        with self.lock:
            self.view = snapshot
            self.view["selected_tab"] = getattr(self.agent.browser, "target", None)
            context = (snapshot.get("page") or {}).get("browser_context")
            if context is not None and "tabs" in context:
                self.tabs = list(context["tabs"])

    def refresh_tabs(self):
        tabs = browser_connection.list_tabs()
        with self.lock:
            self.tabs = tabs
        return self.state()

    def pause(self):
        # This must not wait for the worker lock or the in-flight model response.
        with self.lock:
            self.cancel.set()
            self.running = False
            if self.task_cancel.is_set():
                return self.state(discover_tabs=False)
            if not self.busy and self.agent and self.agent.state["status"] not in TERMINAL:
                self.agent.finish_trace("paused", "Paused; preview discarded")
                self.agent.state["status"] = "paused"
                self.agent.state["decision"] = None
                self.capture()
            if self.view["status"] not in {"idle", "done", "blocked", "needs_input", "error"}:
                self.view["status"] = "pausing" if self.busy else "paused"
        return self.state(discover_tabs=False)

    def cancel_task(self):
        """Latch task cancellation immediately; the worker cannot resume it."""
        with self.lock:
            self.cancel.set()
            self.task_cancel.set()
            self.running = False
            if self.busy:
                self.view["status"] = "cancelling"
            elif self.agent:
                self.agent.cancel_run()
                self.capture()
            else:
                self.view["status"] = "cancelled"
        return self.state(discover_tabs=False)

    def trace(self, identifier):
        with self.lock:
            agent = self.agent
            return agent.trace_store().get(identifier) if agent else None

    def submit(self, name, body):
        if name == "pause":
            return self.pause()
        if name == "cancel":
            return self.cancel_task()
        if name not in {"connect", "credentials", "start", "run", "step", "predict", "act", "reset", "supply_values"}:
            raise ValueError("Unknown command")
        if name == "start":
            self.validate_start(body)
        with self.lock:
            if self.shutdown.is_set():
                raise RuntimeError("The agent is off. Restart it from the CTRL Control Panel.")
            if self.busy:
                raise RuntimeError("An operation is already in progress. Pause it or wait for it to finish.")
            if name in {"run", "step", "predict", "act", "supply_values"} and self.agent is None:
                raise ValueError("Choose a Chrome tab and enter a task first.")
            if name in {"run", "step", "predict", "act", "supply_values"} and self.task_cancel.is_set():
                raise ValueError("This task was cancelled. Start a new task to run again.")
            self.busy = True
            self.error = None
            if name in {"start", "reset"}:
                self.task_cancel.clear()
            if not self.task_cancel.is_set():
                self.cancel.clear()
            self.running = name == "run" or (name == "start" and body.get("run") is True)
            self.worker = threading.Thread(target=self.work, args=(name, body), daemon=True, name="ctrl-agent")
            self.worker.start()
        return self.state()

    @staticmethod
    def validate_start(body):
        goal, target, values = body.get("goal"), body.get("target_id"), body.get("values", [])
        if type(body.get("max_steps", MAX_STEPS)) is not int or not 1 <= body.get("max_steps", MAX_STEPS) <= 500:
            raise ValueError("Set an action limit from 1 to 500.")
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
            raise ValueError("Enter a task of 1–8,000 characters.")
        if not isinstance(values, list) or len(values) > 32:
            raise ValueError("Supply at most 32 exact text values.")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("Each supplied value must contain 1–2,000 characters.")
        if target is not None and (not isinstance(target, str) or len(target) > 200):
            raise ValueError("Choose an existing Chrome tab.")
        destinations = validate_navigation_urls(navigation_urls(goal, supplied_values(goal, values)))
        if not target and not destinations:
            raise ValueError("Choose a Chrome tab or include the destination website in your task.")

    def work(self, name, body):
        try:
            if name == "credentials":
                with self.lock:
                    self.credentials = {"status": "checking", "message": "Checking AWS and OpenRouter…"}
                result = check_openrouter()
                with self.lock:
                    self.credentials = result
            elif name == "connect":
                browser_connection.connect()
                if browser_connection.status().get("connected"):
                    self.refresh_tabs()
            elif name == "reset":
                if self.agent:
                    self.agent.close()
                self.agent = None
                with self.lock:
                    self.view = {"status": "idle", "page": None, "goal": "", "history": [],
                                 "decision": None, "values": []}
            else:
                if name == "start":
                    from jev_ultrafast.agent import Agent

                    # Existing-tab attach is explicit; the Browser validates target existence/type again.
                    if self.agent:
                        self.agent.close()
                        self.agent = None
                    with self.lock:
                        self.view = {
                            "status": "starting", "page": None, "goal": body["goal"],
                            "history": [], "decision": None, "values": body.get("values", []),
                        }
                    self.agent = Agent(
                        None, body["goal"], target_id=body.get("target_id"),
                        values=body.get("values", []), max_steps=body.get("max_steps", MAX_STEPS),
                        cancel_event=self.cancel,
                    )
                    self.capture()
                elif name in {"step", "predict", "act", "supply_values"}:
                    if self.agent.state["status"] == "paused":
                        self.agent.state["status"] = "ready"
                    command = "tick" if name == "step" else name
                    self.agent.command(command, body)
                    self.capture()
                if self.running:
                    if self.agent.state["status"] == "paused":
                        self.agent.state["status"] = "ready"
                    while not self.cancel.is_set() and self.agent.state["status"] not in TERMINAL:
                        self.agent.command("tick")
                        self.capture()
                        # Yield to the UI between decisions; do not schedule another browser mutation after Pause.
                        if self.cancel.wait(0.03):
                            break
        except StalePage:
            # Refresh evidence only. Never replay the consumed preview or browser action.
            if self.agent:
                self.agent.state["decision"] = None
                try:
                    self.agent.state["page"] = self.agent.browser.observe(screenshot=self.agent.screenshots)
                    self.agent.state["status"] = "ready"
                except (ValueError, RuntimeError, TimeoutError):
                    self.agent.state["status"] = "error"
                    self.error = "The page is still changing. Wait for it to settle, then start the task again."
                self.capture()
        except CredentialError as error:
            self.capture()
            with self.lock:
                self.credentials = {"status": "error", "message": str(error)}
                self.error = str(error)
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.capture()
            with self.lock:
                self.error = str(error)
                if name == "credentials":
                    self.credentials = {"status": "error", "message": str(error)}
                if name not in {"connect", "credentials"}:
                    if self.agent:
                        self.agent.state["status"] = "error"
                    self.view["status"] = "error"
        except Exception:
            # Do not reflect SDK details or secrets into HTTP responses or logs.
            with self.lock:
                self.error = (
                    "Operation failed. No browser action was automatically retried. Start a new task to recover."
                )
                if name == "credentials":
                    self.credentials = {"status": "error", "message": self.error}
                if self.agent:
                    self.agent.state["status"] = "error"
                if name not in {"connect", "credentials"}:
                    self.view["status"] = "error"
        finally:
            with self.lock:
                if self.task_cancel.is_set():
                    if self.agent:
                        self.agent.cancel_run()
                        self.capture()
                    else:
                        self.view["status"] = "cancelled"
                elif self.cancel.is_set() and self.agent and self.agent.state["status"] not in TERMINAL:
                    self.agent.finish_trace("paused", "Paused; pending preview discarded")
                    self.agent.state["status"] = "paused"
                    self.agent.state["decision"] = None
                    self.capture()
                self.running = False
                self.busy = False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.shutdown.set()
        self.pause()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)
        # Never detach during a dispatched browser mutation; process shutdown will close transport.
        if self.agent and not self.busy:
            self.agent.close()

    def startup(self):
        """Prepare access and connect once Chrome's manual setup is available."""
        try:
            self.submit("credentials", {})
            self.worker.join()
        except RuntimeError:
            pass
        attempted_endpoint = None
        while not self.shutdown.is_set():
            if not browser_connection.status().get("connected"):
                try:
                    endpoint = browser_connection.chrome_endpoint()
                except RuntimeError:
                    endpoint = None
                if endpoint and endpoint != attempted_endpoint:
                    attempted_endpoint = endpoint
                    browser_connection.connect(wait=0)
            self.shutdown.wait(2)


def make_server(port=8766, controller=None):
    control = controller or Controller()
    token = secrets.token_urlsafe(32)
    expected_host = f"127.0.0.1:{port}"
    origin = f"http://{expected_host}"

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, mime="application/json"):
            if not isinstance(content, (str, bytes)):
                content = json.dumps(content, ensure_ascii=False)
            if isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self, api=False):
            valid = self.headers.get("Host") == expected_host and self.headers.get("Origin") in (None, origin)
            if api:
                valid = valid and secrets.compare_digest(self.headers.get("X-CTRL-Token", ""), token)
            if not valid:
                self.send(403, {"error": "Local [CTRL] requests only."})
            return valid

        def do_GET(self):
            path = urlsplit(self.path).path
            if not self.allowed(api=path.startswith("/api/")):
                return
            try:
                if path == "/api/control":
                    return self.send(200, control.control_state())
                if path == "/api/state":
                    return self.send(200, control.state())
                if path == "/api/tabs":
                    return self.send(200, control.refresh_tabs())
                if path.startswith("/api/traces/"):
                    trace = control.trace(path.removeprefix("/api/traces/"))
                    return self.send(200, trace) if trace is not None else self.send(404, {"error": "Trace not found"})
                if path == "/health":
                    return self.send(200, {"app": "[CTRL]", "status": "ok", "model": MODEL})
                relative = "index.html" if path == "/" else path.removeprefix("/static/").lstrip("/")
                target = (STATIC / relative).resolve()
                if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                    return self.send(404, {"error": "Not found"})
                content = target.read_bytes()
                if target.name == "index.html":
                    content = content.replace(b"__TOKEN__", token.encode())
                self.send(200, content, mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            except (RuntimeError, ValueError, TimeoutError) as error:
                self.send(400, {"error": str(error)})
            except Exception:
                self.send(500, {"error": "Could not read local app state."})

        def do_POST(self):
            if not self.allowed(api=True):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 150_000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                path = urlsplit(self.path).path
                if not path.startswith("/api/"):
                    return self.send(404, {"error": "Not found"})
                if path == "/api/shutdown":
                    result = control.stop()
                    self.send(200, result)
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                self.send(202, control.submit(path.removeprefix("/api/"), body))
            except (ValueError, RuntimeError) as error:
                self.send(400, {"error": str(error)})
            except Exception:
                self.send(500, {"error": "The command failed. It was not retried."})

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    expected_host = f"127.0.0.1:{server.server_port}"
    origin = f"http://{expected_host}"
    server.daemon_threads = True
    server.control = control
    return server


def main():
    parser = argparse.ArgumentParser(description="Run [CTRL] locally beside your normal Chrome.")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535")
    os.environ["CTRL_PORT"] = str(args.port)
    from .launch import stop_generation

    root = Path(__file__).resolve().parents[1]
    generation = os.environ.get("CTRL_START_GENERATION")
    if generation is not None and generation != stop_generation(root):
        return
    server = make_server(args.port)
    atexit.register(server.control.close)
    if generation is not None:
        def watch_stop():
            while not server.control.shutdown.wait(0.1):
                if generation != stop_generation(root):
                    server.control.stop()
                    server.shutdown()
                    return

        threading.Thread(target=watch_stop, daemon=True, name="ctrl-stop-latch").start()
    threading.Thread(target=server.control.startup, daemon=True, name="ctrl-startup").start()
    print(f"[CTRL] is running at http://127.0.0.1:{args.port}", flush=True)
    print("Chrome tabs stay open. The OpenRouter credential stays in Python memory.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.control.close()
        server.server_close()
        pid_file = root / ".ctrl" / "server.pid"
        try:
            saved_pid = pid_file.read_text(encoding="utf-8").strip()
            if saved_pid in {str(os.getpid()), str(os.getppid())}:
                pid_file.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
