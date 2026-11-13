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

from .channels import HARDWARE, is_digital, validate_channels
from .paths import data_dir


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


#: number settings that may also be None ("off", or "not entered")
NULLABLE = frozenset({"low_mm_hg", "stop_mm_hg", "height_cm", "weight_kg", "age_years"})


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
        elif f.name in NULLABLE:
            # a number, or None for "off" / "not entered"
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
                continue
        elif isinstance(cur, (int, float)):
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
    #: remove single-sample spikes (Hampel filter) before anything else
    despike: bool = True
    #: leave noisy, artifact and outlier breaths out of the numbers, graphs and EtCO2 alarms
    reject_bad_breaths: bool = True
    #: how late the CO2 signal is behind the flow signals (sample-line transport and
    #: analyser response), ms. The flow channels are delayed by this much so that
    #: each breath's CO2 lines up with its flow. 0 for a mainstream sensor.
    co2_delay_ms: float = 0
    #: report pneumotach flow and volumes at body conditions (BTPS: 37 °C, saturated)
    #: instead of as measured at room conditions (ATPS)
    volume_btps: bool = False
    #: room temperature for the BTPS correction, °C
    ambient_temp_c: float = 22
    #: pneumotach flow is multiplied by this between the Rebreathing and End-of-test
    #: markers: a pneumotach calibrated in air reads differently in the bag's gas mix.
    #: 1 = no correction
    rebreathing_gas_factor: float = 1


@dataclass
class AlarmSettings:
    #: what the CO2 limits are compared against: 'etco2' (per breath) or 'live' (waveform)
    source: str = "etco2"
    #: warning limit. TODO: confirm the default limits with the PI / clinicians (the
    #: team's design is a warning near 60 mmHg and a stop alert at 65; CHOP ends the
    #: test at 60-65)
    high_mm_hg: float = 60
    #: stop limit: a high-priority alarm as soon as CO2 stays at or above it for
    #: ``stop_delay_sec`` (checked on every sample, not once per breath). None = off
    stop_mm_hg: float | None = 65
    stop_delay_sec: float = 0.5
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
    #: rebreathing for longer than this raises a medium alarm; 0 = off
    rebreathing_max_sec: float = 300
    #: advisory when inspired and expired volume differ by more than this (a leak, or
    #: a drifting pneumotach zero), % of inspired volume; 0 = off
    leak_pct: float = 20
    #: let the app switch the valve through the board's valve output: to the bag at
    #: Start rebreathing, back to room air at End test, at the stop alarm, on apnea and
    #: at an emergency stop. Off: the operator turns a manual valve.
    auto_valve: bool = False
    #: advisory when a real device is connected and a channel was calibrated longer
    #: ago than this, or never, hours; 0 = off
    calibration_max_age_h: float = 24


@dataclass
class Spo2Settings:
    """SpO2 as a safety channel: a pulse oximeter's analog output on the spare ADC
    input. Off until an oximeter is connected.

    TODO: the scaling is a placeholder (1 V = 100 %); set it from the oximeter's manual."""

    enabled: bool = False
    #: which value of each data line it is (the ADS1115's A3 is the fourth: 3)
    index: int = 3
    #: SpO2 % = gain x volts + offset
    gain_pct_per_v: float = 100
    offset_pct: float = 0
    #: high-priority alarm when SpO2 stays below this for ``low_delay_sec``; 0 = off
    low_pct: float = 90
    low_delay_sec: float = 10


@dataclass
class CapnoSettings:
    """A Medtronic Capnostream 35 as the CO2 source: its CO2 waveform (20 Hz, Microstream
    sidestream sampling) replaces the board's own CO2 sensor, and is lined up with the
    Arduino's flow. See ``co2response/sources/capnostream.py`` and docs/capnostream.md."""

    enabled: bool = False
    #: where it is: a serial port (COM4, /dev/ttyUSB0), a network address
    #: (socket://192.168.1.50:4001) or "sim" for a simulated Capnostream
    port: str = ""
    baud: int = 9600
    #: how its ASCII lines are laid out: "" = find KEY=value pairs by themselves, or the
    #: fields in order, e.g. "time,co2,etco2,fico2,rr,spo2,pr" (co2 = a waveform sample,
    #: co2* = every remaining field is a waveform sample, skip = ignore that field)
    fields: str = ""
    #: the unit its CO2 values are in: 'mmHg' | '%' | 'kPa'
    units: str = "mmHg"
    #: the gas reaches its sensor this long after leaving the airway (sample line and
    #: analyser; about 2.5–3 s for Microstream), ms. The flow is delayed to match.
    delay_ms: float = 2700


