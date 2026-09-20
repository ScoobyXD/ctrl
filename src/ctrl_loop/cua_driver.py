"""Pinned native Windows CUA transport and conservative UIA action adapter.

The native driver's element tokens are acquired immediately before an action.
They are not our observation IDs. A successful driver receipt is input-delivery
evidence only; the caller must observe again to assess the resulting UI.

Mapping was inspected against the 0.28.2 binary's tools/list and pinned source
platform-windows/src/tools/impl_.rs at fc188250b4ca8549b8e61f937fdb1fb560770e86.
"""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


class DriverError(RuntimeError):
    """The driver cannot safely continue this request."""


class DriverCancelled(DriverError):
    pass


class OwnedProcessJob:
    """Kill owned Windows child processes if the helper exits or is terminated.

Assign before sending any request. The handle is non-inheritable, so killing
the execution helper closes its last job handle and kills driver/UIA children.
Non-Windows callers keep a no-op owner for offline fake-process tests.
"""
    def __init__(self, process=None):
        self.handle = None
        if os.name == "nt":
            class BasicLimits(ctypes.Structure):
                _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                            ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                            ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                            ("scheduling", wintypes.DWORD)]
            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in
                            ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                           ctypes.c_void_p, wintypes.DWORD]
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.CreateJobObjectW(None, None)
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.handle or not self.kernel.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise DriverError("Could not create the child-process lifetime boundary.")
        if process is not None:
            self.assign(process)

    def assign(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise DriverError("Could not contain the driver process; execution was not started.")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


KillOnCloseJob = OwnedProcessJob


def _check_cancelled(cancelled):
    if cancelled and cancelled():
        raise DriverCancelled("Execution stopped by the user.")


class NativeCuaTransport:
    """One bounded, private MCP stdio runtime; no daemon, port or global config."""
    def __init__(self, root=None, executable=None, timeout=20):
        self.root = Path(root or Path(__file__).resolve().parents[2])
        self.pin = json.loads((self.root / "dependencies/cua-driver.lock.json").read_text())
        self.executable = Path(executable or self.root / ".runtime/cua-driver/packages" /
                               Path(self.pin["asset"]).stem / "cua-driver.exe")
        self.timeout = min(60.0, max(0.1, float(timeout)))
        self.process = None
        self.job = None
        self.responses = queue.Queue(maxsize=32)
        self.stderr = deque(maxlen=32)
        self.sequence = 0
        self.tools = {}
        self.failed = False
        self.threads = []

    def _thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()

    def start(self, cancelled=None):
        if self.process:
            return
        if self.failed:
            raise DriverError("This driver runtime has stopped; create a new executor to retry.")
        _check_cancelled(cancelled)
        try:
            with self.executable.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise DriverError("Install the pinned driver with scripts/install-cua-driver.ps1 first.") from exc
        if digest.lower() != self.pin["executableSha256"].lower():
            raise DriverError("CUA executable SHA-256 differs from the dependency lock.")
        env = dict(os.environ, CUA_DRIVER_RS_TELEMETRY_ENABLED="false",
                   CUA_DRIVER_RS_UPDATE_CHECK="false", CUA_DRIVER_PERMISSION_MODE="standard",
                   CUA_DRIVER_TELEMETRY_HOME=str(self.root / ".runtime/cua-driver/telemetry"))
        _check_cancelled(cancelled)
        try:
            self.process = subprocess.Popen([str(self.executable), "mcp", "--direct", "--no-overlay"],
                cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
                bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.job = OwnedProcessJob(self.process)
            self._thread(self._read_stdout)
            self._thread(self._read_stderr)
            result = self.rpc("initialize", dict(protocolVersion="2025-06-18", capabilities={},
                clientInfo=dict(name="ctrl-loop-execution", version="1.0")), cancelled)
            if (result.get("serverInfo", {}).get("version") != self.pin["version"] or
                    result.get("protocolVersion") != "2025-06-18"):
                raise DriverError("Driver MCP version/protocol differs from the evaluated pin.")
            sent = self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
            deadline = time.monotonic() + self.timeout
            while not sent.wait(0.05):
                _check_cancelled(cancelled)
                if time.monotonic() >= deadline:
                    raise DriverError("Driver initialization pipe timed out.")
            listed = self.rpc("tools/list", {}, cancelled)
            self.tools = {item["name"]: item for item in listed.get("tools", [])}
            if not {"get_window_state", "click", "set_value"} <= self.tools.keys():
                raise DriverError("Pinned native action tools were not advertised.")
        except Exception:
            self.close(abort=True)
            raise

    def _read_stdout(self):
        process = self.process
        try:
            while True:
                line = process.stdout.readline(4_000_001)
                if not line:
                    raise DriverError("Driver closed its response stream.")
                if len(line) > 4_000_000:
                    raise DriverError("Driver response exceeded the bounded 4 MB limit.")
                response = json.loads(line)
                if isinstance(response, dict) and "id" in response:
                    self.responses.put_nowait(response)
        except Exception as exc:
            try:
                self.responses.put_nowait(DriverError(str(exc)))
            except queue.Full:
                pass

    def _read_stderr(self):
        process = self.process
        try:
            while chunk := process.stderr.read(1024):
                self.stderr.append(chunk)
        except (OSError, ValueError):
            pass

    def _write(self, message):
        payload = json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n"
        if len(payload) > 64_000:
            raise DriverError("Driver request exceeded the bounded 64 KB limit.")
        # A blocked stdin pipe must not prevent cancellation/deadline checks.
        sent = threading.Event()
        process = self.process
        def send():
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                try:
                    self.responses.put_nowait(DriverError("Driver input pipe closed: " + str(exc)))
                except queue.Full:
                    pass
            finally:
                sent.set()
        self._thread(send)
        return sent

    def rpc(self, method, params, cancelled=None):
        _check_cancelled(cancelled)
        self.sequence += 1
        request_id = self.sequence
        self._write(dict(jsonrpc="2.0", id=request_id, method=method, params=params))
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                _check_cancelled(cancelled)
                if time.monotonic() >= deadline:
                    raise DriverError(f"Driver request timed out: {method}. Input outcome may be unknown.")
                try:
                    response = self.responses.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if isinstance(response, Exception):
                    raise response
                if response.get("id") != request_id:
                    raise DriverError("Unexpected driver response ID; refusing to continue.")
                if "error" in response:
                    raise DriverError("Driver RPC error: " + json.dumps(response["error"])[:1000])
                if not isinstance(response.get("result"), dict):
                    raise DriverError("Malformed driver RPC result.")
                return response["result"]
        except Exception:
            self.close(abort=True)
            raise

    def call(self, name, arguments, cancelled=None):
        self.start(cancelled)
        if name not in self.tools:
            raise DriverError("Driver does not advertise " + name)
        schema = self.tools[name]["inputSchema"]
        if set(arguments) - schema.get("properties", {}).keys():
            raise DriverError("Unsupported native tool argument; execution refused.")
        if set(schema.get("required", [])) - arguments.keys():
            raise DriverError("Required native tool argument is missing.")
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, cancelled)

    def close(self, abort=False):
        process = self.process
        if process is None:
            return
        self.failed = True
        if abort and process.poll() is None:
            process.kill()
        if not abort:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.job:
            self.job.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
        # Wait for EOF to release pipe locks before closing Python file objects.
        for thread in self.threads:
            thread.join(timeout=0.1)
        if not any(thread.is_alive() for thread in self.threads):
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self.process = None


_CLICK_PRIORITY = [("Invoke", "activate", "invoke"), ("Toggle", "toggle", "toggle"),
                   ("SelectionItem", "select", "select"), ("ExpandCollapse", "expand", "expand")]


def action_mapping(element, operation, arguments=None):
    """Validate explicit operands and return (native tool, operands, capability).

The driver click implementation chooses its first available UIA pattern. Never
pretend an explicit select/toggle/collapse can override that native priority.
"""
    args = {} if arguments is None else arguments
    if not isinstance(args, dict):
        raise DriverError("Action arguments must be an object.")
    if element.get("enabled") is not True or element.get("offscreen") is not False:
        raise DriverError("The current UIA target is disabled or offscreen.")
    patterns = element.get("patterns", [])
    state = element.get("state", {})
    if operation in {"activate", "toggle", "select", "expand"}:
        if args:
            raise DriverError("This action does not accept additional arguments.")
        primary = next((item for item in _CLICK_PRIORITY if item[0] in patterns), None)
        if primary is None or primary[1] != operation:
            raise DriverError("Native click pattern priority cannot faithfully perform " + operation + ".")
        if operation == "expand" and state.get("expand_collapse") not in (0, 2):
            raise DriverError("The current target does not need expansion.")
        return "click", {"delivery_mode": "background"}, primary[2]
    if operation in {"set_value", "set_range_value"}:
        if set(args) != {"value"}:
            raise DriverError("Supply an explicit value argument for " + operation + ".")
        if element.get("is_password") is not False:
            raise DriverError("Value input requires a known non-password target.")
        value = args["value"]
        if operation == "set_value":
            if "Value" not in patterns or state.get("value_read_only") is not False:
                raise DriverError("The current target does not expose writable ValuePattern.")
            if not isinstance(value, str) or len(value) > 4096:
                raise DriverError("Supply explicit text with at most 4096 characters.")
        else:
            if "RangeValue" not in patterns or state.get("range_read_only") is not False or "Value" in patterns:
                raise DriverError("The driver cannot unambiguously set this numeric range target.")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise DriverError("Supply an explicit finite number for set_range_value.")
            lo, hi = state.get("range_minimum"), state.get("range_maximum")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                       for x in (lo, hi)) or not lo <= value <= hi:
                raise DriverError("Number is outside the known current range bounds.")
            value = str(value)
        return "set_value", {"value": value}, "set_value"
    if operation in {"scroll_vertical", "scroll_horizontal"}:
        directions = ("up", "down") if operation == "scroll_vertical" else ("left", "right")
        if args.get("direction") not in directions:
            raise DriverError("Supply an explicit scroll direction: " + " or ".join(directions) + ".")
        raise DriverError("CUA 0.28.2 can fall back from element scroll to a different window region; "
                          "this adapter does not execute scroll until exact-region delivery is supported.")
    if operation == "collapse":
        raise DriverError("CUA 0.28.2 has no advertised action that explicitly collapses this UIA target.")
    raise DriverError("Unsupported execution operation: " + str(operation))


