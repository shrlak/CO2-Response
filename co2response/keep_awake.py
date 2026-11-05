"""Stop the computer sleeping while a device is connected (alarms only work while awake)."""

from __future__ import annotations

import subprocess
import sys

_proc: subprocess.Popen | None = None
_held = False


def acquire() -> None:
    global _proc, _held
    if _held:
        return
    _held = True
    try:
        if sys.platform == "win32":
            import ctypes

            ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        elif sys.platform == "darwin":
            _proc = subprocess.Popen(["caffeinate", "-dis"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # best effort


def release() -> None:
    global _proc, _held
    if not _held:
        return
    _held = False
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif _proc is not None:
            _proc.terminate()
            _proc = None
    except Exception:
        pass
