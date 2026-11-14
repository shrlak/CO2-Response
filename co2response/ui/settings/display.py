"""Settings > Display & units, and Settings > Alarms."""

from __future__ import annotations

import dataclasses

from PySide6.QtWidgets import QVBoxLayout

from ... import __version__, updater
from ...engine.monitor import MonitorEngine, Snapshot
from ...forms import PROCESSING_FIELDS, SPO2_FIELDS
from ..alarm_form import AlarmForm
from ..spec_form import SpecForm
from ..widgets import (
    Form,
    Seg,
    button,
    combo,
    hbox,
    label,
    set_combo,
    toggle,
)
from .common import Page


class DisplayPage(Page):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        s = engine.settings
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
        g = SpecForm(PROCESSING_FIELDS, lambda: engine.settings.processing, self._p)
        lay.addWidget(g)
        self.proc_form = g

        lay.addSpacing(10)
        lay.addWidget(label("SPO2 (SAFETY CHANNEL)", "h3"))
        self.spo2_form = SpecForm(SPO2_FIELDS, lambda: engine.settings.spo2, lambda patch: engine.update_settings(spo2=dataclasses.replace(engine.settings.spo2, **patch)))
        lay.addWidget(self.spo2_form)
        self.autoupdate = None
        if updater.is_frozen():  # the standalone app only: other installs update with `co2response --update`
            lay.addSpacing(10)
            lay.addWidget(label("UPDATES", "h3"))
            u = Form()
            self.autoupdate = toggle(s.auto_update, lambda v: engine.update_settings(auto_update=v))
            u.add(
                "autoupdate",
                "Update automatically",
                hbox(self.autoupdate, button("Check now", self._check_now, small=True)),
                hint=f"This is version {__version__}. A newer release is downloaded in the background (checked once a day) and installed the next time the app is started, never during a recording; the previous version is kept beside it. Set CO2R_NO_UPDATE=1 to turn this off for a computer that must never change.",
            )
            lay.addWidget(u)
        lay.addStretch(1)

    def _check_now(self) -> None:
        updater.background.start(force=True)
        self.engine._add_log("Checking for a newer version …")

    def _p(self, patch: dict) -> None:
        self.engine.update_settings(processing=dataclasses.replace(self.engine.settings.processing, **patch))

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        st = s.settings
        p = st.processing
        self.theme.set_value(st.theme)
        self.unit.set_value(st.co2_unit)
        set_combo(self.sweep, st.sweep_sec)
        set_combo(self.co2scale, st.co2_scale_max_mm_hg)
        set_combo(self.flowscale, st.flow_scale_lpm)
        self.proc_form.refresh(p, s)
        self.spo2_form.refresh(st.spo2, s)
        if self.autoupdate is not None and self.autoupdate.isChecked() != st.auto_update:
            self.autoupdate.setChecked(st.auto_update)


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
        lay.addWidget(label("The warning limit tells the operator the end of the test is near; the stop limit says to turn the valve back to room air now.", "note", wrap=True))
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
        self.form.refresh(s.settings.alarms, s)
        self.arm.setText(f"Audio armed ({s.audio_backend})" if s.audio_armed else "Arm audio")
        a = self.engine.audio
        self.audio.setText(
            "Tones play through the computer's speaker." if s.audio_backend == "speaker" else f"No sound device was found ({a.error}); alarms use the system beep." if s.audio_armed else f"No sound output: {a.error or 'unknown'}"
        )