@dataclass
class TestSettings:
    """The rebreathing test: how the response is analysed, and the subject's size.

    Height, weight and age are written into the session header so the slope can be
    normalized for body size afterwards. They are not identifiers, but never enter a
    name, MRN or date of birth anywhere."""

    __test__ = False  # not a pytest class

    #: breaths in the first seconds after the Rebreathing marker (the coached deep
    #: breaths, and the bag, lungs and blood coming to equilibrium) are not fitted
    response_skip_sec: float = 30
    #: calibration at the start of rebreathing: the first breaths (at least this many) and
    #: seconds (at least this long) after the Rebreathing marker are watched to settle the
    #: starting point, shown but not fitted; the response is recorded after them. The
    #: calibration is part of the left-out start: whichever of it and ``response_skip_sec``
    #: lasts longer decides where the fit begins. 0 and 0 = no calibration.
    calibration_breaths: int = 4
    calibration_sec: float = 15
    height_cm: float | None = None
    weight_kg: float | None = None
    age_years: float | None = None


@dataclass
class SimSettings:
    """The simulator's settings. The simulation panel (``co2response/sim/controls.py``)
    sets every one of them; the defaults are a normal adult at rest."""

    scenario: str = "normal"
    rr: float = 15
    etco2: float = 38
    vt_ml: float = 500
    #: inspired CO2, mmHg
    fico2: float = 0
    #: I:E ratio as 1:x
    ie_ratio: float = 2
    #: airway obstruction (capnogram shape), %
    obstruction_pct: float = 0
    #: CO2 challenge (rebreathing): CO2 climbs at this rate up to the maximum
    ramp: bool = False
    ramp_mm_hg_per_min: float = 15
    ramp_max_mm_hg: float = 62
    #: ventilatory response to the rising CO2 (1 = normal, 0 = none)
    drive: float = 1
    apnea: bool = False
    co2_disconnected: bool = False
    flow_disconnected: bool = False
    pneumo_disconnected: bool = False
    stop_data: bool = False
    #: noise multiplier, ADC spikes %, partial breaths %, lost/corrupt serial lines %
    noise: float = 1
    spike_pct: float = 0
    partial_pct: float = 0
    line_error_pct: float = 0
    #: the flow sensor reads gain % of the true flow, plus an offset
    flow_gain_pct: float = 100
    flow_offset_lpm: float = 0
    #: share of the gap to a new EtCO2 closed each breath: 1 = the next breath is at the
    #: new value (the panel's default), 0.5 = halfway each breath (the original model)
    settle: float = 1
    #: the subject's oxygen saturation, % (falls during apnea). Simulated only while
    #: the SpO2 channel is switched on.
    spo2: float = 98
    #: breath-to-breath variability of tidal volume, % (0 = every breath alike)
    variability_pct: float = 0
    #: simulated seconds per real second (1 = real time). Not part of a situation.
    speed: float = 1
    #: record a whole simulated test as a session, so the review charts and the PDF
    #: report can be made from it too. Not part of a situation.
    record_tests: bool = False
    #: the situation last chosen in the simulation panel, and whether a value was changed since
    preset: str = "normal"
    adjusted: bool = False


def default_hardware() -> str:
    """The board a new installation expects: 'digital' (the Uno R4 board with the SFM3304-D
    and SprintIR sensors) unless the CO2R_HARDWARE environment variable says 'analog'."""
    h = os.environ.get("CO2R_HARDWARE", "").strip().lower()
    return h if h in HARDWARE else "digital"


@dataclass
class Settings:
    theme: str = "dark"  # 'dark' | 'light' | 'system'
    #: the board the simulator emulates and a new connection expects until the device
    #: says what it is: 'digital' (Uno R4, SFM3304-D flow + SprintIR CO2, one flow
    #: waveform) or 'analog' (ADS1115 with a capnograph, a flow sensor and a pneumotach)
    hardware: str = field(default_factory=default_hardware)
    co2_unit: str = "mmHg"  # 'mmHg' | '%'
    #: seconds per screen for the sweep
    sweep_sec: float = 12
    #: capnogram full scale, mmHg (the default shows the 60 mmHg warning and 65 mmHg stop limits)
    co2_scale_max_mm_hg: float = 80
    #: flow panels ± full scale, L/min
    flow_scale_lpm: float = 60
    show_raw_volts: bool = False
    trend_minutes: float = 30
    #: what the trend tab plots against time: 'etco2' | 'mv' | 'rr'
    trend_series: str = "etco2"
    processing: ProcessingOptions = field(default_factory=ProcessingOptions)
    alarms: AlarmSettings = field(default_factory=AlarmSettings)
    test: TestSettings = field(default_factory=TestSettings)
    spo2: Spo2Settings = field(default_factory=Spo2Settings)
    capno: CapnoSettings = field(default_factory=CapnoSettings)
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


