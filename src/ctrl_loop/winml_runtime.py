"""Windows ML's documented Python projection, with explicit CPU execution.

Keep this context alive until all inference sessions have been released. Creating
the EP catalog does not call ensure_ready_async or acquire vendor providers.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from importlib import metadata
import os
from pathlib import Path
import platform
import sys
from typing import Any


EXPECTED_DISTRIBUTIONS = {
    "wasdk-Microsoft.Windows.AI.MachineLearning": "2.3.0",
    "wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap": "2.3.0",
    "onnxruntime-windowsml": "1.25.2.202605110140",
}
_OTHER_ORT_DISTRIBUTIONS = {
    "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml",
    "onnxruntime-openvino", "onnxruntime-training",
}


def _canonical(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def package_diagnostics() -> dict[str, Any]:
    """Read package metadata without importing any native inference libraries."""
    packages = {
        _canonical(dist.metadata["Name"]): dist.version
        for dist in metadata.distributions()
        if dist.metadata.get("Name")
    }
    expected = {
        name: {"expected": version, "installed": packages.get(_canonical(name))}
        for name, version in EXPECTED_DISTRIBUTIONS.items()
    }
    conflicts = {
        name: packages[name]
        for name in sorted(_OTHER_ORT_DISTRIBUTIONS)
        if name in packages
    }
    return {"distributions": expected, "conflicting_ort_distributions": conflicts}


def _file_version(path: str) -> str | None:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buffer):
        return None
    info = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buffer, "\\", ctypes.byref(info), ctypes.byref(length)):
        return None
    # VS_FIXEDFILEINFO begins with signature, structure version, file MS/LS.
    words = ctypes.cast(info, ctypes.POINTER(wintypes.DWORD))
    if length.value < 16 or words[0] != 0xFEEF04BD:
        return None
    major_minor, build_revision = words[2], words[3]
    return ".".join(str(value) for value in (
        major_minor >> 16, major_minor & 0xFFFF,
        build_revision >> 16, build_revision & 0xFFFF,
    ))


def loaded_native_modules() -> list[dict[str, Any]]:
    """Return actual modules loaded in this process, not a filesystem inventory."""
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    process = kernel32.GetCurrentProcess()
    capacity = 256
    while True:
        modules = (wintypes.HMODULE * capacity)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(process, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value <= ctypes.sizeof(modules):
            break
        capacity = needed.value // ctypes.sizeof(wintypes.HMODULE) + 16
    result = []
    for module in modules[: needed.value // ctypes.sizeof(wintypes.HMODULE)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if not psapi.GetModuleFileNameExW(process, module, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        path = buffer.value
        name = Path(path).name.lower()
        if any(part in name for part in ("onnxruntime", "machinelearning", "bootstrap", "directml")):
            result.append({"name": Path(path).name, "path": path, "file_version": _file_version(path)})
    return sorted(result, key=lambda item: item["path"].lower())


class WindowsMLRuntime:
    """Activate the App Runtime, then use its documented Python ML projection."""

    def __init__(self) -> None:
        self._bootstrap_handle: Any = None
        self._catalog: Any = None
        self._ort: Any = None
        self._report: dict[str, Any] = {
            "python": sys.version,
            "python_executable": sys.executable,
            "architecture": platform.machine(),
            "windows": platform.platform(),
            **package_diagnostics(),
        }

    def __enter__(self) -> WindowsMLRuntime:
        if os.name != "nt":
            raise RuntimeError("Windows ML requires Windows.")
        if self._bootstrap_handle is not None:
            raise RuntimeError("WindowsMLRuntime is already active.")
        mismatched = {
            name: versions for name, versions in self._report["distributions"].items()
            if versions["installed"] != versions["expected"]
        }
        if mismatched or self._report["conflicting_ort_distributions"]:
            raise RuntimeError(f"Windows ML environment does not match pinned distributions: {mismatched}; conflicting ORT distributions: {self._report['conflicting_ort_distributions']}")

        from winui3.microsoft.windows.applicationmodel.dynamicdependency import bootstrap

        # Defaults come from the compiled projection, not inferred package names.
        self._report["bootstrap"] = {
            "module": bootstrap.__file__,
            "release_version": bootstrap.initialize.__defaults__[0],
            "minimum_runtime_version": bootstrap.initialize.__defaults__[1],
        }
        self._bootstrap_handle = bootstrap.initialize(options=bootstrap.InitializeOptions.NONE)
        self._bootstrap_handle.__enter__()
        try:
            import winui3.microsoft.windows.ai.machinelearning as winml
            self._catalog = winml.ExecutionProviderCatalog.get_default()
            self._report["catalog_created"] = True
            self._report["projection_module"] = winml.__file__
            self._report["catalog_providers"] = [
                {"name": provider.name, "ready_state": provider.ready_state.name,
                 "library_path": provider.library_path}
                for provider in self._catalog.find_all_providers()
            ]
            self._report["provider_downloads_requested"] = False

            import onnxruntime as ort

            self._ort = ort
            owner = metadata.distribution("onnxruntime-windowsml")
            expected_module = Path(owner.locate_file("onnxruntime/__init__.py")).resolve()
            if Path(ort.__file__).resolve() != expected_module:
                raise RuntimeError(f"onnxruntime import does not come from the pinned Windows ML distribution: {ort.__file__}")
            self._report["onnxruntime"] = {
                "version": ort.__version__,
                "module": ort.__file__,
                "distribution_owner": owner.metadata["Name"],
                "available_providers": ort.get_available_providers(),
                "build_info": ort.get_build_info(),
            }
            self._report["loaded_native_modules"] = loaded_native_modules()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def cpu_session(self, model_path: str | Path) -> Any:
        """Create an explicit CPU-only session; caller releases it before exit."""
        if self._ort is None:
            raise RuntimeError("Enter WindowsMLRuntime before creating a session.")
        model = Path(model_path).resolve(strict=True)
        session = self._ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        session.disable_fallback()
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError(f"Unexpected session providers: {session.get_providers()}")
        return session

    def diagnostics(self) -> dict[str, Any]:
        report = dict(self._report)
        try:
            report["loaded_native_modules"] = loaded_native_modules()
        except OSError as exc:
            report["native_module_enumeration_error"] = str(exc)
        return report

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._catalog = None
        self._ort = None
        if self._bootstrap_handle is not None:
            handle, self._bootstrap_handle = self._bootstrap_handle, None
            handle.__exit__(exc_type, exc, traceback)
