"""Primary-display capture through an owned, persistent CUA MCP stdio helper."""
from __future__ import annotations

import base64
from contextlib import AsyncExitStack
from datetime import timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
import numpy as np
from PIL import Image


class CaptureError(RuntimeError):
    """A capture failed without producing a usable new image."""


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def driver_parameters(root: Path) -> tuple[StdioServerParameters, str]:
    """Resolve only this worktree's hash-verified executable and child settings."""
    pin = json.loads((root / "dependencies/cua-driver.lock.json").read_text(encoding="utf-8-sig"))
    asset = Path(pin["asset"])
    if asset.name != pin["asset"] or asset.suffix != ".zip":
        raise CaptureError("Invalid CUA asset name in dependency lock.")
    runtime = root / ".runtime/cua-driver"
    executable = runtime / "packages" / asset.stem / "cua-driver.exe"
    if not executable.is_file():
        raise CaptureError("CUA helper is missing. Run scripts/install-capture-viewer.ps1.")
    if _digest(executable) != pin["executableSha256"].lower():
        raise CaptureError("CUA helper hash differs from dependencies/cua-driver.lock.json.")
    return StdioServerParameters(
        command=str(executable), args=["mcp", "--direct", "--no-overlay"], cwd=str(root),
        env={
            "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
            "CUA_DRIVER_RS_UPDATE_CHECK": "false",
            "CUA_DRIVER_TELEMETRY_HOME": str(runtime / "telemetry"),
            "CUA_DRIVER_PERMISSION_MODE": "standard",
        },
    ), pin["version"]


def decode_capture(result: Any) -> np.ndarray:
    """Require one native PNG whose declared dimensions match its actual pixels."""
    if result.isError:
        message = " ".join(block.text for block in result.content if block.type == "text")
        raise CaptureError(message[:1200] or "CUA could not capture the primary display.")
    images = [block for block in result.content if block.type == "image"]
    if len(images) != 1 or images[0].mimeType != "image/png":
        raise CaptureError("CUA did not return exactly one PNG screenshot.")
    try:
        data = base64.b64decode(images[0].data, validate=True)
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise CaptureError("CUA screenshot bytes are not a PNG.")
            metadata = result.structuredContent or {}
            if (metadata.get("screenshot_width"), metadata.get("screenshot_height")) != image.size:
                raise CaptureError("CUA screenshot dimensions differ from its metadata.")
            return np.array(image.convert("RGB"), dtype=np.uint8, copy=True)
    except (ValueError, OSError) as exc:
        raise CaptureError("CUA returned an unreadable PNG screenshot.") from exc


class DesktopCapture:
    """Use inside one worker coroutine; enter, capture and exit in the same task.

    The MCP SDK owns framing, request IDs, cancellation, EOF shutdown and process
    termination. No screenshot occurs on startup. Every capture invocation sends
    exactly one screenshot request. An error closes the helper; only the next
    explicit invocation reconnects. Never share this object across threads/tasks.
    """

    def __init__(self, root: str | Path, timeout_seconds: float = 30) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Capture timeout must be positive.")
        self.root = Path(root).resolve()
        self.timeout = timedelta(seconds=timeout_seconds)
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._entered = False
        self._capturing = False

    async def _connect(self) -> None:
        parameters, version = driver_parameters(self.root)
        stack = AsyncExitStack()
        self._stack = stack
        try:
            log_path = self.root / ".runtime/viewer/cua-driver.stderr.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            errlog = stack.enter_context(log_path.open("a", encoding="utf-8"))
            streams = await stack.enter_async_context(stdio_client(parameters, errlog=errlog))
            self._session = await stack.enter_async_context(ClientSession(
                *streams, read_timeout_seconds=self.timeout,
                client_info=Implementation(name="ctrl-capture-viewer", version="0.1.0"),
            ))
            initialized = await self._session.initialize()
            if initialized.serverInfo.version != version:
                raise CaptureError("CUA helper reported a version different from its pin.")
            listing = await self._session.list_tools()
            if "get_desktop_state" not in {tool.name for tool in listing.tools}:
                raise CaptureError("CUA helper does not provide get_desktop_state.")
        except BaseException:
            await self._disconnect()
            raise

    async def _disconnect(self) -> None:
        self._session = None
        stack, self._stack = self._stack, None
        if stack is not None:
            # Do not use asyncio.shield here: it moves cleanup to another task,
            # but the MCP SDK's AnyIO context scopes must exit in their owner task.
            await stack.aclose()

    async def __aenter__(self) -> DesktopCapture:
        if self._entered:
            raise RuntimeError("DesktopCapture is already active.")
        await self._connect()
        self._entered = True
        return self

    async def capture(self) -> np.ndarray:
        if not self._entered:
            raise RuntimeError("Enter DesktopCapture before capturing.")
        if self._capturing:
            raise RuntimeError("A capture is already in progress.")
        self._capturing = True
        try:
            if self._session is None:
                await self._connect()
            assert self._session is not None
            result = await self._session.call_tool(
                "get_desktop_state", {}, read_timeout_seconds=self.timeout,
            )
            return decode_capture(result)
        except BaseException:
            await self._disconnect()
            raise
        finally:
            self._capturing = False

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._entered = False
        await self._disconnect()
