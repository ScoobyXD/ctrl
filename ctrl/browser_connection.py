"""One persistent Browser Harness connection to the user's normal Google Chrome.

The harness's patient transport keeps Chrome's native approval prompt alive. We do
not start its convenience daemon: that daemon chooses/creates a tab on startup.
All page attachment is instead explicit and owned by ``Browser``. No CDP request
is retried, including when a response is lost after Chrome executed an input.
"""

import asyncio
import concurrent.futures
import os
import platform
import threading
from pathlib import Path
from urllib.parse import urlparse

from .browser_lease import BrowserLease
from .browser_scope import is_loopback_host, normalized_host

SETUP_MESSAGE = (
    "In your normal Google Chrome, open chrome://inspect/#remote-debugging and enable "
    "'Allow remote debugging for this browser instance', then connect again."
)


def _harness_client(endpoint):
    # Harness imports initialize runtime directories even when its daemon is unused.
    # Keep those directories inside this standalone application.
    runtime = Path(__file__).resolve().parents[1] / ".runtime" / "harness"
    os.environ["BH_HOME"] = str(runtime)
    os.environ["BH_TMP_DIR"] = str(runtime / "tmp")
    os.environ["BH_RUNTIME_DIR"] = str(runtime / "runtime")
    os.environ["BH_AGENT_WORKSPACE"] = str(runtime / "workspace")
    from browser_harness.daemon import _PatientCDPClient

    return _PatientCDPClient(endpoint)


