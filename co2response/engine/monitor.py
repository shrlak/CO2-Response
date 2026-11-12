"""The monitor engine: source → parser → processing → alarms → recorder.

It has no user interface. The window calls ``poll()`` about 50 times a second (to read
the source) and ``tick()`` 4 times a second (alarm checks, audio, saving), and reads a
``snapshot()`` to draw. The alarm checks run here, on every sample and breath, not in
the drawing code. Everything can be driven from tests with a fake clock.
"""

from __future__ import annotations

import bisect
import collections
import dataclasses
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import __version__, keep_awake
from ..alarms.audio import AlarmAudio
from ..alarms.engine import AlarmEngine, AlarmLogEntry, AlarmRecord
from ..channels import (
    CHANNEL_IDS,
    DEFAULT_CHANNELS,
    DEFAULT_DIGITAL_CHANNELS,
    clone_channels,
    is_digital,
    load_default_channels,
    single_flow,
    validate_channels,
)
from ..jsfmt import iso_ms, parse_iso_ms
from ..paths import default_sessions_dir
from ..processing.breath import Breath
from ..processing.calibration import invert_calibration
from ..processing.metrics import (
    BASELINE_LABEL,
    EMERGENCY_LABEL,
    END_LABEL,
    REBREATHING_LABEL,
    CO2Response,
    TestWindow,
    co2_response,
    co2_response_summary,
    etco2_median,
    marker_kind,
    median,
    minute_ventilation,
    respiratory_rate,
    stability,
    test_window,
)
from ..processing.pipeline import Pipeline, Processed
from ..protocol import SeqTracker, parse_line
from ..settings import (
    ProcessingOptions,
    Settings,
    SettingsStore,
    Spo2Settings,
    sanitize_alarms,
    sanitize_processing,
    sanitize_spo2,
    sanitize_test,
    to_dict,
)
from ..sim.controls import (
    SITUATION_BY_ID,
    TEST_BASELINE_SEC,
    TEST_REACTION_SEC,
    TEST_RECOVERY_SEC,
    model_params,
    situation_settings,
    switch_patch,
)
from ..sim.core import Encoders
from ..sources.base import MarkerEvent, Source, StatusEvent
from ..sources.bridge import BridgeSource
from ..sources.capnostream import CapnoDecoder, CapnoMerge, CapnostreamSource, SimulatedCapnostream, mmhg_to_line_pct
from ..sources.replay import ReplaySource
from ..sources.serial_source import PortInfo, SerialSource, list_ports
from ..sources.simulator import SimulatorSource
from ..storage.library import SessionLibrary, SessionRecord
from ..storage.recorder import SessionRecorder
from ..storage.session_file import ParsedSession, SessionMeta, session_file_name
from .link_test import CheckResult, LinkStats, LinkTestCollector, analyze_link_test

NAN = float("nan")
MAX_TREND = 20000
MAX_TRACE = 4 * 3600 * 2  # 4 h at 2 Hz
MAX_WAVE = 6000
#: no "Capnostream not answering" alarm before its first CO2 for this long after connecting (ms)
CAPNO_WARMUP_MS = 45_000
TECHNICAL = ("noData", "apnea", "railCo2", "railFlow", "railPneumo", "adcError", "dropped", "checksum", "noisyBreaths", "leak", "calibration", "testTime", "spo2NoSignal")
#: the alarms that turn the valve back to room air when the app switches the valve
AUTO_STOP = {"co2Stop": "stop limit", "apnea": "apnea", "spo2Low": "low SpO2"}
#: breaths compared for the inspired-against-expired volume check
LEAK_BREATHS = 5
#: how often the host tells the device it is alive (firmware with a watchdog), ms
HEARTBEAT_MS = 1000
#: a valve command the board has not answered within this long was not heard, ms
VALVE_ANSWER_MS = 2000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mss(sec: float) -> str:
    t = max(0, int(sec))
    return f"{t // 60}:{t % 60:02d}"


def _age(ms: float) -> str:
    h = ms / 3600_000
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.0f} days"


def simulator_encoders(spo2: Spo2Settings | None = None, digital: bool = False) -> Encoders:
    """The simulator's "true" sensors: the bundled default calibration, inverted (for the
    digital board that is 1:1, since its lines carry units). With the SpO2 channel on,
    it also has an oximeter with that channel's scaling."""
    d = DEFAULT_DIGITAL_CHANNELS if digital else DEFAULT_CHANNELS
    c = d["channels"]
    lo, hi = d["adc"]["railLowV"], d["adc"]["railHighV"]
    oximeter = None
    if spo2 is not None and spo2.enabled:
        gain, offset = spo2.gain_pct_per_v or 100.0, spo2.offset_pct

        def oximeter(pct: float) -> float:
            return (pct - offset) / gain

    return Encoders(
        co2=lambda pct: invert_calibration(c["co2"]["calibration"], pct, lo, hi),
        flow=lambda lpm: invert_calibration(c["flow"]["calibration"], lpm, lo, hi),
        pneumo=lambda pa: invert_calibration(c["pneumo"]["calibration"], pa, lo, hi),
        spo2=oximeter,
    )


def sim_clipping(core, exp: dict) -> list[str]:
    """The simulated sensors whose signal would pass the ADC rails at the current
    peak flows (so the values set in the simulation panel cannot all be measured)."""
    if "insp_flow" not in exp:  # apnea: nothing flows
        return []
    p = core.model.params
    lo, hi = core._rail_low_v + 0.01, core._rail_high_v - 0.01
    enc = core.encoders
    out = []
    flows = (exp["insp_flow"], -exp["exp_flow"])
    if not p.flow_disconnected and any(not lo < enc.flow(p.flow_gain * q + p.flow_offset_lpm) < hi for q in flows):
        out.append("flow sensor")
    if not p.pneumo_disconnected and any(not lo < enc.pneumo(q / p.k_pneumo) < hi for q in flows):
        out.append("pneumotach")
    return out


@dataclass
class Numbers:
    co2_live: float = NAN
    etco2: float = NAN
    etco2_median: float = NAN
    rr: float = NAN
    fico2: float = NAN
    flow: float = NAN
    peak_flow: float = NAN
    pneumo_flow: float = NAN
    pneumo_pa: float = NAN
    vt: float = NAN
    mv: float = NAN
    volts: list[float] = field(default_factory=list)
    #: why the latest breath was left out of the numbers, if it was
    rejected: str = ""
    #: expired volume of the latest breath, mL
    vte: float = NAN
    #: (inspired − expired) / inspired volume over the last few breaths, %
    volume_mismatch_pct: float = NAN
    #: how long after expiration begins the CO2 rises, s (median of recent breaths)
    co2_lag_sec: float = NAN
    #: oxygen saturation, % (NaN without an oximeter, or with no reading for 5 s)
    spo2: float = NAN


@dataclass
class TrendPoint:
    #: wall clock, s
    t: float
    etco2: float
    mv: float
    vt: float
    rr: float
    #: the breath's quality; only "ok" points are drawn (see processing/denoise.py)
    quality: str = "ok"
    #: the breath's own minute ventilation (VT x 60 / period): what the response is fitted to
    mv_breath: float = NAN
    #: identifies the point (for taking it out of the response fit by hand)
    key: int = 0
    #: the breath's number in the recording, if one was running
    rec_n: int | None = None
    #: taken out of the response fit by hand
    excluded: bool = False


@dataclass
class Marker:
    id: int
    #: wall clock, ms
    t: float
    label: str


@dataclass
class TestState:
    """Where the rebreathing test stands, from its markers (see ``test_window``)."""

    __test__ = False  # not a pytest class

    #: 'idle' | 'baseline' | 'rebreathing' | 'ended'
    phase: str
    window: TestWindow
    #: seconds in the current phase (rebreathing: since the valve was turned)
    elapsed_sec: float = 0.0
    #: the rebreathing time limit, s (0 = none)
    limit_sec: float = 0.0
    #: baseline only: is breathing steady enough to start, and why
    stable: bool = False
    stable_text: str = ""
    #: one line for the screen
    text: str = ""

    # which of the three test keys make sense now
    @property
    def can_baseline(self) -> bool:
        return self.phase != "rebreathing"

    @property
    def can_start(self) -> bool:
        return self.phase != "rebreathing"

    @property
    def can_end(self) -> bool:
        return self.phase == "rebreathing"

    @property
    def can_emergency(self) -> bool:
        return self.phase == "rebreathing"


@dataclass
class LogLine:
    t: float
    text: str
    level: str = "info"  # 'info' | 'alarm' | 'error'


@dataclass
class RecordingState:
    active: bool
    started_at: float
    file_name: str
    bytes: int
    stored: bool
    write_error: str | None
    library_version: int
    folder: str


@dataclass
class Snapshot:
    now: float
    status: str  # 'idle' | 'connecting' | 'connected' | 'error'
    status_detail: str
    source_kind: str | None
    source_label: str
    info: dict[str, str] | None
    rate_hz: float
    measured_rate_hz: float
    received: int
    dropped: int
    drop_rate: float
    bad_lines: int
    numbers: Numbers
    stale: dict[str, bool]
    breath_stale: bool
    alarms: list[AlarmRecord]
    silence_ms: float
    audio_armed: bool
    audio_backend: str
    recording: RecordingState
    frozen: bool
    trend_version: int
    settings: Settings
    channels: dict
    using_file_calibration: bool
    replay_progress: float | None
    #: a previously used Arduino that is plugged in and can be reopened in one click
    known_port: str | None
    awaiting_reconnect: bool
    i2c_scan: str | None
    test: TestState = field(default_factory=lambda: TestState("idle", TestWindow(None, None, None)))
    #: where the board's valve output stands: 'bag', 'air', or None if the device has none
    valve: str | None = None
    #: the board in use: 'digital' (Uno R4, SFM3304-D + SprintIR, units on the line) or
    #: 'analog' (ADS1115, volts on the line)
    hardware: str = "analog"
    #: one flow sensor does everything, so there is one flow waveform, not two
    single_flow: bool = False
    #: the Capnostream's own numbers (etco2, fico2, rr, spo2, pr) while one is the CO2
    #: source, from its last 5 s; None without one
    capno: dict | None = None
    #: when the program started (same clock as ``now``)
    started_at: float = 0.0


@dataclass
class StopResult:
    file_name: str
    path: Path | None
    rec: SessionRecord | None
    error: str | None = None


@dataclass
class CaptureResult:
    volts: float
    value: float
    n: int