def _clamp(v, lo, hi, d):
    return min(hi, max(lo, v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else d


def sanitize_alarms(a: AlarmSettings) -> AlarmSettings:
    """Enforce the safety limits on alarm settings regardless of what was stored."""
    clamp = _clamp
    high = clamp(a.high_mm_hg, 5, 150, DEFAULT_ALARMS.high_mm_hg)
    return dataclasses.replace(
        a,
        source="live" if a.source == "live" else "etco2",
        high_mm_hg=high,
        # the stop limit is never below the warning limit
        stop_mm_hg=None if a.stop_mm_hg is None else max(high, clamp(a.stop_mm_hg, 5, 150, 65)),
        stop_delay_sec=clamp(a.stop_delay_sec, 0, 5, 0.5),
        low_mm_hg=None if a.low_mm_hg is None else clamp(a.low_mm_hg, 0, 100, 0),
        delay_breaths=int(round(clamp(a.delay_breaths, 1, 10, 2))),
        delay_sec=clamp(a.delay_sec, 0, 60, 5),
        hysteresis_mm_hg=clamp(a.hysteresis_mm_hg, 0, 10, 2),
        silence_sec=clamp(a.silence_sec, 10, 120, 120),
        volume=clamp(a.volume, 0.1, 1, 0.8),
        apnea_sec=clamp(a.apnea_sec, 0, 120, 20),
        rebreathing_max_sec=clamp(a.rebreathing_max_sec, 0, 1800, 300),
        leak_pct=clamp(a.leak_pct, 0, 100, 20),
        calibration_max_age_h=clamp(a.calibration_max_age_h, 0, 24 * 365, 24),
    )


def sanitize_processing(p: ProcessingOptions) -> ProcessingOptions:
    return dataclasses.replace(
        p,
        co2_delay_ms=_clamp(p.co2_delay_ms, 0, 5000, 0),
        ambient_temp_c=_clamp(p.ambient_temp_c, 0, 40, 22),
        rebreathing_gas_factor=_clamp(p.rebreathing_gas_factor, 0.5, 1.5, 1),
    )


def sanitize_spo2(o: Spo2Settings) -> Spo2Settings:
    return dataclasses.replace(
        o,
        index=int(_clamp(o.index, 0, 7, 3)),
        gain_pct_per_v=_clamp(o.gain_pct_per_v, -1000, 1000, 100) or 100,
        offset_pct=_clamp(o.offset_pct, -500, 500, 0),
        low_pct=_clamp(o.low_pct, 0, 100, 90),
        low_delay_sec=_clamp(o.low_delay_sec, 0, 120, 10),
    )


CAPNO_UNITS = ("mmHg", "%", "kPa")


def sanitize_capno(c: CapnoSettings) -> CapnoSettings:
    return dataclasses.replace(
        c,
        baud=int(_clamp(c.baud, 300, 921600, 9600)),
        units=c.units if c.units in CAPNO_UNITS else "mmHg",
        delay_ms=_clamp(c.delay_ms, 0, 10000, 2700),
        port=str(c.port or "").strip(),
        fields=str(c.fields or "").strip(),
    )


def sanitize_test(t: TestSettings) -> TestSettings:
    def opt(v, lo, hi):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else None

    return dataclasses.replace(
        t,
        response_skip_sec=_clamp(t.response_skip_sec, 0, 300, 30),
        calibration_breaths=int(round(_clamp(t.calibration_breaths, 0, 20, 4))),
        calibration_sec=_clamp(t.calibration_sec, 0, 120, 15),
        height_cm=opt(t.height_cm, 30, 250),
        weight_kg=opt(t.weight_kg, 1, 400),
        age_years=opt(t.age_years, 0, 120),
    )


class SettingsStore:
    """Reads and writes settings.json and the saved calibration (channels.json)."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = Path(folder) if folder else data_dir()
        self.settings_path = self.folder / "settings.json"
        self.channels_path = self.folder / "channels.json"
        #: the saved calibration of the Uno R4 board with digital sensors
        self.digital_channels_path = self.folder / "channels-digital.json"
        #: why the last save failed (None when it worked): the monitor shows it, so a
        #: calibration or limit that was not kept is never a silent surprise
        self.save_error: str | None = None

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
            self.save_error = None
        except OSError as e:
            # read-only or full disk: settings stay for this session only
            self.save_error = f"{p.name}: {e.strerror or e}"

    def load_settings(self) -> Settings:
        s = from_dict(Settings, self._read(self.settings_path) or {})
        s.alarms = sanitize_alarms(s.alarms)
        s.processing = sanitize_processing(s.processing)
        s.test = sanitize_test(s.test)
        s.spo2 = sanitize_spo2(s.spo2)
        s.capno = sanitize_capno(s.capno)
        if s.trend_series not in ("etco2", "mv", "rr"):
            s.trend_series = "etco2"
        if s.hardware not in HARDWARE:
            s.hardware = default_hardware()
        return s

    def save_settings(self, s: Settings) -> None:
        self._write(self.settings_path, to_dict(s))

    def _channels_file(self, hardware: str) -> Path:
        return self.digital_channels_path if hardware == "digital" else self.channels_path

    def load_saved_channels(self, hardware: str = "analog") -> dict | None:
        c = self._read(self._channels_file(hardware))
        if c is None:
            return None
        try:
            c = validate_channels(c)
        except ValueError:
            return None
        return c if is_digital(c) == (hardware == "digital") else None

    def save_channels(self, c: dict) -> None:
        """Each board keeps its own calibration file."""
        self._write(self._channels_file("digital" if is_digital(c) else "analog"), c)

    def clear_saved_channels(self, hardware: str = "analog") -> None:
        try:
            self._channels_file(hardware).unlink()
        except OSError:
            pass
