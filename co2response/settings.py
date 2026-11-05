"""App settings, persisted as JSON in the per-user data folder.

Attribute names are snake_case in Python. On disk (and in the session-file header)
they are camelCase, which keeps files compatible with the earlier version of the app.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .channels import validate_channels
from .paths import data_dir


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def to_dict(obj: Any) -> Any:
    """Dataclass → plain dict with camelCase keys (recursively)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {_camel(f.name): to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    return obj


def from_dict(cls, d: Any):
    """camelCase dict → dataclass, keeping defaults for missing or malformed keys."""
    obj = cls()
    if not isinstance(d, dict):
        return obj
    for f in dataclasses.fields(cls):
        key = _camel(f.name)
        if key not in d:
            continue
        cur = getattr(obj, f.name)
        val = d[key]
        if dataclasses.is_dataclass(cur):
            val = from_dict(type(cur), val)
        elif isinstance(cur, bool):
            if not isinstance(val, bool):
                continue
        elif isinstance(cur, (int, float)) and not (val is None and f.name == "low_mm_hg"):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
        elif isinstance(cur, str) and not isinstance(val, str):
            continue
        setattr(obj, f.name, val)
    return obj


@dataclass
class ProcessingOptions:
    barometric_mm_hg: float = 760
    water_vapor: bool = False
    filter_on: bool = True
    filter_hz: float = 10
    sample_rate_hz: float = 100
    #: flow must pass ± this (L/min) to count as inspiration; 0.05 L/s = 3 L/min
    deadband_lpm: float = 3
    #: breaths shorter than this (s) are treated as noise
    min_breath_sec: float = 1


@dataclass
class AlarmSettings:
    #: what the CO2 limits are compared against: 'etco2' (per breath) or 'live' (waveform)
    source: str = "etco2"
    high_mm_hg: float = 50  # TODO: confirm default limits with the PI / clinicians
    #: None = off
    low_mm_hg: float | None = None
    #: consecutive breaths beyond a limit before alarming (EtCO2 source)
    delay_breaths: int = 2
    #: seconds beyond a limit before alarming (live source)
    delay_sec: float = 5
    hysteresis_mm_hg: float = 2
    #: audio silence length, s (max 120)
    silence_sec: float = 120
    #: 0.1–1; cannot be 0
    volume: float = 0.8
    #: no breath for this long raises a high-priority alarm; 0 = off
    apnea_sec: float = 20


@dataclass
class SimSettings:
    scenario: str = "normal"
    rr: float = 15
    etco2: float = 38
    vt_ml: float = 500


@dataclass
class Settings:
    theme: str = "dark"  # 'dark' | 'light' | 'system'
    co2_unit: str = "mmHg"  # 'mmHg' | '%'
    #: seconds per screen for the sweep
    sweep_sec: float = 12
    #: capnogram full scale, mmHg
    co2_scale_max_mm_hg: float = 60
    #: flow panels ± full scale, L/min
    flow_scale_lpm: float = 60
    show_raw_volts: bool = False
    trend_minutes: float = 30
    processing: ProcessingOptions = field(default_factory=ProcessingOptions)
    alarms: AlarmSettings = field(default_factory=AlarmSettings)
    study_code: str = "STUDY-01"
    baud_rate: int = 115200
    bridge_url: str = "ws://localhost:8765"
    #: reconnect by itself when the same Arduino is plugged back in
    auto_reconnect: bool = True
    #: start recording as soon as an Arduino or bridge connects
    auto_record: bool = False
    sim: SimSettings = field(default_factory=SimSettings)
    #: folder for session files; empty = the default (Documents/CO2-Response/sessions)
    sessions_dir: str = ""
    #: the serial port used last: device name and USB ids, for one-click reconnect
    last_port: str = ""
    last_port_usb: str = ""


DEFAULT_ALARMS = AlarmSettings()
DEFAULT_SETTINGS = Settings()


def sanitize_alarms(a: AlarmSettings) -> AlarmSettings:
    """Enforce the safety limits on alarm settings regardless of what was stored."""

    def clamp(v, lo, hi, d):
        return min(hi, max(lo, v)) if isinstance(v, (int, float)) and math.isfinite(v) else d

    return dataclasses.replace(
        a,
        source="live" if a.source == "live" else "etco2",
        high_mm_hg=clamp(a.high_mm_hg, 5, 150, DEFAULT_ALARMS.high_mm_hg),
        low_mm_hg=None if a.low_mm_hg is None else clamp(a.low_mm_hg, 0, 100, 0),
        delay_breaths=int(round(clamp(a.delay_breaths, 1, 10, 2))),
        delay_sec=clamp(a.delay_sec, 0, 60, 5),
        hysteresis_mm_hg=clamp(a.hysteresis_mm_hg, 0, 10, 2),
        silence_sec=clamp(a.silence_sec, 10, 120, 120),
        volume=clamp(a.volume, 0.1, 1, 0.8),
        apnea_sec=clamp(a.apnea_sec, 0, 120, 20),
    )


class SettingsStore:
    """Reads and writes settings.json and the saved calibration (channels.json)."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = Path(folder) if folder else data_dir()
        self.settings_path = self.folder / "settings.json"
        self.channels_path = self.folder / "channels.json"

    def _read(self, p: Path):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write(self, p: Path, value) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=2)
            os.replace(tmp, p)
        except OSError:
            pass  # read-only or full disk: settings stay for this session only

    def load_settings(self) -> Settings:
        s = from_dict(Settings, self._read(self.settings_path) or {})
        s.alarms = sanitize_alarms(s.alarms)
        return s

    def save_settings(self, s: Settings) -> None:
        self._write(self.settings_path, to_dict(s))

    def load_saved_channels(self) -> dict | None:
        c = self._read(self.channels_path)
        if c is None:
            return None
        try:
            return validate_channels(c)
        except ValueError:
            return None

    def save_channels(self, c: dict) -> None:
        self._write(self.channels_path, c)

    def clear_saved_channels(self) -> None:
        try:
            self.channels_path.unlink()
        except OSError:
            pass
