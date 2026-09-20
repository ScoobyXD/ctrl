"""Offline action-boundary tests. No target-app input is delivered by these tests."""
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.cua_driver import (DriverCancelled, DriverError, NativeCuaExecutor,
                                 NativeCuaTransport, action_mapping, match_driver_element)


TARGET = dict(pid=42, hwnd=900)


def element(**changes):
    value = dict(id="fresh:e2", role="Button", name="Open Bookmarks", enabled=True,
                 offscreen=False, bounds=[20, 30, 120, 40], patterns=["Invoke"],
                 is_password=False, state={})
    value.update(changes)
    return value


def snapshot(**changes):
    value = dict(pid=42, window_id=900, snapshot_id="s1234abcd", elements=[dict(
        element_index=5, element_token="s1234abcd:5", role="Button", label="Open Bookmarks",
        enabled=True, actions=["invoke"], frame=dict(x=20, y=30, w=120, h=40))])
    value.update(changes)
    return value


class FakeTransport:
    def __init__(self, observed=None, receipt=None, failure=None):
        self.observed = observed or snapshot()
        self.receipt = receipt if receipt is not None else dict(
            content=[dict(type="text", text="Input delivered")],
            structuredContent=dict(effect="unverifiable", route="accessibility"))
        self.failure = failure
        self.calls = []
        self.closed = False

    def call(self, name, arguments, cancelled=None):
        self.calls.append((name, arguments))
        if name == "get_window_state":
            return dict(structuredContent=self.observed)
        if self.failure:
            raise self.failure
        return self.receipt

    def close(self):
        self.closed = True


class MappingTests(unittest.TestCase):
    def test_native_priority_does_not_masquerade_as_requested_operation(self):
        with self.assertRaisesRegex(DriverError, "priority"):
            action_mapping(element(patterns=["Invoke", "SelectionItem"]), "select")
        self.assertEqual(action_mapping(element(patterns=["SelectionItem"]), "select"),
                         ("click", dict(delivery_mode="background"), "select"))

    def test_disabled_offscreen_and_unknown_capabilities_fail(self):
        for e in (element(enabled=False), element(offscreen=True), element(patterns=[])):
            with self.subTest(e=e), self.assertRaises(DriverError):
                action_mapping(e, "activate")
        with self.assertRaisesRegex(DriverError, "arguments must be an object"):
            action_mapping(element(), "activate", [])

    def test_text_is_required_literal_not_inferred(self):
        e = element(patterns=["Value"], state=dict(value_read_only=False))
        for args in ({}, {"value": 4}, {"value": "x", "extra": 1}, {"value": "x" * 4097}):
            with self.subTest(args=args), self.assertRaises(DriverError):
                action_mapping(e, "set_value", args)
        self.assertEqual(action_mapping(e, "set_value", {"value": ""})[1], {"value": ""})
        self.assertEqual(action_mapping(e, "set_value", {"value": "line\nquote\""})[1],
                         {"value": "line\nquote\""})

    def test_password_unknown_and_readonly_refuse_text(self):
        for password, readonly in ((True, False), (None, False), (False, True), (False, None)):
            with self.subTest(password=password, readonly=readonly), self.assertRaises(DriverError):
                action_mapping(element(patterns=["Value"], is_password=password,
                    state=dict(value_read_only=readonly)), "set_value", {"value": "hello"})

    def test_numeric_range_uses_native_string_and_rejects_invalid_values(self):
        e = element(patterns=["RangeValue"], state=dict(range_read_only=False,
                    range_minimum=0, range_maximum=10))
        self.assertEqual(action_mapping(e, "set_range_value", {"value": 2.5})[1], {"value": "2.5"})
        for value in (False, "2", float("nan"), float("inf"), -1, 11):
            with self.subTest(value=value), self.assertRaises(DriverError):
                action_mapping(e, "set_range_value", {"value": value})
        e["patterns"].append("Value")
        with self.assertRaisesRegex(DriverError, "unambiguously"):
            action_mapping(e, "set_range_value", {"value": 2})

    def test_scroll_and_collapse_have_precise_limits(self):
        with self.assertRaisesRegex(DriverError, "explicit scroll direction"):
            action_mapping(element(), "scroll_vertical", {})
        with self.assertRaisesRegex(DriverError, "different window region"):
            action_mapping(element(), "scroll_vertical", {"direction": "down"})
        with self.assertRaisesRegex(DriverError, "explicitly collapses"):
            action_mapping(element(), "collapse")

    def test_expand_state_is_current(self):
        e = element(patterns=["ExpandCollapse"], state=dict(expand_collapse=0))
        self.assertEqual(action_mapping(e, "expand")[0], "click")
        e["state"]["expand_collapse"] = 1
        with self.assertRaises(DriverError):
            action_mapping(e, "expand")