def match_driver_element(target, element, snapshot, capability):
    """Join two *current* UIA views, requiring unambiguous identity and geometry."""
    if snapshot.get("pid") != target["pid"] or snapshot.get("window_id") != target["hwnd"]:
        raise DriverError("Native driver observed a different PID/HWND.")
    bounds = element.get("bounds")
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or
            not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in bounds) or
            bounds[2] <= 0 or bounds[3] <= 0):
        raise DriverError("Current UIA geometry is unavailable or invalid.")
    matches = []
    for row in snapshot.get("elements", []):
        frame = row.get("frame") or {}
        values = [frame.get(key) for key in ("x", "y", "w", "h")]
        actions = row.get("actions")
        if (row.get("role") != element.get("role") or
                (row.get("label") or "") != (element.get("name") or "") or
                row.get("enabled") is not True or not isinstance(actions, list) or
                any(not isinstance(action, str) for action in actions) or capability not in actions):
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and
               abs(v - expected) <= 2 for v, expected in zip(values, bounds)):
            matches.append(row)
    if len(matches) != 1:
        raise DriverError("Current native target could not be uniquely matched by role, name and physical bounds.")
    row = matches[0]
    if not isinstance(row.get("element_token"), str) or not row["element_token"]:
        raise DriverError("Native observation supplied no fresh element token.")
    # The second observation must agree about native click's pattern priority.
    if capability in {"invoke", "toggle", "select", "expand"}:
        first = next((action for _, _, action in _CLICK_PRIORITY if action in row.get("actions", [])), None)
        if first != capability:
            raise DriverError("Native target capabilities changed before input delivery.")
    return row


