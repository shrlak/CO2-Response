"""Settings > Display & units, and Settings > Alarms."""

from __future__ import annotations

import dataclasses

from PySide6.QtWidgets import QVBoxLayout

from ...engine.monitor import MonitorEngine, Snapshot
from ..alarm_form import AlarmForm
from ..widgets import (
    Form,
    Seg,
    button,
    combo,
    hbox,
    label,
    number_field,
    set_combo,
    toggle,
)
from .common import Page


class DisplayPage(Page):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        s = engine.settings
        p = s.processing
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label("Display & units", "h2"))
        lay.addWidget(label("How the monitor looks and how raw signals are turned into numbers.", "lead", wrap=True))
        lay.addWidget(label("SCREEN", "h3"))
        f = Form()
        self.theme = Seg([("dark", "Dark"), ("light", "Light"), ("system", "Match system")], s.theme)
        self.theme.changed.connect(lambda v: engine.update_settings(theme=v))
        f.add("theme", "Theme", self.theme, hint="Dark is easier on the eyes in a dim room or overnight. Light suits a bright bench.")
        self.unit = Seg([("mmHg", "mmHg"), ("%", "%")], s.co2_unit)
        self.unit.changed.connect(lambda v: engine.update_settings(co2_unit=v))
        f.add("unit", "CO2 units", self.unit, hint="Alarm limits are always set in mmHg.")
        self.sweep = combo([(x, f"{x} s per screen") for x in (6, 12, 30, 60)], s.sweep_sec, lambda v: engine.update_settings(sweep_sec=v))
        f.add("sweep", "Sweep speed", self.sweep)
        self.co2scale = combo([(x, f"0–{x} mmHg") for x in (40, 60, 80, 100)], s.co2_scale_max_mm_hg, lambda v: engine.update_settings(co2_scale_max_mm_hg=v))
        f.add("co2scale", "Capnogram scale", self.co2scale)
        self.flowscale = combo([(x, f"±{x} L/min") for x in (30, 60, 100, 200)], s.flow_scale_lpm, lambda v: engine.update_settings(flow_scale_lpm=v))
        f.add("flowscale", "Flow scale", self.flowscale)
        self.raw = toggle(s.show_raw_volts, lambda v: engine.update_settings(show_raw_volts=v))
        f.add("raw", "Show raw volts on the monitor", self.raw, hint="Useful while calibrating; hide it for clinical use.")
        lay.addWidget(f)

        lay.addSpacing(10)
        lay.addWidget(label("SIGNAL PROCESSING", "h3"))
        g = Form()
        self.pb = number_field(g, "pb", "Barometric pressure", p.barometric_mm_hg, lambda v: self._p(barometric_mm_hg=v), "mmHg",
                               "Used to convert % CO2 to mmHg. Pittsburgh is typically about 735 mmHg; enter the lab's reading for accuracy.", 500, 800)
        self.wv = toggle(p.water_vapor, lambda v: self._p(water_vapor=v))
        g.add("wv", "Water-vapor correction (BTPS)", self.wv, hint="Subtracts 47 mmHg before converting % to mmHg.")
        self.filt = toggle(p.filter_on, lambda v: self._p(filter_on=v))
        g.add("filt", "Low-pass filter", self.filt, hint="2nd-order Butterworth on every channel.")
        self.fhz = number_field(g, "fhz", "Filter cutoff", p.filter_hz, lambda v: self._p(filter_hz=v), "Hz", lo=1, hi=40)
        self.db = number_field(g, "db", "Breath detection deadband", round(p.deadband_lpm / 60, 3), lambda v: self._p(deadband_lpm=v * 60), "L/s",
                               "Flow must pass ± this to count as a new inspiration. Raise it if noise is counted as breaths.", 0.005, 0.5)
        self.minb = number_field(g, "minb", "Minimum breath length", p.min_breath_sec, lambda v: self._p(min_breath_sec=v), "s", lo=0.3, hi=5)
        self.lag = number_field(g, "lag", "CO2 delay behind flow", p.co2_delay_ms, lambda v: self._p(co2_delay_ms=round(v)), "ms",
                                "How long the capnograph's CO2 lags the pneumotach flow (sample-line transport plus sensor response). CO2 is shifted back by this before breaths are cut, so each breath gets its own EtCO2 and FiCO2. A sidestream capnograph usually needs 1000–3000 ms; a mainstream sensor much less. Measure it with normal breathing.",
                                0, 6000, 50)
        self.lag_btn = button("Measure from 20 s of breathing", self._measure_lag)
        self.lag_note = label("", "note", wrap=True)
        g.add("lagm", "", hbox(self.lag_btn, self.lag_note, stretch_end=True))
        self.despike = toggle(p.despike, lambda v: self._p(despike=v))
        g.add("despike", "Remove spikes (Hampel filter)", self.despike, hint='Replaces single glitchy samples with the median of their neighbours before anything is calculated. Keeps real fast changes such as the capnogram upstroke. Adds 30 ms of delay.')
        self.reject = toggle(p.reject_bad_breaths, lambda v: self._p(reject_bad_breaths=v))
        g.add("reject", "Leave noisy breaths out", self.reject, hint=('Each breath is checked before it is used: too noisy (signal-to-noise below 8), a partial breath with no alveolar plateau, an impossible value, or an isolated EtCO2 jump. Those breaths are left out of the numbers, the EtCO2 trend, the response plot and the EtCO2 alarms, but stay in the recording, marked. A run of them raises a technical alarm.'))
        lay.addWidget(g)
        self.proc_form = g

        lay.addSpacing(10)
        lay.addWidget(label("REBREATHING TEST", "h3"))
        t = Form()
        self.skip = number_field(t, "skip", "Breaths left out at the start", p.response_skip_breaths, lambda v: self._p(response_skip_breaths=int(round(v))), "breaths",
                                 "The deep breaths the subject is coached to take when the valve turns to the bag. They are left out of the MV-against-EtCO2 slope.", 0, 20, 1)
        lay.addWidget(t)
        lay.addWidget(label("The end-test CO2 limit, the warning before it and the time limit are under Settings > Alarms.", "note", wrap=True))
        lay.addStretch(1)

    def _measure_lag(self) -> None:
        self.lag_btn.setEnabled(False)
        self.lag_note.setText("Measuring… keep breathing normally through the mouthpiece.")

        def done(res, err) -> None:
            self.lag_btn.setEnabled(True)
            if err:
                self.lag_note.setText(err)
                return
            lag, n = res
            ms = int(round(lag * 1000 / 10) * 10)
            self._p(co2_delay_ms=ms)
            self.lag.set(ms)
            self.lag_note.setText(f"CO2 lags flow by {ms} ms (median of {n} breaths). Saved.")

        try:
            self.engine.measure_co2_delay(20, done)
        except RuntimeError as ex:
            done(None, str(ex))

    def _p(self, **patch) -> None:
        self.engine.update_settings(processing=dataclasses.replace(self.engine.settings.processing, **patch))

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        st = s.settings
        p = st.processing
        self.theme.set_value(st.theme)
        self.unit.set_value(st.co2_unit)
        set_combo(self.sweep, st.sweep_sec)
        set_combo(self.co2scale, st.co2_scale_max_mm_hg)
        set_combo(self.flowscale, st.flow_scale_lpm)
        self.pb.set(p.barometric_mm_hg)
        self.fhz.set(p.filter_hz)
        self.db.set(round(p.deadband_lpm / 60, 3))
        self.minb.set(p.min_breath_sec)
        self.lag.set(p.co2_delay_ms)
        self.skip.set(p.response_skip_breaths)
        self.proc_form.show_row("fhz", p.filter_on)


class AlarmsPage(Page):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        self.window = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label("Alarms", "h2"))
        lay.addWidget(label("CO2 limits, delays and audio. Changes apply immediately. Technical alarms (no data, sensor at rail, device disconnected) are always on.", "lead", wrap=True))
        self.form = AlarmForm(engine)
        lay.addWidget(self.form)
        self.arm = button("Arm audio", lambda: engine.arm_audio(), kind="primary")
        lay.addLayout(hbox(self.arm, button("Test alarm (high, then medium tone)", self._test), stretch_end=True))
        self.audio = label("", "note", wrap=True)
        lay.addWidget(self.audio)
        lay.addStretch(1)

    def _test(self) -> None:
        if not self.engine.audio.armed:
            self.window.toast("No sound output is armed: press Arm audio first to hear the test tones.")
        self.engine.test_alarm()

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.form.refresh(s.settings.alarms)
        self.arm.setText(f"Audio armed ({s.audio_backend})" if s.audio_armed else "Arm audio")
        a = self.engine.audio
        self.audio.setText(
            "Tones play through the computer's speaker." if s.audio_backend == "speaker" else f"No sound device was found ({a.error}); alarms use the system beep." if s.audio_armed else f"No sound output: {a.error or 'unknown'}"
        )