def chrome_profile() -> Path:
    """Use only stable Google Chrome's ordinary user-data directory."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Google/Chrome/User Data"
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Google/Chrome"
    return Path.home() / ".config/google-chrome"


def chrome_endpoint() -> str:
    """Read Chrome's own local endpoint; never scan ports or other profiles."""
    try:
        lines = (chrome_profile() / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
        port = int(lines[0].strip())
        path = lines[1].strip()
    except (OSError, IndexError, ValueError) as exc:
        raise RuntimeError(SETUP_MESSAGE) from exc
    if not 0 < port <= 65535 or not path.startswith("/devtools/browser/") or any(c.isspace() for c in path):
        raise RuntimeError(SETUP_MESSAGE)
    # A path cannot supply an authority, query string, or fragment.
    if urlparse(path).path != path or "\\" in path:
        raise RuntimeError(SETUP_MESSAGE)
    return f"ws://127.0.0.1:{port}{path}"


def selectable_tab(tab: dict) -> bool:
    if tab.get("type") != "page":
        return False
    parsed = urlparse(tab.get("url", ""))
    if parsed.scheme not in {"http", "https"}:
        return False
    # Never let the agent control its own authenticated local console.
    ui_port = int(os.environ.get("CTRL_PORT", "8766"))
    try:
        effective_port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if is_loopback_host(normalized_host(tab.get("url", ""))) and effective_port in {8766, 8767, ui_port}:
            return False
    except ValueError:
        return False
    return True


class ChromeConnection:
    def __init__(self):
        self._lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._client = None
        self._pending = None
        self._state = "disconnected"
        self._message = "Connect to your normal Chrome."
        self._version = None
        self._lease = BrowserLease()
        self._generation = 0
        self._attempt = None
        self._closing = False
        self._cleanup_future = None

    def _start_loop(self):
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ctrl-chrome", daemon=True)
        self._thread.start()

    async def _open(self, endpoint, attempt):
        # Private API intentionally pinned to browser-harness==0.1.13.
        with self._lock:
            attempt["task"] = asyncio.current_task()
            if self._closing or attempt["generation"] != self._generation:
                return
        published = False
        try:
            for previous in list(attempt["clients"]):
                await previous.stop()
                attempt["clients"].remove(previous)
            client = _harness_client(endpoint)
            attempt["clients"].append(client)
            await client.start()
            version = await asyncio.wait_for(client.send_raw("Browser.getVersion"), 10)
            product = version.get("product", "")
            if not product.startswith("Chrome/"):
                raise RuntimeError("The normal Google Chrome connection could not be verified.")
            with self._lock:
                if self._closing or attempt["generation"] != self._generation or self._attempt is not attempt:
                    return
                self._client = client
                self._state = "connected"
                self._message = "Connected to your normal Chrome. Select a tab to start."
                self._version = product
                published = True
        finally:
            if not published:
                # A cancelled concurrent Future is not cleanup confirmation. Keep
                # every transport recorded until its asynchronous stop succeeds.
                for client in list(attempt["clients"]):
                    await client.stop()
                    attempt["clients"].remove(client)
            with self._lock:
                attempt["task"] = None

    def _completed(self, future):
        try:
            future.result()
        except BaseException:
            with self._lock:
                if self._pending is not future:
                    return  # A cancelled earlier handshake cannot release a newer connection's lease.
                if self._closing:
                    return  # The disconnect cleanup owns the lease until its coroutine finishes.
                if self._attempt and self._attempt["clients"]:
                    self._closing = True
                    self._state = "closing"
                    self._message = "Chrome cleanup could not be confirmed. Stop this app before connecting another."
                    return
                self._state = "disconnected"
                self._client = None
                self._lease.release()
                self._message = (
                    "Chrome did not accept the connection. Check that Chrome is open, remote debugging "
                    "is enabled, and approve its native connection prompt; then connect again."
                )

    def connect(self, wait=2.0):
        with self._lock:
            if self._closing:
                return self.status()
            if self._state == "connected":
                return self.status()
            if self._pending is None or self._pending.done():
                try:
                    endpoint = chrome_endpoint()
                except RuntimeError as exc:
                    self._state = "disconnected"
                    self._message = str(exc)
                    return self.status()
                if not self._lease.acquire():
                    self._state = "owned_by_other_experiment"
                    self._message = (
                        "Another CTRL experiment owns Chrome. Stop that app, then click Connect Chrome here. "
                        "The experiments never control your browser simultaneously."
                    )
                    return self.status()
                self._start_loop()
                self._generation += 1
                attempt = {
                    "generation": self._generation,
                    "task": None,
                    "clients": [self._client] if self._client is not None else [],
                }
                self._attempt = attempt
                self._state = "waiting_for_approval"
                self._message = "If Chrome asks to allow remote debugging, approve its native prompt."
                self._pending = asyncio.run_coroutine_threadsafe(self._open(endpoint, attempt), self._loop)
                self._pending.add_done_callback(self._completed)
            future = self._pending
        try:
            future.result(timeout=max(0, wait))
        except Exception:
            # Keep the original handshake running. Retrying it would replace Chrome's prompt.
            pass
        return self.status()

    def status(self):
        with self._lock:
            client = self._client
            handler = getattr(client, "_message_handler_task", None)
            if self._state == "connected" and handler is not None and handler.done():
                self._state = "disconnected"
                self._message = "Chrome disconnected. Connect again to resume."
            return {
                "state": self._state,
                "status": self._state,
                "connected": self._state == "connected",
                "message": self._message,
                "browser": "Google Chrome",
                "version": self._version,
            }

    def cdp(self, method, session_id=None, _response_timeout=15.0, **params):
        with self._call_lock:
            with self._lock:
                if not self.status()["connected"]:
                    raise RuntimeError(self.status()["message"])
                client, loop = self._client, self._loop
            future = asyncio.run_coroutine_threadsafe(client.send_raw(method, params, session_id=session_id), loop)
            try:
                return future.result(timeout=_response_timeout)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise RuntimeError(f"Chrome did not confirm {method}. Inspect the page before a new action.") from exc
            except Exception as exc:
                raise RuntimeError(f"Chrome rejected {method}; the command was not retried.") from exc

    async def _close_attempt(self, attempt, pending):
        if attempt is not None:
            task = attempt["task"]
            if task is not None and not task.done():
                task.cancel()
        if pending is not None:
            try:
                # Do not cancel this concurrent Future: its completion must mean
                # _open's finally block, including client.stop(), has finished.
                await asyncio.wrap_future(pending)
            except (Exception, asyncio.CancelledError):
                pass
        if attempt is not None:
            for client in list(attempt["clients"]):
                await client.stop()
                attempt["clients"].remove(client)

    def _cleanup_completed(self, future):
        with self._lock:
            if self._cleanup_future is not future:
                return
            self._cleanup_future = None
            try:
                future.result()
            except BaseException:
                self._message = "Chrome cleanup could not be confirmed. Stop this app before connecting another."
                return  # Keep the lease and reject reconnect until process shutdown.
            self._pending = None
            self._attempt = None
            self._closing = False
            self._state = "disconnected"
            self._message = "Disconnected from Chrome. Your tabs remain open."
            self._lease.release()

    def disconnect(self, *, wait=5.0):
        """Release only the transport, preserving Chrome and every tab."""
        with self._lock:
            if not self._closing:
                self._generation += 1  # Invalidate success publication before requesting cancellation.
                self._closing = True
                self._client = None
                self._state = "closing"
                self._message = "Disconnecting Chrome; waiting for the previous transport to stop."
                if self._loop is None:
                    self._closing = False
                    self._state = "disconnected"
                    self._lease.release()
                    return
                self._cleanup_future = asyncio.run_coroutine_threadsafe(
                    self._close_attempt(self._attempt, self._pending), self._loop
                )
                self._cleanup_future.add_done_callback(self._cleanup_completed)
            future = self._cleanup_future
        if future is not None:
            try:
                future.result(timeout=max(0, wait))
                self._cleanup_completed(future)
            except Exception:
                pass  # Timeout does not release the lease or permit another connection.


_connection = ChromeConnection()


def connect(wait=2.0):
    return _connection.connect(wait=wait)


def status():
    return _connection.status()


def cdp(method, session_id=None, **params):
    return _connection.cdp(method, session_id=session_id, **params)


def list_tabs():
    if not status()["connected"]:
        return []
    tabs = cdp("Target.getTargets").get("targetInfos", [])
    return [
        {"target_id": t["targetId"], "title": t.get("title", "Untitled"), "url": t.get("url", "")}
        for t in tabs
        if selectable_tab(t)
    ]


def disconnect():
    _connection.disconnect()