class HardwareCheckJob:
    """A running hardware check. The engine finishes it after ``seconds``; ``cancel`` ends it early."""

    def __init__(self, engine: "MonitorEngine", seconds: float) -> None:
        self.engine = engine
        self.seconds = seconds
        self.collector = LinkTestCollector(engine.effective_channels)
        self._start = (engine._seq.received, engine._seq.dropped, engine._bad_lines, engine._adc_error_count)
        self._off = engine.on_sample(lambda p: self.collector.sample(p.t_sec, p.volts))
        self.t0 = engine.clock()
        self.results: list[CheckResult] | None = None

    @property
    def done(self) -> bool:
        return self.results is not None

    @property
    def progress(self) -> float:
        if self.done:
            return 1.0
        return min(1.0, (self.engine.clock() - self.t0) / (self.seconds * 1000))

    def cancel(self) -> None:
        self._finish()

    def _update(self) -> None:
        if not self.done and self.progress >= 1:
            self._finish()

    def _finish(self) -> None:
        if self.done:
            return
        e = self.engine
        self._off()
        received, dropped, bad, adc = self._start
        stats = LinkStats(
            seconds=(e.clock() - self.t0) / 1000,
            span_sec=self.collector.span_sec,
            received=e._seq.received - received,
            dropped=e._seq.dropped - dropped,
            bad_lines=e._bad_lines - bad,
            info=e._info,
            adc_errors=e._adc_error_count - adc + (1 if e.alarms.is_active("adcError") else 0),
            i2c_scan=e._i2c_scan,
            adc_detail=e._adc_detail,
            channels=self.collector.stats(),
        )
        self.results = analyze_link_test(stats, e.effective_channels, e.settings.processing.barometric_mm_hg)
        e._jobs.discard(self)
        e._add_log(f"Hardware check ({self.seconds:g} s): " + "; ".join(f"{r.label} {r.status}" for r in self.results))