class NativeBindingTests(unittest.TestCase):
    def test_native_tokens_not_observation_ids_are_sent(self):
        transport = FakeTransport()
        result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate")
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["driver_result"]["structuredContent"]["effect"], "unverifiable")
        self.assertNotIn("success", result)
        tool, args = transport.calls[-1]
        self.assertEqual(tool, "click")
        self.assertEqual(args, dict(pid=42, window_id=900, element_token="s1234abcd:5",
                                    delivery_mode="background"))
        self.assertFalse(transport.calls[0][1]["include_screenshot"])

    def test_wrong_pid_hwnd_frame_name_role_or_ambiguous_rows_never_send_input(self):
        bad = []
        for key, value in (("pid", 43), ("window_id", 901)):
            bad.append(snapshot(**{key: value}))
        for key, value in (("role", "TabItem"), ("label", "Different"),
                           ("enabled", False), ("frame", dict(x=40, y=30, w=120, h=40)),
                           ("element_token", None), ("actions", ["toggle"]), ("actions", "invoke")):
            s = snapshot()
            s["elements"][0][key] = value
            bad.append(s)
        s = snapshot()
        s["elements"].append(copy.deepcopy(s["elements"][0]))
        bad.append(s)
        for observed in bad:
            with self.subTest(observed=observed):
                transport = FakeTransport(observed=observed)
                result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["attempted"])
                self.assertEqual(len(transport.calls), 1)

    def test_bounds_must_be_finite_positive_dimensions(self):
        for bounds in (None, [], [0, 0, 0, 20], [False, 0, 20, 20], [float("nan"), 0, 20, 20]):
            with self.subTest(bounds=bounds), self.assertRaises(DriverError):
                match_driver_element(TARGET, element(bounds=bounds), snapshot(), "invoke")

    def test_pattern_priority_must_match_driver_observation(self):
        e = element(patterns=["SelectionItem"])
        s = snapshot()
        s["elements"][0]["actions"] = ["invoke", "select"]
        with self.assertRaisesRegex(DriverError, "capabilities changed"):
            match_driver_element(TARGET, e, s, "select")

    def test_cancel_before_input_and_cancel_during_input_are_distinct(self):
        transport = FakeTransport()
        result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate", cancelled=lambda: True)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(transport.calls, [])
        transport = FakeTransport(failure=DriverCancelled("stop"))
        result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate")
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["attempted"])

    def test_failure_or_timeout_is_not_retried(self):
        for receipt, failure, status in ((dict(isError=True, content=[]), None, "failed"),
                (dict(structuredContent=dict(effect="refused")), None, "failed"),
                (None, DriverError("timeout"), "unknown")):
            with self.subTest(status=status):
                transport = FakeTransport(receipt=receipt, failure=failure)
                result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate")
                self.assertEqual(result["status"], status)
                self.assertEqual(len(transport.calls), 2)

    def test_missing_operand_is_rejected_before_driver_start(self):
        transport = FakeTransport()
        result = NativeCuaExecutor(transport=transport).execute(TARGET,
            element(patterns=["Value"], state=dict(value_read_only=False)), "set_value")
        self.assertFalse(result["attempted"])
        self.assertEqual(transport.calls, [])

    def test_malformed_or_partial_action_receipts_are_not_delivery_evidence(self):
        for receipt in ({}, {"content": [{"type": "text", "text": "ok"}]},
                        {"structuredContent": {"effect": "confirmed"}},
                        {"structuredContent": {"effect": "partial", "route": "accessibility"}},
                        {"structuredContent": {"effect": "suspected_noop", "route": "accessibility"}},
                        {"structuredContent": {"effect": "confirmed", "route": "invented"}}):
            with self.subTest(receipt=receipt):
                transport = FakeTransport(receipt=receipt)
                result = NativeCuaExecutor(transport=transport).execute(TARGET, element(), "activate")
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(result["attempted"])
                self.assertEqual(len(transport.calls), 2)


PEER = r'''
import sys,json,time,os
for line in sys.stdin:
    r=json.loads(line)
    if 'id' not in r: continue
    if r['method']=='initialize':
        result={'protocolVersion':'2025-06-18','serverInfo':{'version':VERSION}}
    elif r['method']=='tools/list':
        result={'tools':[{'name':name,'inputSchema':{'properties':{}}} for name in ['get_window_state','click','set_value']]}
    elif MODE=='stall':
        time.sleep(30)
        continue
    elif MODE=='badid':
        r['id']+=1
        result={}
    else:
        result={'env':{k:os.environ.get(k) for k in ['CUA_DRIVER_RS_TELEMETRY_ENABLED','CUA_DRIVER_RS_UPDATE_CHECK','CUA_DRIVER_PERMISSION_MODE']}}
    print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
'''


class StdioBoundaryTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".runtime"
        scratch.mkdir(exist_ok=True)
        # Ordinary inherited ACLs also work on restricted Windows test desktops;
        # tempfile's owner-only ACL can exclude the sandbox's restricted token.
        self.root = scratch / ("cua-driver-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        (self.root / "dependencies").mkdir()
        self.exe = self.root / "fake-driver.exe"
        self.exe.write_bytes(b"test peer placeholder")
        self.pin = dict(version="0.28.2", asset="fake.zip",
                        executableSha256=hashlib.sha256(self.exe.read_bytes()).hexdigest())
        (self.root / "dependencies/cua-driver.lock.json").write_text(json.dumps(self.pin))
        self.real_popen = subprocess.Popen
        self.started = []

    def tearDown(self):
        for process in self.started:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        (self.root / "dependencies/cua-driver.lock.json").unlink()
        (self.root / "dependencies").rmdir()
        self.exe.unlink()
        self.root.rmdir()

    def peer(self, mode="normal", version="0.28.2"):
        def launch(args, **kwargs):
            self.assertEqual(args[1:], ["mcp", "--direct", "--no-overlay"])
            process = self.real_popen([sys.executable, "-u", "-c",
                f"MODE={mode!r}\nVERSION={version!r}\n" + PEER], **kwargs)
            self.started.append(process)
            return process
        return patch("ctrl_loop.cua_driver.subprocess.Popen", side_effect=launch)

    def test_pin_mismatch_never_launches(self):
        self.exe.write_bytes(b"changed")
        with self.peer(), self.assertRaisesRegex(DriverError, "SHA-256"):
            NativeCuaTransport(self.root, self.exe).start()
        self.assertEqual(self.started, [])

    def test_version_mismatch_stops_child(self):
        with self.peer(version="1.0"):
            driver = NativeCuaTransport(self.root, self.exe)
            with self.assertRaisesRegex(DriverError, "version/protocol"):
                driver.start()
        self.assertIsNotNone(self.started[-1].poll())

    def test_environment_is_child_only_and_close_stops_child(self):
        before = os.environ.get("CUA_DRIVER_RS_TELEMETRY_ENABLED")
        with self.peer():
            driver = NativeCuaTransport(self.root, self.exe)
            driver.start()
            env = driver.call("click", {})["env"]
            self.assertEqual(env, dict(CUA_DRIVER_RS_TELEMETRY_ENABLED="false",
                CUA_DRIVER_RS_UPDATE_CHECK="false", CUA_DRIVER_PERMISSION_MODE="standard"))
            driver.close()
        self.assertEqual(os.environ.get("CUA_DRIVER_RS_TELEMETRY_ENABLED"), before)
        self.assertEqual(self.started[-1].poll(), 0)

    def test_timeout_and_cancel_stop_child_without_retry(self):
        for cancelled in (None, lambda: time.monotonic() >= deadline):
            with self.subTest(cancelled=cancelled), self.peer(mode="stall"):
                driver = NativeCuaTransport(self.root, self.exe, timeout=0.3)
                driver.start()
                deadline = time.monotonic() + 0.1
                start = time.monotonic()
                with self.assertRaises(DriverError):
                    driver.call("click", {}, cancelled)
                self.assertLess(time.monotonic() - start, 2)
                self.assertIsNotNone(self.started[-1].poll())

    def test_wrong_response_id_stops_child(self):
        with self.peer(mode="badid"):
            driver = NativeCuaTransport(self.root, self.exe)
            driver.start()
            with self.assertRaisesRegex(DriverError, "response ID"):
                driver.call("click", {})
        self.assertIsNotNone(self.started[-1].poll())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifetime contract")
    def test_killing_owner_terminates_owned_child(self):
        script = "\n".join([
            "import sys,subprocess,time", "from ctrl_loop.cua_driver import OwnedProcessJob",
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])",
            "job=OwnedProcessJob(child)", "print(child.pid,flush=True)", "time.sleep(30)"])
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        helper = self.real_popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.started.append(helper)
        # Poll a bounded pipe read through the same threading mechanism as transport.
        import queue
        import threading
        output = queue.Queue()
        reader = threading.Thread(target=lambda: output.put(helper.stdout.readline()), daemon=True)
        reader.start()
        child_pid = int(output.get(timeout=5).strip())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
        self.assertTrue(handle)
        try:
            helper.kill()
            helper.wait(timeout=2)
            self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
        finally:
            kernel.CloseHandle(handle)
            helper.stdout.close()
            helper.stderr.close()


if __name__ == "__main__":
    unittest.main()