class NativeCuaExecutor:
    def __init__(self, root=None, executable=None, timeout=20, *, transport=None):
        self.transport = transport or NativeCuaTransport(root, executable, timeout)

    def execute(self, target, element, operation, arguments=None, cancelled=None):
        outcome = dict(status="failed", attempted=False, operation=operation, tool=None,
                       driver_result=None, error=None)
        try:
            _check_cancelled(cancelled)
            if any(isinstance(target.get(k), bool) or not isinstance(target.get(k), int) or target[k] <= 0
                   for k in ("pid", "hwnd")):
                raise DriverError("Execution requires an explicit positive PID and HWND.")
            name, operands, capability = action_mapping(element, operation, arguments)
            outcome["tool"] = name
            observed = self.transport.call("get_window_state", dict(pid=target["pid"], window_id=target["hwnd"],
                include_accessibility_tree=True, include_screenshot=False, max_elements=2000, max_depth=40), cancelled)
            if observed.get("isError"):
                outcome["driver_result"] = observed
                raise DriverError("Driver could not obtain a fresh target observation.")
            snapshot = observed.get("structuredContent") or {}
            row = match_driver_element(target, element, snapshot, capability)
            _check_cancelled(cancelled)
            params = dict(pid=target["pid"], window_id=target["hwnd"], element_token=row["element_token"], **operands)
            outcome.update(attempted=True, native_snapshot_id=snapshot.get("snapshot_id"),
                           native_element_index=row.get("element_index"))
            result = self.transport.call(name, params, cancelled)
            outcome["driver_result"] = result
            evidence = result.get("structuredContent") or {}
            refused = bool(result.get("isError") or evidence.get("refusal") or evidence.get("effect") == "refused")
            if refused:
                outcome.update(status="failed", error="Driver refused or failed the action; inspect its receipt.")
            elif (evidence.get("effect") in {"confirmed", "unverifiable"} and
                  evidence.get("route") in {"accessibility", "synthetic_events", "global_input",
                                            "system_api", "dom", "trusted_input"}):
                # This acknowledges the native action contract, not goal completion.
                outcome["status"] = "delivered"
            else:
                outcome.update(status="unknown", error="Driver returned no complete positive action receipt; "
                               "input may have occurred. Inspect fresh observation before retrying.")
        except DriverCancelled as exc:
            outcome.update(status="unknown" if outcome["attempted"] else "cancelled", error=str(exc)[:1200])
        except Exception as exc:
            outcome.update(status="unknown" if outcome["attempted"] else "failed", error=str(exc)[:1200])
        return outcome

    def close(self):
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
