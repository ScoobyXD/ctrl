"""Process lifetime lease: only one CTRL experiment may connect to normal Chrome."""

import ctypes
import hashlib
import os
import tempfile
import threading
from pathlib import Path

LEASE_NAME = "Local\\CTRL.NormalChrome.ExperimentOwner.v1"


class BrowserLease:
    def __init__(self, name=LEASE_NAME):
        self.name = name
        self._handle = None
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            if self._handle is not None:
                return True
            if os.name == "nt":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
                kernel.CreateMutexW.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                ctypes.set_last_error(0)
                handle = kernel.CreateMutexW(None, False, self.name)
                if not handle:
                    raise RuntimeError("Could not reserve Chrome for this experiment.")
                if ctypes.get_last_error() == 183:  # Existing object belongs to another live process.
                    kernel.CloseHandle(handle)
                    return False
                # Object existence, not thread ownership, is the lease. CloseHandle
                # is valid from the server's shutdown thread; process exit also releases it.
                self._handle = (kernel, handle)
            else:
                import fcntl

                digest = hashlib.sha256(f"{os.getuid()}:{self.name}".encode()).hexdigest()
                path = Path(tempfile.gettempdir()) / f"ctrl-chrome-{digest}.lock"
                stream = path.open("a+b")
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    return False
                self._handle = stream
            return True

    def release(self):
        with self._lock:
            handle, self._handle = self._handle, None
            if handle is None:
                return
            if os.name == "nt":
                handle[0].CloseHandle(handle[1])
            else:
                handle.close()