class MonitorEngine:
    def __init__(
        self,
        store: SettingsStore | None = None,
        audio=None,
        clock: Callable[[], float] = _now_ms,
        port_lister: Callable[[], list[PortInfo]] = list_ports,
        sessions_dir: Path | None = None,
        prevent_sleep: bool = True,
        library_factory: Callable[..., SessionLibrary] = SessionLibrary,
    ) -> None:
        self.clock = clock
        #: when the program started (the engine's clock, ms): the header shows how long it has run
        self.started_at = clock()
        self.store = store or SettingsStore()
        self.settings = self.store.load_settings()
        #: the saved calibration of each board; ``channels`` is the one in use
        self._channels_of: dict[str, dict] = {
            "analog": self.store.load_saved_channels() or clone_channels(DEFAULT_CHANNELS),
            "digital": self.store.load_saved_channels("digital") or clone_channels(DEFAULT_DIGITAL_CHANNELS),
        }
        #: the board a replayed recording was made with
        self._replay_hardware: str | None = None
        self._info: dict[str, str] | None = None
        self._source: Source | None = None
        self.alarms = AlarmEngine(self.settings.alarms, self._on_alarm_log)
        self.audio = audio if audio is not None else AlarmAudio()
        self.audio.volume = self.settings.alarms.volume
        self._sessions_override = Path(sessions_dir) if sessions_dir else None
        self._library_factory = library_factory
        self.library = library_factory(self.sessions_dir, clock)
        self.recorder = SessionRecorder(self.library, clock)
        self.port_lister = port_lister
        self.prevent_sleep = prevent_sleep
        #: bumps whenever the session library changes, so lists can refresh
        self.library_version = 0
        #: the advisory shown while no sound output is armed
        self.audio_off_message = "Audible alarm NOT armed: no sound output found. Press Arm audio."
        #: called with the message when a high-priority alarm starts (the window flashes)
        self.high_alarm_listeners: list[Callable[[str], None]] = []

        self._file_channels: dict | None = None
        self._known_port: PortInfo | None = None
        self._awaiting_reconnect = False
        self._reconnect_at: float | None = None
        self._last_port_scan = -math.inf
        self._i2c_scan: str | None = None
        #: the reason a Python DAQ driver gives after ADC_NOT_FOUND (co2r-daq)
        self._adc_detail: str | None = None
        self._adc_error_count = 0
        self._status = "idle"
        self._status_detail = ""
        self._seq = SeqTracker()
        self._bad_lines = 0
        self._bad_times: collections.deque[float] = collections.deque()
        self._last_data_wall = 0.0
        self._last_valid_wall = {cid: 0.0 for cid in CHANNEL_IDS}
        self._last_breath_wall = 0.0
        self._last_good_wall = 0.0
        self._recent_quality: collections.deque[str] = collections.deque(maxlen=5)
        self._trend_of: dict[int, TrendPoint] = {}
        self._last: Processed | None = None
        self._flow_peak_cur = -math.inf
        self._flow_peak_last = NAN
        self._rate_count = 0
        self._rate_window_start = 0.0
        self._measured_rate = 0.0
        self._last_high_tone = 0.0
        self._last_med_tone = 0.0
        self._trace_max = -math.inf
        self._trace_start = 0.0
        self._connected_at = 0.0
        self._handshake_retried = False
        self._last_heartbeat = 0.0
        self._trend_key = 0
        #: a whole simulated test in progress (see ``_start_sim_test``)
        self._sim_test: dict | None = None
        #: the board's valve output as last commanded: True = to the bag
        self._valve = False
        #: when each valve command not yet answered by the board was sent
        self._valve_asked: collections.deque[float] = collections.deque()
        self._spo2_low_since: float | None = None
        self._spo2_valid_wall = 0.0
        self._save_error_logged = False
        #: a Capnostream 35 as the CO2 source (settings.capno): its own source, and the
        #: merge that puts its CO2 into the board's data lines
        self._capno_src: Source | None = None
        self._merge: CapnoMerge | None = None
        self._capno_on = False
        #: what the board's alarm LED and buzzer were last told (None = not told yet)
        self._alarm_out: int | None = None
        self._alarm_out_at = 0.0

        #: breaths on the device clock (cleared on every new connection), all of them
        self.breaths: list[Breath] = []
        #: the breaths that passed the noise screen: everything shown is computed from these
        self.good: list[Breath] = []
        #: per-breath trend on the wall clock (kept across reconnects)
        self.trend: list[TrendPoint] = []
        #: continuous CO2 behind the trend: wall s, peak mmHg per 0.5 s
        self.trace_t: list[float] = []
        self.trace_v: list[float] = []
        #: False where a point covers a breath the noise screen left out (not drawn)
        self.trace_ok: list[bool] = []
        self.markers: list[Marker] = []
        #: where alarms started, for the trend chart: (wall ms, priority)
        self.alarm_starts: list[tuple[float, str]] = []
        self.log: list[LogLine] = []
        self.recent_lines: collections.deque[str] = collections.deque(maxlen=40)
        self.trend_version = 0
        self.frozen = False
        self._marker_id = 1
        #: display values waiting to be drawn, per channel; the window drains them
        self.waves: dict[str, list[float]] = {cid: [] for cid in CHANNEL_IDS}
        #: bumps when the waveforms should be cleared (new connection, a new speed)
        self.wave_epoch = 0
        #: the sweep gets one sample in this many (the speed of a simulation or replay)
        self._wave_every = 1
        self._wave_count = 0
        self._sample_listeners: list[Callable[[Processed], None]] = []
        self._timers: list[tuple[float, Callable[[], None]]] = []
        self._jobs: set[HardwareCheckJob] = set()

        self.pipeline = Pipeline(self.effective_channels, self._processing_options(), self.settings.spo2)

    # ---------- configuration ----------

    @property
    def sessions_dir(self) -> Path:
        if self._sessions_override:
            return self._sessions_override
        return Path(self.settings.sessions_dir) if self.settings.sessions_dir else default_sessions_dir()

    @property
    def hardware(self) -> str:
        """The board in use: what the connected device says it is (a board that sends
        engineering units says UNITS in its info line), what a replayed recording was
        made with, and otherwise the setting."""
        if self._source is not None:
            if self._replay_hardware is not None:
                return self._replay_hardware
            if self._info is not None and "FW" in self._info:
                return "digital" if "UNITS" in self._info else "analog"
        return self.settings.hardware

    @property
    def channels(self) -> dict:
        """The saved calibration of the board in use."""
        return self._channels_of[self.hardware]

    @channels.setter
    def channels(self, c: dict) -> None:
        self._channels_of["digital" if is_digital(c) else "analog"] = c

    @property
    def effective_channels(self) -> dict:
        """The calibration in use: a replay's own, or the saved one."""
        return self._file_channels if self._file_channels is not None else self.channels

    @property
    def single_flow(self) -> bool:
        """One flow sensor is both the flow waveform and the source of breaths and volumes."""
        return single_flow(self.effective_channels)

    @property
    def source(self) -> Source | None:
        return self._source

    @property
    def info(self) -> dict[str, str] | None:
        return self._info

    def _rate(self) -> float:
        try:
            r = float((self._info or {}).get("RATE", ""))
        except ValueError:
            r = NAN
        return r if math.isfinite(r) and r else 100

    def _processing_options(self) -> ProcessingOptions:
        o = dataclasses.replace(self.settings.processing, sample_rate_hz=self._rate())
        if self._capno_on:
            # the Capnostream's sample line: the flow waits for the CO2 it goes with
            o = dataclasses.replace(o, co2_delay_ms=self.settings.capno.delay_ms)
        return o

    def update_settings(self, **changes) -> None:
        """Change settings (attribute names of ``Settings``); applied and saved at once."""
        prev = self.settings
        if changes.get("study_code", prev.study_code) != prev.study_code and "test" not in changes:
            # a new study code is a new subject: the last one's size must not carry over
            changes["test"] = dataclasses.replace(prev.test, height_cm=None, weight_kg=None, age_years=None)
        nxt = dataclasses.replace(prev, **changes)
        nxt.alarms = sanitize_alarms(nxt.alarms)
        nxt.processing = sanitize_processing(nxt.processing)
        nxt.test = sanitize_test(nxt.test)
        nxt.spo2 = sanitize_spo2(nxt.spo2)
        if nxt.hardware not in ("digital", "analog"):
            nxt.hardware = prev.hardware
        was = self.effective_channels
        self.settings = nxt
        self.store.save_settings(nxt)
        if nxt.capno != prev.capno and self._source is not None:
            if isinstance(self._source, SimulatorSource):
                self.connect("simulator")  # with (or without) its simulated Capnostream
                return
            self._add_log("Capnostream settings take effect at the next connection")
        if nxt.hardware != prev.hardware:
            if isinstance(self._source, SimulatorSource):
                self.connect("simulator")  # the simulated board is the other one now
            elif self.effective_channels is not was:
                self.pipeline.set_channels(self.effective_channels)
        if self.store.save_error and not self._save_error_logged:
            self._add_log(f"Settings could not be saved ({self.store.save_error})", "error")
        self._save_error_logged = bool(self.store.save_error)
        if nxt.test.response_skip_sec != prev.test.response_skip_sec:
            self.trend_version += 1
        self.alarms.set_settings(nxt.alarms, self.clock())
        self.audio.volume = nxt.alarms.volume
        if nxt.processing != prev.processing:
            self.pipeline.set_options(self._processing_options())
        if nxt.spo2 != prev.spo2:
            self.pipeline.spo2 = nxt.spo2
            self._spo2_low_since = None
            if isinstance(self._source, SimulatorSource):
                self._source.core.encoders = simulator_encoders(nxt.spo2)  # the simulated oximeter follows
            if not nxt.spo2.enabled or nxt.spo2.low_pct <= 0:
                for aid in ("spo2Low", "spo2NoSignal"):
                    if self.alarms.is_active(aid):
                        self.alarms.set_condition(aid, False, self.clock(), "high", "")
        if nxt.sessions_dir != prev.sessions_dir and not self.recorder.active:
            self.library = self._library_factory(self.sessions_dir, self.clock)
            self.recorder.library = self.library
            self.library_changed()

    def set_sim(self, **patch) -> None:
        """Change simulator settings; a running simulator follows at once."""
        if "preset" not in patch and set(patch) - {"speed", "record_tests"}:  # these are not part of a situation
            patch.setdefault("adjusted", True)
        self.update_settings(sim=dataclasses.replace(self.settings.sim, **patch))
        if isinstance(self._source, SimulatorSource):
            if "speed" in patch and self._source.speed != self.settings.sim.speed:
                self._source.speed = self.settings.sim.speed
                self._sync_wave_speed()
                self._add_log(f"Simulation speed {self._source.run_speed:g}×")
                # a recording's clock follows the simulated time, so it notes where that changes
                self.recorder.event(self.clock(), f"SIM_SPEED {self._source.run_speed:g}")
            self._source.core.model.set_params(**model_params(patch))

    def set_sim_switch(self, key: str, on: bool) -> None:
        """Flip a simulation switch (apnea, an unplugged sensor, the CO2 rise, ...)."""
        self.set_sim(**switch_patch(self.settings.sim, key, on))

    def apply_situation(self, sid: str, start: bool = True) -> None:
        """Set every simulator value for a ready-made situation. Starts the simulator
        (when ``start``) or changes the running one in place, without a gap in the data."""
        sit = SITUATION_BY_ID[sid]
        self._end_sim_test("another situation was chosen")
        sim = dataclasses.replace(situation_settings(sid), speed=self.settings.sim.speed, record_tests=self.settings.sim.record_tests)
        self.update_settings(sim=sim)
        running = isinstance(self._source, SimulatorSource)
        if running:
            self._source.core.model.set_params(**model_params(dataclasses.asdict(sim)))
        elif start:
            self.connect("simulator")
        if sit.marker and isinstance(self._source, SimulatorSource):
            self.add_marker(sit.marker)
        self._add_log(f"Simulation: {sit.label}")
        if sit.test and isinstance(self._source, SimulatorSource):
            self._start_sim_test()

    # ---------- a whole simulated test: the simulation presses the test keys itself

    def _start_sim_test(self) -> None:
        """Baseline now; ``_run_sim_test`` turns the "valve", ends the test at the stop
        alarm (or when the CO2 stops rising) and stops after the recovery. Everything is
        timed on the simulated clock, so it runs faster with the simulation's speed."""
        own = self.settings.sim.record_tests and not self.recorder.active
        if own:
            self.start_recording()
        self.start_baseline()
        self._sim_test = {"step": "baseline", "t": None, "due": None, "own_recording": own}

    def _sim_patch(self, **patch) -> None:
        """Change the running simulation as part of a whole test (it stays "not adjusted")."""
        self.set_sim(preset=self.settings.sim.preset, **patch)

    def _end_sim_test(self, why: str = "") -> None:
        st, self._sim_test = self._sim_test, None
        if st is None:
            return
        if st["own_recording"] and self.recorder.active:
            self.stop_recording()
        if why:
            self._add_log(f"Simulated test stopped: {why}")

    def _run_sim_test(self) -> None:
        st = self._sim_test
        if st is None:
            return
        src = self._source
        if not isinstance(src, SimulatorSource) or self._last is None:
            if src is None or not isinstance(src, SimulatorSource):
                self._end_sim_test("the simulator is no longer running")
            return
        t = self._last.t_sec  # simulated seconds
        if st["t"] is None:
            st["t"] = t
        phase = self.test_window()
        rebreathing = phase.start is not None and phase.end is None
        if st["step"] == "baseline":
            if rebreathing:  # the operator pressed Start rebreathing first
                st.update(step="rebreathing", t=t, due=None)
                self._sim_patch(ramp=True)
            elif t - st["t"] >= TEST_BASELINE_SEC:
                self.start_rebreathing()
                self._sim_patch(ramp=True)
                st.update(step="rebreathing", t=t, due=None)
        elif st["step"] == "rebreathing":
            if not rebreathing:  # the operator pressed End test
                self._sim_patch(ramp=False)
                st.update(step="recovery", t=t)
                return
            p = src.core.model.params
            done = self.alarms.is_active("co2Stop") or self.alarms.is_active("testTime") or src.core.model.target_etco2 >= p.ramp_max_mm_hg - 0.01
            if done and st["due"] is None:
                # the "operator" takes a moment to turn the valve back (a few breaths at the top
                # when it is the CO2 itself that stopped rising)
                st["due"] = t + (TEST_REACTION_SEC if self.alarms.is_active("co2Stop") else 4 * TEST_REACTION_SEC)
            if st["due"] is not None and t >= st["due"]:
                self.end_test()
                self._sim_patch(ramp=False)
                st.update(step="recovery", t=t)
        elif st["step"] == "recovery" and t - st["t"] >= TEST_RECOVERY_SEC:
            res = self.co2_response()
            self._add_log("Simulated test finished: " + co2_response_summary(res, lambda v, d: f"{v:.{d}f}"))
            self._end_sim_test()

    def sim_test_text(self) -> str:
        """Where a whole simulated test stands, for the simulation panel ("" if none runs)."""
        st = self._sim_test
        if st is None:
            return ""
        el = self._last.t_sec - st["t"] if self._last is not None and st["t"] is not None else 0.0
        rec = " · recording" if st["own_recording"] else ""
        if st["step"] == "baseline":
            return f"Whole test: baseline {_mss(el)} of {_mss(TEST_BASELINE_SEC)}, then the valve is turned{rec}"
        if st["step"] == "rebreathing":
            return f"Whole test: rebreathing {_mss(el)}, until the stop alarm{rec}"
        return f"Whole test: ended; recovery {_mss(el)} of {_mss(TEST_RECOVERY_SEC)}{rec}"

    def sim_expected(self) -> dict[str, float] | None:
        """What the running simulator is producing now (for comparing with the
        monitor's numbers), or None when the simulator is not the source."""
        if not isinstance(self._source, SimulatorSource):
            return None
        core = self._source.core
        exp = core.model.expected()
        exp["clipping"] = sim_clipping(core, exp)
        return exp

    def set_channels(self, c: dict) -> None:
        """Save a new calibration (applies immediately)."""
        c = validate_channels(clone_channels(c))
        self.channels = c
        self.store.save_channels(c)
        if self._file_channels is None:
            self.pipeline.set_channels(self.channels)
        if self.store.save_error:
            self._add_log(f"Calibration is in use but could not be saved ({self.store.save_error})", "error")
        else:
            self._add_log("Calibration saved")
        self.recorder.event(self.clock(), "CALIBRATION_CHANGED")

    def reset_channels(self) -> None:
        """Back to the bundled defaults of the board in use (co2response/config/)."""
        hw = self.hardware
        self.store.clear_saved_channels(hw)
        self._channels_of[hw] = load_default_channels(hw)
        if self._file_channels is None:
            self.pipeline.set_channels(self.channels)
        self._add_log("Calibration reset to defaults")

    def init(self) -> list[SessionRecord]:
        """Find recordings that a crashed or closed app left unfinished."""
        self._refresh_known_port(force=True)
        try:
            recovered = self.library.recover_interrupted(self.clock())
        except OSError:
            recovered = []
        if recovered:
            self.library_changed()
        return recovered

    def library_changed(self) -> None:
        self.library_version += 1

    # ---------- connection ----------

    def connect(
        self,
        kind: str,
        *,
        port: str | None = None,
        session: ParsedSession | None = None,
        file_name: str | None = None,
        speed: float = 1,
        use_file_calibration: bool = True,
        auto: bool = False,
    ) -> bool:
        """Connect a source: 'serial', 'simulator', 'bridge' or 'replay'. Returns True on success."""
        if kind == "serial":
            device = port or (self._known_port.device if self._known_port else None)
            if not device:
                self._fail("Choose the Arduino's serial port first (Settings > Connection).")
                return False
            info = next((p for p in self._ports() if p.device == device), None)
            src: Source = SerialSource(device, self.settings.baud_rate, info.label if info else None)
        elif kind == "simulator":
            sim = self.settings.sim
            digital = self.settings.hardware == "digital"
            a = (DEFAULT_DIGITAL_CHANNELS if digital else DEFAULT_CHANNELS)["adc"]
            src = SimulatorSource(
                simulator_encoders(self.settings.spo2, digital),
                a["railLowV"],
                a["railHighV"],
                clock=self._seconds,
                speed=sim.speed,
                digital=digital,
                seed=int(self.clock()) & 0xFFFF,
                **model_params(dataclasses.asdict(sim)),
            )
        elif kind == "bridge":
            src = BridgeSource(self.settings.bridge_url)
        elif kind == "replay":
            if session is None:
                raise ValueError("No session to replay")
            src = ReplaySource(session, file_name or "session.csv", speed, clock=self._seconds)
        else:
            raise ValueError(f"unknown source {kind!r}")
        if self._source is not None:
            self._stop_source("Switched source")
        return self._start_source(src, session, use_file_calibration, auto)

    def connect_source(self, src: Source, session: ParsedSession | None = None, use_file_calibration: bool = True, auto: bool = False) -> bool:
        """Connect a source that was made elsewhere (e.g. a browser serial port the
        user picked, already open). Returns True on success."""
        if self._source is not None:
            self._stop_source("Switched source")
        return self._start_source(src, session, use_file_calibration, auto)

    @property
    def awaiting_reconnect(self) -> bool:
        return self._awaiting_reconnect

    def _seconds(self) -> float:
        return self.clock() / 1000

    def _fail(self, detail: str) -> None:
        self._status = "error"
        self._status_detail = detail
        self._add_log(f"Connection failed: {detail}", "error")

    def _start_source(self, src: Source, session: ParsedSession | None, use_file_calibration: bool, auto: bool) -> bool:
        kind = src.kind
        self._file_channels = session.channels if kind == "replay" and use_file_calibration and session is not None and session.channels else None
        self._replay_hardware = None
        if kind == "replay":
            # replayed without its own calibration, a recording still needs its own board's
            made_with = session.channels if session is not None and session.channels else None
            self._replay_hardware = ("digital" if is_digital(made_with) else "analog") if made_with else self.settings.hardware
        self._info = None
        self._alarm_out = None
        self._capno_on = self.settings.capno.enabled and kind in ("serial", "bridge", "simulator")
        self._valve = False  # a board starts (and restarts) on room air
        self._valve_asked.clear()
        if self._file_channels is not None:
            first = self._file_channels
        elif isinstance(src, SimulatorSource):
            first = self._channels_of["digital" if src.core.digital else "analog"]
        else:
            first = self._channels_of[self._replay_hardware or self.settings.hardware]
        self.pipeline = Pipeline(first, self._processing_options(), self.settings.spo2)
        self._spo2_low_since = None
        self._spo2_valid_wall = self.clock()
        self._sync_phase()
        self._seq.reset()
        self._bad_lines = 0
        self._bad_times.clear()
        self.breaths = []
        self.good = []
        self._recent_quality.clear()
        self._trend_of = {}
        self._last = None
        self._flow_peak_cur = -math.inf
        self._flow_peak_last = NAN
        now = self.clock()
        self._last_data_wall = self._last_breath_wall = now
        self._last_valid_wall = {cid: 0.0 for cid in CHANNEL_IDS}
        self._wave_every = max(1, round(src.run_speed if isinstance(src, SimulatorSource) else float(getattr(src, "speed", 1) or 1)))
        self._clear_waves()
        self.alarms.set_condition("deviceLost", False, now, "medium", "")
        self._awaiting_reconnect = False
        self._reconnect_at = None
        self._i2c_scan = None
        self._adc_detail = None
        self._adc_error_count = 0
        self._status = "connecting"
        self._status_detail = ""
        try:
            src.start()
        except Exception as e:
            self._file_channels = None
            self._fail(str(e) or type(e).__name__)
            if auto:
                self._awaiting_reconnect = True  # try again when the port settles
            return False
        self._source = src
        self._status = "connected"
        self._connected_at = now
        self._handshake_retried = False
        if kind in ("serial", "bridge"):
            src.send("INFO")
            src.send("START")
        if isinstance(src, SerialSource):
            info = next((p for p in self._ports() if p.device == src.port), None)
            self.settings.last_port = src.port
            self.settings.last_port_usb = info.usb_id if info else ""
            self.store.save_settings(self.settings)
            self._known_port = info or PortInfo(src.port)
        self._add_log(f"Connected: {src.label}")
        self.recorder.event(now, f"CONNECTED {src.label}")
        if self._capno_on:
            self._start_capno(src)
        if self.prevent_sleep:
            keep_awake.acquire()
        if kind in ("serial", "bridge") and self.settings.auto_record and not self.recorder.active:
            self.start_recording()
        return True

    # ---------- serial port memory and auto-reconnect ----------

    def _ports(self) -> list[PortInfo]:
        try:
            return self.port_lister()
        except Exception:
            return []

    def find_known_port(self, strict: bool = False) -> PortInfo | None:
        """The Arduino used last time, if it is plugged in. Without ``strict``, the only
        Arduino-like port when there is exactly one."""
        ports = self._ports()
        usb, dev = self.settings.last_port_usb, self.settings.last_port
        match = next((p for p in ports if usb and p.usb_id == usb), None) or next((p for p in ports if dev and p.device == dev and (not usb or not p.usb_id)), None)
        if match or strict:
            return match
        likely = [p for p in ports if p.likely_arduino]
        return likely[0] if len(likely) == 1 else None

    def _refresh_known_port(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_port_scan < 2000:
            return
        self._last_port_scan = now
        self._known_port = self.find_known_port()

    def reconnect_known_port(self) -> bool:
        """One click: reopen the Arduino used last time. False if it is not plugged in."""
        p = self.find_known_port()
        if p is None:
            self._fail("The Arduino used last time is not plugged in. Choose a port in Settings > Connection.")
            return False
        return self.connect("serial", port=p.device)

    def _check_reconnect(self, now: float) -> None:
        if not self._awaiting_reconnect or self._source is not None or not self.settings.auto_reconnect:
            self._reconnect_at = None
            return
        if self._reconnect_at is None:
            if now - self._last_port_scan < 1000:
                return
            self._last_port_scan = now
            p = self.find_known_port(strict=True)
            if p is not None:
                self._add_log("Arduino plugged back in: reconnecting")
                self._reconnect_at = now + 1500  # give the board a moment to boot
        elif now >= self._reconnect_at:
            self._reconnect_at = None
            p = self.find_known_port(strict=True)
            if p is not None:
                self.connect("serial", port=p.device, auto=True)

    def _on_source_status(self, ev: StatusEvent) -> None:
        now = self.clock()
        if ev.status == "error":
            self._status = "error"
            self._status_detail = ev.detail or "Connection error"
            reconnect = self._source is not None and self._source.kind == "serial" and self.settings.auto_reconnect
            if reconnect:
                self._status_detail += ". Reconnects by itself when the Arduino is plugged back in."
            self.alarms.set_condition("deviceLost", True, now, "medium", f"DEVICE DISCONNECTED: {self._status_detail}")
            self._add_log(self._status_detail, "error")
            self._stop_source(self._status_detail, keep_error=True)
            self._awaiting_reconnect = reconnect
            self._last_port_scan = now  # the port may still be listed for a moment
        elif ev.status == "disconnected" and ev.detail:
            self._add_log(ev.detail)
            self._stop_source(ev.detail)

    def disconnect(self) -> None:
        """User-initiated disconnect."""
        self._awaiting_reconnect = False
        self._stop_source("Disconnected")

    def _stop_source(self, reason: str, keep_error: bool = False) -> None:
        src = self._source
        if src is None:
            return
        now = self.clock()
        for pr in self.pipeline.flush():  # the last 30 ms the spike filter still held
            self._use(pr, now)
        self._source = None
        try:
            if (self._info or {}).get("VALVE"):
                # never leave the valve on the bag behind. Sent whatever the app believes
                # the valve is doing: a belief can be wrong, room air cannot.
                src.send("VALVE 0")
            self._valve = False
            self._valve_asked.clear()
            if (self._info or {}).get("ALARM"):
                src.send("ALARM 0")  # the board's LED and buzzer go quiet with the app
            if src.kind in ("serial", "bridge") and self._has_watchdog():
                src.send("HB OFF")  # a deliberate disconnect must not sound the device's buzzer
            if src.kind == "serial":
                src.send("STOP")
            src.stop()
        except Exception:
            pass
        now = self.clock()
        # an intentional disconnect is not a technical fault
        for aid in TECHNICAL:
            if self.alarms.is_active(aid):
                self.alarms.remove(aid)
        self._stop_capno()
        self._file_channels = None
        self._replay_hardware = None
        if self.pipeline.channels is not self.channels:
            self.pipeline.set_channels(self.channels)
        if not keep_error:
            self._status = "idle"
            self._status_detail = "" if reason == "Disconnected" else reason
        self.recorder.event(now, f"DISCONNECTED {reason}")
        for job in list(self._jobs):
            job.cancel()
        keep_awake.release()

    def send(self, cmd: str) -> None:
        if self._source is not None:
            self._source.send(cmd)
        self._add_log(f"Sent {cmd}")

    # ---------- data path ----------

    def poll(self) -> None:
        """Read whatever the source has produced. Call often (e.g. every 20 ms)."""
        src = self._source
        if src is not None:
            items = src.poll()
            if self._merge is not None:
                items = self._merged(items)
            for item in items:
                if self._source is not src:
                    break
                if isinstance(item, str):
                    self._on_line(item)
                elif isinstance(item, MarkerEvent):
                    self.add_marker(item.label)  # a replayed session's own markers
                else:
                    self._on_source_status(item)
        self._run_timers()

    def _on_line(self, line: str) -> None:
        self.recent_lines.append(line)
        p = parse_line(line)
        now = self.clock()
        kind = p.kind
        if kind == "data":
            self._on_data(p.seq, p.t_ms, p.volts, now)
        elif kind == "info":
            if "I2C_SCAN" in p.fields:
                self._i2c_scan = p.fields["I2C_SCAN"]
                self._add_log(f"Device I2C scan: {self._i2c_scan}", "error")
                if self.alarms.is_active("adcError"):
                    self.alarms.set_condition("adcError", True, now, "medium", f"ADC NOT FOUND: I2C devices answering: {self._i2c_scan}. See Settings > Diagnostics")
                return
            prev_rate = self._rate()
            self._info = {**(self._info or {}), **p.fields}
            if self._rate() != prev_rate:
                self.pipeline.set_options(self._processing_options())
            if self.pipeline.channels is not self.effective_channels:
                # the device turned out to be the other board: its calibration applies
                self.pipeline.set_channels(self.effective_channels)
                self._add_log("Board with digital sensors: lines carry % CO2 and L/min" if self.hardware == "digital" else "Board with an ADC: lines carry volts")
            self._add_log(f"Device: {p.raw}")
        elif kind == "error":
            if p.code == "ADC_NOT_FOUND":
                self._adc_error_count += 1
                # a Python DAQ driver (co2r-daq) says why after the code
                detail = ",".join(p.raw.split(",")[2:]).strip()
                self._adc_detail = detail or None
                if not self.alarms.is_active("adcError"):
                    scan = f" (I2C devices answering: {self._i2c_scan})" if self._i2c_scan else ""
                    msg = f"DAQ NOT FOUND: {detail}" if detail else f"ADC NOT FOUND: check I2C wiring and power{scan}"
                    self.alarms.set_condition("adcError", True, now, "medium", msg)
            self._add_log(f"Device error: {p.raw[2:]}", "error")
            self.recorder.event(now, f"DEVICE_ERROR {p.code}")
        elif kind == "ack":
            if p.fields[:1] == ["VALVE"] and len(p.fields) > 1:
                self._valve_reported(p.fields[1] == "1", now)
            elif p.fields[:1] == ["BUTTON"]:
                self._on_button(now)
            elif p.fields[:1] == ["REED"] and len(p.fields) > 1:
                self._on_reed(p.fields[1] == "1", now)
            elif p.fields[:1] == ["ALARM"]:
                return  # the answer to the alarm output command, sent on every change
            self._add_log(f"Device: {p.raw}")
        elif kind == "bad":
            self._bad_lines += 1
            self._bad_times.append(now)

    def _on_data(self, seq: int, t_ms: float, volts: list[float], now: float) -> None:
        self._last_data_wall = now
        self._seq.push(seq, t_ms)
        if self.alarms.is_active("adcError"):
            self.alarms.set_condition("adcError", False, now, "medium", "")
        self._rate_count += 1
        if now - self._rate_window_start >= 2000:
            self._measured_rate = (self._rate_count * 1000) / (now - self._rate_window_start)
            self._rate_count = 0
            self._rate_window_start = now

        # cleaned first: spike removal and filtering happen inside the pipeline, and
        # each breath is screened before anything below interprets it
        pr = self.pipeline.process(t_ms, volts, seq)
        if pr is not None:  # None while the spike filter fills (the first 30 ms)
            self._use(pr, now)

    def _use(self, pr: Processed, now: float) -> None:
        """Everything that interprets a cleaned sample."""
        self._last = pr
        for cid in CHANNEL_IDS:
            if pr.valid[cid]:
                self._last_valid_wall[cid] = now

        # Faster than real time, the sweep gets every k-th sample, so it still crosses
        # the screen at the real-time pace (a time-compressed trace) instead of racing
        # across it k times a sweep, which looks like flashing colour, not a waveform.
        self._wave_count += 1
        if not self.frozen and self._wave_count >= self._wave_every:
            self._wave_count = 0
            w = self.waves
            w["co2"].append(pr.co2_pct if self.settings.co2_unit == "%" else pr.co2_mmhg)
            w["flow"].append(pr.flow_lpm)
            w["pneumo"].append(pr.pneumo_lpm)
            if len(w["co2"]) > MAX_WAVE:
                for lst in w.values():
                    del lst[: len(lst) - MAX_WAVE]
        if math.isfinite(pr.flow_lpm):
            self._flow_peak_cur = max(self._flow_peak_cur, pr.flow_lpm)

        if math.isfinite(pr.co2_mmhg):
            self._trace_max = max(self._trace_max, pr.co2_mmhg)
        if now - self._trace_start >= 500:
            if math.isfinite(self._trace_max):
                self.trace_t.append(now / 1000)
                self.trace_v.append(self._trace_max)
                self.trace_ok.append(True)
                if len(self.trace_t) > MAX_TRACE:
                    del self.trace_t[:1000]
                    del self.trace_v[:1000]
                    del self.trace_ok[:1000]
            self._trace_max = -math.inf
            self._trace_start = now

        if pr.outlier is not None:
            self._on_outlier(pr.outlier, now)
        if pr.breath is not None:
            self._on_breath(pr.breath, now)
        self.alarms.on_live_co2(pr.co2_mmhg, now)
        self._check_spo2(pr.spo2, now)

        # the raw volts are recorded as received, so a session can be re-analysed
        self.recorder.sample(now, pr.t_ms, pr.seq, pr.volts, pr.co2_mmhg, pr.co2_pct, pr.flow_lpm, pr.pneumo_pa, pr.pneumo_lpm, pr.spo2)
        for fn in list(self._sample_listeners):
            fn(pr)

    def _check_spo2(self, spo2: float, now: float) -> None:
        """The low-SpO2 alarm: below the limit for the delay; clears 1 % back above it."""
        o = self.settings.spo2
        if not o.enabled or not math.isfinite(spo2):
            self._spo2_low_since = None
            return
        self._spo2_valid_wall = now
        if o.low_pct <= 0:
            return
        active = self.alarms.is_active("spo2Low")
        if spo2 < o.low_pct:
            if self._spo2_low_since is None:
                self._spo2_low_since = now
            if active or now - self._spo2_low_since >= o.low_delay_sec * 1000:
                self.alarms.set_condition("spo2Low", True, now, "high", f"SpO2 LOW {spo2:.0f} % < {o.low_pct:g}", spo2)
        else:
            self._spo2_low_since = None
            if active and spo2 >= o.low_pct + 1:
                self.alarms.set_condition("spo2Low", False, now, "high", "")

    def _on_breath(self, b: Breath, now: float) -> None:
        self.breaths.append(b)
        if len(self.breaths) > 2000:
            del self.breaths[:500]
        self._last_breath_wall = now  # any breath, even a noisy one, means the subject breathed
        self._recent_quality.append(b.quality)
        peak = self._flow_peak_cur
        self._flow_peak_cur = -math.inf
        if b.quality == "ok":
            self.good.append(b)
            if len(self.good) > 2000:
                del self.good[:500]
            self._last_good_wall = now
            self._flow_peak_last = peak
        else:
            self._add_log(f"Breath left out ({b.quality}): {b.reason}")
            # its stretch of the continuous CO2 is left off the trend too
            self._hide_trace(now / 1000 - b.period_sec, now / 1000)
            self._trace_max = -math.inf
        mv = minute_ventilation(self.good, b.t_end)
        rr = respiratory_rate(self.good)
        self._trend_key += 1
        tp = TrendPoint(now / 1000, b.etco2, mv, b.vt_ml, rr, b.quality, b.mv_lpm, self._trend_key)
        self.trend.append(tp)
        if len(self.trend) > MAX_TREND:
            del self.trend[:1000]
        self._trend_of[id(b)] = tp
        if len(self._trend_of) > 20:
            del self._trend_of[next(iter(self._trend_of))]
        self.trend_version += 1
        # recorded on the sample that completed the breath (written right after this)
        b.rec_n = tp.rec_n = self.recorder.breath(b.etco2, b.fico2, rr, b.vt_ml, mv, b.peak_insp_lpm, b.quality, b.period_sec, b.vte_ml)
        # a noisy or artifact breath would give false EtCO2 alarms; a run of them raises
        # the technical alarm in tick() instead, so the alarm system is never silently blind
        if b.quality == "ok":
            self.alarms.on_breath(b.etco2, now)

    def _on_outlier(self, b: Breath, now: float) -> None:
        """A breath that looked fine turned out to be an isolated EtCO2 jump."""
        tp = self._trend_of.get(id(b))
        if tp is not None:
            tp.quality = "outlier"
            self._hide_trace(tp.t - b.period_sec, tp.t)
        if b in self.good:
            self.good.remove(b)
        self.trend_version += 1
        self._add_log(f"Breath left out (outlier): {b.reason}")
        if b.rec_n is not None:
            self.recorder.event(now, f"BREATH_REJECTED {b.rec_n} outlier: {b.reason}")

    def _hide_trace(self, t_from: float, t_to: float) -> None:
        """Leave the continuous CO2 from wall time ``t_from`` to ``t_to`` (s) off the trend."""
        tt, ok = self.trace_t, self.trace_ok
        i = len(tt) - 1
        while i >= 0 and tt[i] > t_from:
            if tt[i] <= t_to:
                ok[i] = False
            i -= 1
        self.trend_version += 1

    def trend_trace(self, t0: float) -> tuple[list[float], list[float], list[bool]]:
        """The continuous CO2 behind the EtCO2 trend from wall time ``t0`` (s): times,
        peak mmHg per 0.5 s, and whether each point joins the next. Stretches that cover
        breaths the noise screen left out are not included; the line is broken there and
        where data stopped for more than 5 s."""
        tt, vv, ok = self.trace_t, self.trace_v, self.trace_ok
        keep = [i for i in range(bisect.bisect_left(tt, t0), len(tt)) if ok[i]]
        ts = [tt[i] for i in keep]
        join = [keep[k + 1] == keep[k] + 1 and ts[k + 1] - ts[k] <= 5 for k in range(len(keep) - 1)]
        return ts, [vv[i] for i in keep], join + [False] if keep else []

    def take_waves(self) -> dict[str, list[float]]:
        """Display values since the last call, per channel (for the sweep waveforms)."""
        out = self.waves
        self.waves = {cid: [] for cid in CHANNEL_IDS}
        return out

    def _clear_waves(self) -> None:
        self.waves = {cid: [] for cid in CHANNEL_IDS}
        self.wave_epoch += 1
        self._wave_count = 0

    @property
    def wave_compression(self) -> int:
        """How many of the subject's seconds one second of the sweep shows (1 = real time)."""
        return self._wave_every

    def _sync_wave_speed(self) -> None:
        k = max(1, round(self.time_scale()))
        if k != self._wave_every:
            self._wave_every = k
            self._clear_waves()

    # ---------- periodic checks (4 Hz) ----------

    def tick(self) -> None:
        now = self.clock()
        src = self._source
        connected = self._status == "connected" and src is not None
        fresh = now - self._last_data_wall < 3000
        a = self.alarms

        a.set_condition("noData", connected and not fresh, now, "medium", f"NO DATA FROM DEVICE for {round((now - self._last_data_wall) / 1000)} s")
        rails = {"co2": ("railCo2", "CO2 SENSOR"), "flow": ("railFlow", "FLOW SENSOR"), "pneumo": ("railPneumo", "PNEUMOTACH")}
        digital = is_digital(self.pipeline.channels)
        one_flow = single_flow(self.pipeline.channels)
        for cid, (aid, name) in rails.items():
            stuck = self._last is not None and self._last.stuck[cid] and not (one_flow and cid == "pneumo")
            if cid == "co2" and self._merge is not None and self._merge.samples_in == 0 and now - self._connected_at < CAPNO_WARMUP_MS:
                stuck = False  # the Capnostream warms up (about 40 s) and its first CO2 is still in the sample line
            # a digital sensor that stops answering is sent as 9999, the "rail" of that board
            if cid == "co2" and self._merge is not None:
                name = "CAPNOSTREAM"  # no CO2 from the monitor's data port
            what = f"{name} NOT ANSWERING: check its wiring and power" if digital else f"{name} SIGNAL AT ADC RAIL: check sensor connection"
            a.set_condition(aid, connected and fresh and stuck, now, "medium", what)

        apnea_sec = self.settings.alarms.apnea_sec
        signal = self._last is not None and (self._last.valid["pneumo"] or self._last.valid["co2"])
        since_breath = (now - self._last_breath_wall) / 1000
        a.set_condition("apnea", apnea_sec > 0 and connected and fresh and signal and since_breath > apnea_sec, now, "high", f"APNEA: no breath for {math.floor(since_breath)} s")

        a.set_condition("audioOff", not self.audio.armed, now, "advisory", self.audio_off_message)
        dr = self._seq.drop_rate
        a.set_condition("dropped", connected and self._seq.received > 500 and dr > 0.001, now, "advisory", f"Dropped samples {dr * 100:.2f} % ({self._seq.dropped})")
        while self._bad_times and self._bad_times[0] < now - 60000:
            self._bad_times.popleft()
        nb = len(self._bad_times)
        a.set_condition("checksum", connected and nb > 0, now, "advisory", f"{nb} corrupt line{'' if nb == 1 else 's'} in the last minute")

        bad = sum(1 for q in self._recent_quality if q in ("noisy", "artifact"))
        noisy = connected and (bad >= 3 or (a.is_active("noisyBreaths") and bad > 1))
        a.set_condition("noisyBreaths", noisy, now, "medium", f"SIGNAL TOO NOISY: {bad} of the last {len(self._recent_quality)} breaths left out of EtCO2 and its alarms")

        # the rebreathing time limit
        ts = self.test_state(now)
        over = connected and ts.phase == "rebreathing" and ts.limit_sec > 0 and ts.elapsed_sec >= ts.limit_sec
        a.set_condition("testTime", over, now, "medium", f"REBREATHING TIME LIMIT: {_mss(ts.elapsed_sec)} since the valve was turned (limit {_mss(ts.limit_sec)}): end the test")

        # inspired against expired volume: a leak, or a pneumotach zero that has drifted
        mm = self.volume_mismatch_pct()
        lim = self.settings.alarms.leak_pct
        leak = connected and fresh and lim > 0 and math.isfinite(mm) and abs(mm) > (lim if not a.is_active("leak") else lim * 0.8)
        a.set_condition(
            "leak",
            leak,
            now,
            "advisory",
            f"VOLUME MISMATCH: expired volume {abs(mm):.0f} % {'below' if mm > 0 else 'above'} inspired over the last {LEAK_BREATHS} breaths: check for a leak, or zero the pneumotach" if leak else "",
        )

        # a real device on a calibration that is old, or was never done
        stale = self.stale_calibrations(now) if connected and src.kind in ("serial", "bridge") else []
        a.set_condition("calibration", bool(stale), now, "advisory", "CALIBRATION: " + "; ".join(stale) + ". See Settings > Calibration")
        err = self.store.save_error
        a.set_condition("settingsSave", bool(err), now, "advisory", f"SETTINGS NOT SAVED ({err}): changes last only until the app closes")
        # the oximeter is switched on but gives no reading
        lost = self.settings.spo2.enabled and connected and fresh and now - max(self._spo2_valid_wall, self._connected_at) > 10000
        a.set_condition("spo2NoSignal", lost, now, "advisory", "SpO2: no reading from the oximeter. Check the probe, the wiring to input A3 and the channel's scaling")

        a.tick(now)
        self._run_sim_test()
        self._drive_alarm_output(now)

        # audio: highest active priority, unless silenced
        pri = a.active_priority()
        if pri and not a.is_silenced(now) and self.audio.armed:
            if pri == "high" and now - self._last_high_tone >= 5000:
                self.audio.high()
                self._last_high_tone = now
            elif pri == "medium" and now - self._last_med_tone >= 15000:
                self.audio.medium()
                self._last_med_tone = now

        # serial: if the board reset when the port opened, the first commands were lost
        if connected and src.kind in ("serial", "bridge") and self._info is None and not self._handshake_retried and now - self._connected_at > 2500:
            self._handshake_retried = True
            src.send("INFO")
            src.send("START")
        # heartbeat: firmware with a watchdog sounds its own buzzer when this stops
        if connected and src.kind in ("serial", "bridge") and self._has_watchdog() and now - self._last_heartbeat >= HEARTBEAT_MS:
            self._last_heartbeat = now
            src.send("HB")

        if src is None:
            self._refresh_known_port()
        self._check_reconnect(now)
        for job in list(self._jobs):
            job._update()
        self._run_timers()
        self.recorder.poll(now)

    def call_later(self, ms: float, fn: Callable[[], None]) -> None:
        self._timers.append((self.clock() + ms, fn))

    def _run_timers(self) -> None:
        if not self._timers:
            return
        now = self.clock()
        due = [t for t in self._timers if t[0] <= now]
        if due:
            self._timers = [t for t in self._timers if t[0] > now]
            for _, fn in due:
                fn()

    def _on_alarm_log(self, e: AlarmLogEntry) -> None:
        aid = f" {e.id}" if e.id else ""
        pri = f" ({e.priority})" if e.priority else ""
        self._add_log(f"{e.kind.upper()}{aid}{pri}: {e.message}", "alarm")
        self.recorder.event(e.t, f"ALARM_{e.kind.upper()}{aid}{f' {e.priority}' if e.priority else ''} {e.message}")
        if e.kind == "start" and e.id != "test" and e.priority in ("high", "medium"):
            self.alarm_starts.append((e.t, e.priority))
            if len(self.alarm_starts) > 2000:
                del self.alarm_starts[:500]
            self.trend_version += 1
        if e.kind == "start" and e.id in AUTO_STOP and self.settings.alarms.auto_valve and self._valve and self.valve_available():
            # automatic stop: the valve goes back to room air at once; the end-of-test
            # marker follows on the next pass (not from inside the alarm check)
            self._set_valve(False, f"automatic stop: {AUTO_STOP[e.id]}")
            label = f"End of test ({AUTO_STOP[e.id]})"
            self.call_later(0, lambda: self.add_marker(label) if self._rebreathing() else None)
        if e.kind == "start":
            # sound the new alarm right away rather than waiting for the repeat interval
            if e.priority == "high":
                self._last_high_tone = 0
                for fn in self.high_alarm_listeners:
                    fn(e.message)
            if e.priority == "medium":
                self._last_med_tone = 0

    # ---------- operator actions ----------

    def arm_audio(self, confirm: bool = True) -> bool:
        ok = self.audio.arm(confirm)
        self._add_log(f"Audio armed ({self.audio.backend})" if ok else f"No sound output: {self.audio.error}", "info" if ok else "error")
        self.tick()
        return ok

    def acknowledge(self, aid: str | None = None) -> None:
        self.alarms.acknowledge(self.clock(), aid)

    def toggle_silence(self) -> None:
        now = self.clock()
        if self.alarms.is_silenced(now):
            self.alarms.unsilence(now)
        else:
            self.alarms.silence(now)

    def test_alarm(self) -> None:
        """High alarm with tone now, a medium tone after 4 s, cleared after 8 s."""
        self.alarms.set_condition("test", True, self.clock(), "high", "TEST ALARM: confirm you can see and hear this")
        self._last_high_tone = 0
        self.tick()
        self.call_later(4000, lambda: self.audio.medium() if self.audio.armed else None)
        self.call_later(8000, lambda: self.alarms.set_condition("test", False, self.clock(), "high", ""))

    def set_frozen(self, on: bool) -> None:
        self.frozen = on

    def add_marker(self, label: str | None = None) -> Marker:
        mid = self._marker_id
        self._marker_id += 1
        m = Marker(mid, self.clock(), (label or "").strip() or f"Event {mid}")
        self.markers.append(m)
        if len(self.markers) > 2000:
            del self.markers[:500]
        self.trend_version += 1
        self.recorder.event(m.t, f"MARK {m.label}")
        self._add_log(f"Marker: {m.label}")
        self._sync_phase()
        if marker_kind(m.label) != "start" and self.alarms.is_active("testTime"):
            self.alarms.set_condition("testTime", False, m.t, "medium", "")
        return m

    # ---------- the rebreathing test

    def test_window(self) -> TestWindow:
        """The latest test's baseline, rebreathing and end times (wall seconds)."""
        return test_window((m.t / 1000, m.label) for m in self.markers)

    def rebreathing_start(self) -> float | None:
        """Wall-clock seconds of the marker that started the latest rebreathing
        (e.g. "Rebreathing", "CO2 rebreathing started"), or None if there is none."""
        return self.test_window().start

    def _sync_phase(self) -> None:
        """Tell the pipeline whether the bag gas is in the pneumotach right now."""
        w = self.test_window()
        self.pipeline.rebreathing = w.start is not None and w.end is None

    def time_scale(self) -> float:
        """Seconds of the subject's time per second on the wall clock: 1 for a live
        device; the speed of a simulation or a replay running faster than real time.
        The test's own times (the baseline minute, the seconds left out of the fit,
        the rebreathing timer and its limit) are in the subject's time."""
        src = self._source
        if isinstance(src, SimulatorSource):
            return src.run_speed
        if isinstance(src, ReplaySource):
            return max(1e-6, float(src.speed))
        return 1.0

    def test_state(self, now: float | None = None) -> TestState:
        now = self.clock() if now is None else now
        w = self.test_window()
        t = now / 1000
        k = self.time_scale()
        limit = self.settings.alarms.rebreathing_max_sec
        rec = "" if self.recorder.active else " · NOT RECORDING"
        if w.start is None:
            if w.baseline is None:
                return TestState("idle", w, text="No test running. Press Start baseline once the subject is on the mouthpiece.")
            recent = [(q.etco2, q.mv_breath) for q in self.trend if q.t >= max(w.baseline, t - 60 / k) and q.quality == "ok" and not q.excluded]
            ok, why = stability(recent)
            el = (t - w.baseline) * k
            text = f"BASELINE {_mss(el)} · {'steady' if ok else 'settling'} ({why} in the last minute){rec}"
            return TestState("baseline", w, el, 0.0, ok, why, text)
        if w.end is None:
            el = (t - w.start) * k
            lim = f" of {_mss(limit)}" if limit > 0 else ""
            valve = ""
            if self.valve_available() and self.settings.alarms.auto_valve:
                valve = " · valve to the bag" if self._valve else " · VALVE ON ROOM AIR"
            return TestState("rebreathing", w, el, limit, text=f"REBREATHING {_mss(el)}{lim}{valve}{rec}")
        el = (w.end - w.start) * k
        return TestState("ended", w, el, limit, text=f"TEST ENDED after {_mss(el)} of rebreathing")

    def start_baseline(self) -> Marker:
        """Baseline key: breaths from now until rebreathing starts are the baseline."""
        return self.add_marker(BASELINE_LABEL)

    def start_rebreathing(self) -> Marker:
        """Rebreathing key: press it as the valve is turned to the bag (with the valve
        switched from the app, this turns it)."""
        if self.settings.alarms.auto_valve:
            self._set_valve(True)
        return self.add_marker(REBREATHING_LABEL)

    def end_test(self) -> Marker:
        """End key: press it as the valve is turned back to room air (with the valve
        switched from the app, this turns it)."""
        if self.settings.alarms.auto_valve:
            self._set_valve(False)
        return self.add_marker(END_LABEL)

    def emergency_stop(self) -> Marker | None:
        """Emergency stop key: the valve output goes to room air at once (whether or not
        the app normally switches it) and the test is ended. Without a valve output it
        ends the test in the record; the operator turns the valve."""
        now = self.clock()
        sent = self._set_valve(False, "emergency stop")
        self._add_log("EMERGENCY STOP" + (": valve to room air" if sent else ": turn the valve to room air"), "alarm")
        self.recorder.event(now, "EMERGENCY_STOP")
        return self.add_marker(EMERGENCY_LABEL) if self._rebreathing() else None

    # ---------- a Capnostream 35 as the CO2 source

    def _start_capno(self, src: Source) -> None:
        """Open the Capnostream (or a simulated one) and start merging its CO2 in."""
        c = self.settings.capno
        p = self.settings.processing
        if isinstance(src, SimulatorSource):
            capno: Source = SimulatedCapnostream(src.core, c.delay_ms / 1000)
            core = src.core
            clock = lambda: core.device_s  # noqa: E731  (the simulated device's clock)
        elif not c.port or c.port == "sim":
            self._add_log("Capnostream: choose its port (a simulated one works only with the simulator)", "error")
            capno, clock = None, time.monotonic
        else:
            capno, clock = CapnostreamSource(c.port, c.baud), time.monotonic
        self._merge = CapnoMerge(CapnoDecoder(c.fields, c.units, p.barometric_mm_hg), clock, mmhg_to_line_pct(p.barometric_mm_hg, p.water_vapor))
        if capno is None:
            return
        try:
            capno.start()
        except Exception as e:  # noqa: BLE001
            self._add_log(str(e) or "Could not open the Capnostream", "error")
            return
        self._capno_src = capno
        self._add_log(f"CO2 from {capno.label}: its CO2 delay {c.delay_ms:g} ms is applied to the flow")
        self.recorder.event(self.clock(), f"CO2_SOURCE {capno.label}")

    def _stop_capno(self) -> None:
        capno, self._capno_src, self._merge = self._capno_src, None, None
        self._capno_on = False
        if capno is not None:
            try:
                capno.stop()
            except Exception:  # noqa: BLE001
                pass

    def _merged(self, items: list) -> list:
        """The board's items with the Capnostream's CO2 in its data lines."""
        m = self._merge
        capno = self._capno_src
        if capno is not None:
            for item in capno.poll():
                if isinstance(item, str):
                    m.capno_line(item)
                elif isinstance(item, StatusEvent):
                    self._add_log(item.detail or "Capnostream disconnected", "error")
                    self._capno_src = None  # its CO2 now reads "not answering"
                    try:
                        capno.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    break
        out = m.board_items(items)
        if not m.board_ok and not getattr(m, "_warned", False):
            m._warned = True  # type: ignore[attr-defined]
            self._add_log("Capnostream: this board sends volts, not units; the Capnostream's CO2 is used with the Uno R4 board only", "error")
        return out

    @property
    def capnostream(self) -> CapnoMerge | None:
        """The Capnostream link while one is in use."""
        return self._merge

    # ---------- the board's button, reed switch, alarm LED and buzzer (the Uno R4 board)

    def _on_button(self, now: float) -> None:
        """The stop / marker button on the board. During rebreathing it stops the test
        (the valve goes back to room air where the board can move it); at any other
        time it places an event marker, like the Mark event key."""
        self.recorder.event(now, "BUTTON")
        if self._rebreathing():
            self._add_log("Stop button pressed on the board", "alarm")
            self.emergency_stop()
        else:
            self._add_log("Marker button pressed on the board")
            self.add_marker()

    def _on_reed(self, at_bag: bool, now: float) -> None:
        """The reed switch on the valve handle: the operator turned the valve by hand.
        To the bag starts the rebreathing phase; back to room air ends the test."""
        self._valve = at_bag
        self.recorder.event(now, f"VALVE {'BAG' if at_bag else 'AIR'} (reed switch)")
        self._add_log(f"Valve handle turned to {'the bag' if at_bag else 'room air'}")
        if at_bag and not self._rebreathing():
            self.add_marker(REBREATHING_LABEL)
        elif not at_bag and self._rebreathing():
            self.add_marker(END_LABEL)

    def _drive_alarm_output(self, now: float) -> None:
        """Tell the board's alarm LED and buzzer what the monitor is alarming about:
        2 = a high-priority alarm (stop limit, apnea), 1 = a medium one (the warning
        limit, a technical fault), 0 = nothing. Silenced alarms keep the LED on (1)
        but stop the buzzer. Sent on a change, and repeated every 5 s so a board that
        missed one still follows."""
        src = self._source
        if src is None or not (self._info or {}).get("ALARM"):
            return
        pri = self.alarms.active_priority()
        level = 2 if pri == "high" else 1 if pri == "medium" else 0
        if level == 2 and self.alarms.is_silenced(now):
            level = 1
        if level != self._alarm_out or now - self._alarm_out_at >= 5000:
            self._alarm_out, self._alarm_out_at = level, now
            src.send(f"ALARM {level}")

    # ---------- the board's valve output

    def valve_available(self) -> bool:
        return self._source is not None and bool((self._info or {}).get("VALVE"))

    def _rebreathing(self) -> bool:
        w = self.test_window()
        return w.start is not None and w.end is None

    def _valve_reported(self, to_bag: bool, now: float) -> None:
        """The board says where its valve output is: its answer to a command, or a move
        of its own (its watchdog returns the valve to room air). An answer to a command
        that newer commands have already overtaken says nothing about where the valve
        will end up, so it is not believed; the answer to the newest command is."""
        while self._valve_asked and now - self._valve_asked[0] > VALVE_ANSWER_MS:
            self._valve_asked.popleft()  # that answer was lost
        if self._valve_asked:
            self._valve_asked.popleft()
        if self._valve_asked or to_bag == self._valve:
            return
        self._valve = to_bag
        self.recorder.event(now, f"VALVE {'BAG' if to_bag else 'AIR'} (reported by the device)")

    def _set_valve(self, to_bag: bool, why: str = "") -> bool:
        """Command the valve output. Returns False when the device has none."""
        src = self._source
        if src is None or not self.valve_available():
            return False
        src.send(f"VALVE {1 if to_bag else 0}")
        now = self.clock()
        while self._valve_asked and now - self._valve_asked[0] > VALVE_ANSWER_MS:
            self._valve_asked.popleft()
        self._valve_asked.append(now)
        changed = self._valve != to_bag
        self._valve = to_bag
        if changed or why:
            text = f"VALVE {'BAG' if to_bag else 'AIR'}" + (f" ({why})" if why else "")
            self._add_log(f"Valve to {'the bag' if to_bag else 'room air'}" + (f": {why}" if why else ""))
            self.recorder.event(self.clock(), text)
        if isinstance(src, SimulatorSource) and self.settings.sim.ramp != to_bag:
            self._sim_patch(ramp=to_bag)  # in the simulation the valve decides whether CO2 rises
        return True

    def co2_response(self, t0: float = float("-inf")) -> CO2Response:
        """Each breath's own MV vs its EtCO2, over accepted breaths from wall second
        ``t0``: baseline breaths before the rebreathing marker, the line fitted to the
        rebreathing breaths (after the first seconds, before the end of the test, and
        not taken out by hand)."""
        by_key = {q.key: q for q in self.trend if q.t >= t0 and q.quality == "ok"}
        pts = ((q.t, q.etco2, q.mv_breath, q.key) for q in by_key.values())
        # the trend is on the wall clock: at 10x speed, 30 s of the subject's time is 3 s of it
        skip = self.settings.test.response_skip_sec
        res = co2_response(pts, self.test_window(), skip / self.time_scale(), lambda k: by_key[k].excluded)
        if res.skip_sec:
            res.skip_sec = skip  # reported in the subject's seconds
        return res

    def set_breath_excluded(self, key: int, on: bool = True) -> bool:
        """Take a breath out of the response fit by hand (or put it back). The breath
        stays in the recording, with a BREATH_EXCLUDED / BREATH_INCLUDED event."""
        tp = next((q for q in reversed(self.trend) if q.key == key), None)
        if tp is None or tp.excluded == on:
            return False
        tp.excluded = on
        self.trend_version += 1
        what = "excluded from" if on else "put back into"
        self._add_log(f"Breath at {iso_ms(tp.t * 1000)} (EtCO2 {tp.etco2:.1f} mmHg) {what} the response fit by hand")
        if tp.rec_n is not None:
            self.recorder.event(self.clock(), f"BREATH_EXCLUDED {tp.rec_n} manual" if on else f"BREATH_INCLUDED {tp.rec_n}")
        return True

    def toggle_breath(self, key: int) -> bool:
        tp = next((q for q in reversed(self.trend) if q.key == key), None)
        return tp is not None and self.set_breath_excluded(key, not tp.excluded)

    def volume_mismatch_pct(self) -> float:
        """(inspired − expired) / inspired volume over the last few clean breaths, %."""
        last = [b for b in self.good[-LEAK_BREATHS:] if math.isfinite(b.vt_ml) and math.isfinite(b.vte_ml)]
        vi = sum(b.vt_ml for b in last)
        if len(last) < LEAK_BREATHS or vi <= 0:
            return NAN
        return (vi - sum(b.vte_ml for b in last)) / vi * 100

    def co2_lag_sec(self) -> float:
        """How long after expiration begins the CO2 rises, s: the median of the last
        10 clean breaths. Beyond the 0.1-0.3 s of dead-space washout, it is the CO2
        sensor's delay that the CO2 delay setting should cancel."""
        return median([b.co2_lag_sec for b in self.good[-10:]])

    def stale_calibrations(self, now: float | None = None) -> list[str]:
        """Channels whose calibration was never done, or is older than the limit."""
        limit_h = self.settings.alarms.calibration_max_age_h
        if limit_h <= 0:
            return []
        now = self.clock() if now is None else now
        out = []
        for cid in CHANNEL_IDS:
            ch = self.effective_channels["channels"][cid]
            at = parse_iso_ms(ch.get("calibratedAt") or "")
            name = ch.get("label") or cid
            if not math.isfinite(at):
                out.append(f"{name} never calibrated (default values)")
            elif now - at > limit_h * 3600_000:
                out.append(f"{name} calibrated {_age(now - at)} ago")
        return out

    def _has_watchdog(self) -> bool:
        return bool((self._info or {}).get("WD"))

    def rename_marker(self, mid: int, label: str) -> None:
        m = next((x for x in self.markers if x.id == mid), None)
        if m is None or label.strip() == m.label:
            return
        m.label = label.strip() or m.label
        self.trend_version += 1
        self.recorder.event(self.clock(), f"MARK_RENAMED {mid} {m.label}")
        self._sync_phase()

    def start_recording(self) -> SessionRecord | None:
        src = self._source
        rec = self.recorder.start(
            SessionMeta(
                study_code=self.settings.study_code,
                started_ms=0,
                app_version=__version__,
                source=src.label if src else "none",
                firmware=(self._info or {}).get("FW", "unknown"),
                rate_hz=self._rate(),
                channels=self.effective_channels,
                processing=self._processing_options(),
                alarm_settings=self.settings.alarms,
                test=self.settings.test,
                spo2=self.settings.spo2 if self.settings.spo2.enabled else None,
            )
        )
        where = "" if rec else f" (in memory: cannot write to {self.library.folder})"
        self._add_log(f"Recording {self.recorder.file_name}{where}")
        now = self.clock()
        self.recorder.event(now, f"RECORDING_STARTED source={src.label if src else 'none'}")
        for r in self.alarms.list():
            if r.state == "active" and r.priority != "advisory":
                self.recorder.event(now, f"ALARM_ACTIVE_AT_START {r.id} {r.priority} {r.message}")
        if src is not None and src.kind in ("serial", "bridge"):
            for text in self.stale_calibrations(now):
                self.recorder.event(now, f"CALIBRATION_STALE {text}")
        self.library_changed()
        return rec

    def stop_recording(self) -> StopResult:
        """Stop and finish the session file. It is already on disk in the sessions folder."""
        file_name = self.recorder.file_name
        self.recorder.event(self.clock(), "RECORDING_STOPPED")
        rec, memory_text = self.recorder.stop()
        self._add_log("Recording stopped")
        self.library_changed()
        if memory_text is None:
            return StopResult(file_name, self.library.path(rec) if rec else None, rec)
        # the sessions folder failed: save the whole file somewhere else
        for folder in (Path.home(), Path.cwd()):
            try:
                path = folder / file_name
                path.write_text(memory_text, encoding="utf-8", newline="")
                self._add_log(f"Session file saved to {path} (sessions folder not writable)", "error")
                return StopResult(file_name, path, rec, self.recorder.write_error)
            except OSError:
                continue
        self._add_log(f"Could not save {file_name}", "error")
        return StopResult(file_name, None, rec, self.recorder.write_error or "could not write the file")

    def shutdown(self) -> StopResult | None:
        """Close cleanly: finish any recording and release the device."""
        result = self.stop_recording() if self.recorder.active else None
        self._awaiting_reconnect = False
        self._stop_source("App closed")
        return result

    # ---------- hardware check and calibration helpers ----------

    def run_hardware_check(self, seconds: float) -> HardwareCheckJob:
        if self._status != "connected":
            raise RuntimeError("Connect the Arduino (or the simulator) first.")
        job = HardwareCheckJob(self, seconds)
        self._jobs.add(job)
        return job

    def on_sample(self, fn: Callable[[Processed], None]) -> Callable[[], None]:
        """Call ``fn`` with every processed sample. Returns a function that unsubscribes."""
        self._sample_listeners.append(fn)

        def off() -> None:
            if fn in self._sample_listeners:
                self._sample_listeners.remove(fn)

        return off

    def capture_average(self, cid: str, seconds: float, done: Callable[[CaptureResult | None, str | None], None]) -> None:
        """Average one channel's raw volts and calibrated value over ``seconds``; then
        ``done(result, None)``, or ``done(None, message)`` if nothing arrived."""
        index = int(self.effective_channels["channels"][cid]["index"])
        acc = [0.0, 0, 0.0, 0]

        def on(p: Processed) -> None:
            v = p.clean[index] if index < len(p.clean) else NAN
            if math.isfinite(v):
                acc[0] += v
                acc[1] += 1
            y = p.co2_pct if cid == "co2" else p.flow_lpm if cid == "flow" else p.pneumo_pa
            if math.isfinite(y):
                acc[2] += y
                acc[3] += 1

        off = self.on_sample(on)

        def finish() -> None:
            off()
            if not acc[1]:
                done(None, "No data arrived. Connect the device (or the simulator) first.")
            else:
                done(CaptureResult(acc[0] / acc[1], acc[2] / acc[3] if acc[3] else NAN, acc[1]), None)

        self.call_later(seconds * 1000, finish)

    # ---------- exports of the live session ----------

    def trend_csv(self) -> str:
        def f(v: float, d: int) -> str:
            return f"{v:.{d}f}" if math.isfinite(v) else ""

        rows = ["wall_time_iso,etco2_mmHg,rr_bpm,vt_ml,mv_lpm,mv_breath_lpm,quality"]
        rows += [
            ",".join((iso_ms(p.t * 1000), f(p.etco2, 1), f(p.rr, 1), f(p.vt, 0), f(p.mv, 2), f(p.mv_breath, 2), "excluded" if p.excluded and p.quality == "ok" else p.quality))
            for p in self.trend
        ]
        return "\n".join(rows) + "\n"

    def log_text(self) -> str:
        return "".join(f"{iso_ms(line.t)}\t{line.level}\t{line.text}\n" for line in self.log)

    def next_file_name(self) -> str:
        return session_file_name(self.settings.study_code, datetime.now())

    # ---------- misc ----------

    def _add_log(self, text: str, level: str = "info") -> None:
        self.log.append(LogLine(self.clock(), text, level))
        if len(self.log) > 2000:
            del self.log[:500]

    def _source_label(self) -> str:
        """The source's name; a Python acquisition server (co2r-daq) names its device in
        INFO, e.g. "NI Dev1 (Python)"."""
        src = self._source
        if src is None:
            return ""
        info = self._info or {}
        if src.kind == "bridge" and str(info.get("FW", "")).startswith("co2r-py") and info.get("ADC"):
            return f"{info['ADC']} (Python)"
        return src.label

    def snapshot(self) -> Snapshot:
        now = self.clock()
        connected = self._status == "connected"
        stale = {cid: not connected or now - self._last_valid_wall[cid] > 2000 for cid in CHANNEL_IDS}
        # numbers come only from breaths that passed the noise screen
        last_b = self.good[-1] if self.good else None
        last_period = last_b.period_sec if last_b else 4
        breath_stale = last_b is None or now - self._last_good_wall > max(15000, 3000 * last_period) or not connected
        last_any = self.breaths[-1] if self.breaths else None
        L = self._last
        pct = self.settings.co2_unit == "%"
        n = Numbers(
            co2_live=NAN if stale["co2"] or L is None else (L.co2_pct if pct else L.co2_mmhg),
            etco2=NAN if breath_stale or stale["co2"] else last_b.etco2,
            etco2_median=NAN if breath_stale or stale["co2"] else etco2_median(self.good),
            rr=NAN if breath_stale else respiratory_rate(self.good),
            fico2=NAN if breath_stale or stale["co2"] else last_b.fico2,
            flow=NAN if stale["flow"] or L is None else L.flow_lpm,
            peak_flow=NAN if breath_stale or stale["flow"] else self._flow_peak_last,
            pneumo_flow=NAN if stale["pneumo"] or L is None else L.pneumo_lpm,
            pneumo_pa=NAN if stale["pneumo"] or L is None else L.pneumo_pa,
            vt=NAN if breath_stale or stale["pneumo"] else last_b.vt_ml,
            # the last breath's own minute ventilation (VT x 60 / its length), not a 60 s average
            mv=NAN if breath_stale or stale["pneumo"] else last_b.mv_lpm,
            volts=list(L.volts) if L else [],
            rejected=f"{last_any.quality}: {last_any.reason}" if connected and last_any is not None and last_any.quality != "ok" else "",
            vte=NAN if breath_stale or stale["pneumo"] else last_b.vte_ml,
            volume_mismatch_pct=NAN if breath_stale or stale["pneumo"] else self.volume_mismatch_pct(),
            co2_lag_sec=NAN if breath_stale else self.co2_lag_sec(),
            spo2=L.spo2 if connected and L is not None and now - self._spo2_valid_wall <= 5000 else NAN,
        )
        rec = self.recorder
        src = self._source
        return Snapshot(
            now=now,
            status=self._status,
            status_detail=self._status_detail,
            source_kind=src.kind if src else None,
            source_label=self._source_label(),
            info=self._info,
            rate_hz=self._rate(),
            measured_rate_hz=self._measured_rate if connected else 0,
            received=self._seq.received,
            dropped=self._seq.dropped,
            drop_rate=self._seq.drop_rate,
            bad_lines=self._bad_lines,
            numbers=n,
            stale=stale,
            breath_stale=breath_stale,
            alarms=self.alarms.list(),
            silence_ms=self.alarms.silence_remaining_ms(now),
            audio_armed=self.audio.armed,
            audio_backend=self.audio.backend,
            recording=RecordingState(rec.active, rec.started_at, rec.file_name, rec.bytes, rec.rec is not None, rec.write_error, self.library_version, str(self.library.folder)),
            frozen=self.frozen,
            trend_version=self.trend_version,
            settings=self.settings,
            channels=self.channels,
            using_file_calibration=self._file_channels is not None,
            replay_progress=src.progress if isinstance(src, ReplaySource) else None,
            known_port=self._known_port.label if self._known_port and src is None else None,
            awaiting_reconnect=self._awaiting_reconnect,
            i2c_scan=self._i2c_scan,
            test=self.test_state(now),
            valve=("bag" if self._valve else "air") if self.valve_available() else None,
            hardware=self.hardware,
            single_flow=self.single_flow,
            capno=self._merge.fresh_numbers if self._merge is not None else None,
            started_at=self.started_at,
        )

    def settings_json(self) -> dict:
        return to_dict(self.settings)
