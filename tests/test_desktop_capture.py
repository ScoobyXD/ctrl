"""Capture protocol validation and private-helper lifecycle; no desktop access."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.desktop_capture import CaptureError, DesktopCapture, decode_capture, driver_parameters


def screenshot(width=7, height=5):
    stream = BytesIO()
    Image.new("RGBA", (width, height), (21, 34, 55, 128)).save(stream, format="PNG")
    return NS(isError=False, content=[NS(type="image", mimeType="image/png",
              data=base64.b64encode(stream.getvalue()).decode())],
              structuredContent={"screenshot_width": width, "screenshot_height": height})


class CaptureDecodeTests(unittest.TestCase):
    def test_png_pixels_retained_as_rgb_without_resize(self):
        rgb = decode_capture(screenshot())
        self.assertEqual(rgb.shape, (5, 7, 3))
        self.assertEqual(rgb.dtype, np.uint8)
        np.testing.assert_array_equal(rgb[3, 4], [21, 34, 55])
        self.assertTrue(rgb.flags.owndata)

    def test_malformed_or_mismatched_images_are_not_published(self):
        samples = []
        result = screenshot(); result.structuredContent["screenshot_width"] = 8; samples.append(result)
        result = screenshot(); result.content *= 2; samples.append(result)
        result = screenshot(); result.content[0].data = "not-base64"; samples.append(result)
        result = screenshot(); result.content[0].mimeType = "image/jpeg"; samples.append(result)
        for result in samples:
            with self.subTest(result=result), self.assertRaises(CaptureError):
                decode_capture(result)

    def test_driver_error_has_no_image_result(self):
        with self.assertRaisesRegex(CaptureError, "desktop unavailable"):
            decode_capture(NS(isError=True, content=[NS(type="text", text="desktop unavailable")]))


class CaptureLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        dependencies = self.root / "dependencies"
        dependencies.mkdir()
        exe = self.root / ".runtime/cua-driver/packages/cua-test/cua-driver.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"test-only executable bytes")
        (dependencies / "cua-driver.lock.json").write_text(json.dumps({
            "asset": "cua-test.zip", "version": "0.28.2",
            "executableSha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        }))
        self.exe = exe
        self.events = []
        self.responses = []
        owner = self

        @asynccontextmanager
        async def transport(parameters, errlog):
            owner.events.append("helper-open")
            try:
                yield ("read", "write")
            finally:
                owner.events.append("helper-close")

        class Session:
            def __init__(self, *args, **kwargs):
                owner.events.append("session-created")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                owner.events.append("session-close")
            async def initialize(self):
                owner.events.append("initialize")
                return NS(serverInfo=NS(version="0.28.2"))
            async def list_tools(self):
                owner.events.append("tools")
                return NS(tools=[NS(name="get_desktop_state")])
            async def call_tool(self, name, arguments, **kwargs):
                owner.events.append((name, arguments))
                response = owner.responses.pop(0) if owner.responses else screenshot()
                if isinstance(response, BaseException):
                    raise response
                if callable(response):
                    return await response()
                return response

        self.enterContext(patch("ctrl_loop.desktop_capture.stdio_client", transport))
        self.enterContext(patch("ctrl_loop.desktop_capture.ClientSession", Session))

    def test_child_environment_and_pin(self):
        before = os.environ.copy()
        parameters, version = driver_parameters(self.root)
        self.assertEqual(parameters.command, str(self.exe))
        self.assertEqual(parameters.args, ["mcp", "--direct", "--no-overlay"])
        self.assertEqual(parameters.env["CUA_DRIVER_RS_TELEMETRY_ENABLED"], "false")
        self.assertEqual(parameters.env["CUA_DRIVER_RS_UPDATE_CHECK"], "false")
        self.assertEqual(version, "0.28.2")
        self.assertEqual(os.environ, before)
        self.exe.write_bytes(b"tampered")
        with self.assertRaisesRegex(CaptureError, "hash"):
            driver_parameters(self.root)

    async def test_one_helper_two_deliberate_calls_and_ordered_shutdown(self):
        async with DesktopCapture(self.root) as capture:
            self.assertFalse(any(isinstance(event, tuple) for event in self.events))
            self.assertEqual((await capture.capture()).shape, (5, 7, 3))
            await capture.capture()
            self.assertEqual(self.events.count("helper-open"), 1)
        self.assertEqual(self.events.count(("get_desktop_state", {})), 2)
        self.assertEqual(self.events[-2:], ["session-close", "helper-close"])
        with self.assertRaisesRegex(RuntimeError, "Enter"):
            await capture.capture()

    async def test_timeout_closes_helper_without_retry_then_next_click_can_reconnect(self):
        self.responses.append(TimeoutError("capture deadline"))
        async with DesktopCapture(self.root) as capture:
            with self.assertRaises(TimeoutError):
                await capture.capture()
            self.assertEqual(self.events.count(("get_desktop_state", {})), 1)
            self.assertEqual(self.events[-2:], ["session-close", "helper-close"])
            await capture.capture()
            self.assertEqual(self.events.count("helper-open"), 2)
        self.assertEqual(self.events.count(("get_desktop_state", {})), 2)

    async def test_cancellation_releases_helper_in_owner_task(self):
        async def cancelled_request():
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            await asyncio.Event().wait()
        self.responses.append(cancelled_request)
        async with DesktopCapture(self.root) as capture:
            with self.assertRaises(asyncio.CancelledError):
                await capture.capture()
            self.assertEqual(self.events[-2:], ["session-close", "helper-close"])
        self.assertEqual(self.events.count(("get_desktop_state", {})), 1)

    async def test_decode_error_closes_helper(self):
        result = screenshot(); result.content = []
        self.responses.append(result)
        async with DesktopCapture(self.root) as capture:
            with self.assertRaises(CaptureError):
                await capture.capture()
            self.assertEqual(self.events[-2:], ["session-close", "helper-close"])


if __name__ == "__main__":
    unittest.main()

class RealMcpTransportTests(unittest.IsolatedAsyncioTestCase):
    """Use the actual SDK and an owned test child, without accessing the desktop."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.child = self.root / "fake_mcp.py"
        self.child.write_text('''import json, pathlib, sys
root = pathlib.Path(__file__).parent
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "test-capture", "version": "test"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "get_desktop_state", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        (root / "capture-requested").write_text("one")
        continue
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
(root / "closed-on-eof").write_text("clean")
''')
        from mcp import StdioServerParameters
        self.enterContext(patch("ctrl_loop.desktop_capture.driver_parameters", return_value=(
            StdioServerParameters(command=sys.executable, args=[str(self.child)], cwd=str(self.root)), "test",
        )))

    async def test_real_sdk_timeout_closes_child_on_eof(self):
        from mcp.shared.exceptions import McpError
        async with DesktopCapture(self.root, timeout_seconds=1) as capture:
            with self.assertRaises(McpError):
                await capture.capture()
            self.assertEqual((self.root / "capture-requested").read_text(), "one")
            self.assertEqual((self.root / "closed-on-eof").read_text(), "clean")

    async def test_real_sdk_cancelled_request_closes_child_on_eof(self):
        owner = asyncio.current_task()
        async def cancel_after_request():
            for _ in range(200):
                if (self.root / "capture-requested").exists():
                    owner.cancel()
                    return
                await asyncio.sleep(.01)
            self.fail("test helper did not receive capture")
        async with DesktopCapture(self.root) as capture:
            watcher = asyncio.create_task(cancel_after_request())
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await capture.capture()
                self.assertEqual((self.root / "closed-on-eof").read_text(), "clean")
            finally:
                await watcher

    async def test_pending_cancel_at_context_exit_still_closes_child(self):
        owner = asyncio.current_task()
        with self.assertRaises(asyncio.CancelledError):
            async with DesktopCapture(self.root):
                # Models a close requested while synchronous inference was busy:
                # its scheduled cancel first runs during the following cleanup.
                asyncio.get_running_loop().call_soon(owner.cancel)
        self.assertEqual((self.root / "closed-on-eof").read_text(), "clean")

if __name__ == "__main__":
    unittest.main()
