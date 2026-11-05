"""Where the app keeps its settings and session files.

Set the environment variable ``CO2R_HOME`` to put everything in one folder instead
(useful for tests, or for running from a USB stick).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Settings and saved calibration."""
    if os.environ.get("CO2R_HOME"):
        return Path(os.environ["CO2R_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "CO2-Response"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CO2-Response"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "co2response"


def default_sessions_dir() -> Path:
    """Recorded sessions: research data, so somewhere the user will find it."""
    if os.environ.get("CO2R_HOME"):
        return Path(os.environ["CO2R_HOME"]) / "sessions"
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "CO2-Response" / "sessions"
