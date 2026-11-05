"""Alarm limits, delays and audio; used in the monitor's drawer and in Settings > Alarms."""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from ..engine.monitor import MonitorEngine
from ..settings import AlarmSettings
from .widgets import Form, NumberEdit, Seg, label, toggle

SOURCE_HINT = {
    "etco2": "Compared once per breath, after each expiration.",
    "live": "Live CO2 alarms when the waveform itself stays past a limit, e.g. raised inspired CO2 during rebreathing.",
}


class AlarmForm(Form):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        a = engine.settings.alarms

        self.source = Seg([("etco2", "EtCO2 per breath"), ("live", "Live CO2")], a.source)
        self.source.changed.connect(lambda v: self._set(source=v))
        self.add("source", "Alarm on", self.source, hint=SOURCE_HINT[a.source])
        self.source_hint: QLabel = self.rows["source"][2]

        self.high = self._num("high", "High CO2 limit", a.high_mm_hg, "mmHg", 5, 150, "Drawn as the dashed line on the capnogram and trend.", "high_mm_hg")
        self.low_on = toggle(a.low_mm_hg is not None, lambda on: self._set(low_mm_hg=25 if on else None))
        self.add("lowon", "Low CO2 limit", self.low_on, hint="Optional. Helps catch apnea, a leak or a disconnected sample line.")
        self.low = self._num("low", "Low limit", a.low_mm_hg if a.low_mm_hg is not None else 25, "mmHg", 0, 100, None, "low_mm_hg")
        self.delay_b = NumberEdit(a.delay_breaths, 1, 10)
        self.delay_b.committed.connect(lambda v: self._set(delay_breaths=round(v)))
        self.add("delayb", "Trigger delay", self.delay_b, "breaths", "Consecutive breaths past the limit before alarming. Stops a single breath from alarming.")
        self.delay_s = self._num("delays", "Trigger delay", a.delay_sec, "s", 0, 60, None, "delay_sec")
        self.hyst = self._num("hyst", "Hysteresis", a.hysteresis_mm_hg, "mmHg", 0, 10, "A high alarm clears only below limit − hysteresis.", "hysteresis_mm_hg")
        self.apnea = self._num("apnea", "Apnea alarm after", a.apnea_sec, "s", 0, 120, "High-priority alarm when no breath is detected for this long. 0 turns it off.", "apnea_sec")
        self.silence = self._num("silence", "Silence duration", a.silence_sec, "s", 10, 120, "Audio comes back on by itself after this. Maximum 120 s.", "silence_sec")

        vol = QWidget()
        h = QHBoxLayout(vol)
        h.setContentsMargins(0, 0, 0, 0)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(10, 100)
        self.volume.setSingleStep(5)
        self.volume.setPageStep(10)
        self.volume.setFixedWidth(160)
        self.volume.setAccessibleName("Alarm volume")
        self.vol_label = label("", "muted")
        self.vol_label.setMinimumWidth(48)
        self.volume.valueChanged.connect(lambda v: self.vol_label.setText(f"{v} %"))
        self.volume.sliderReleased.connect(lambda: self._set(volume=self.volume.value() / 100))
        self.volume.valueChanged.connect(lambda v: None if self.volume.isSliderDown() else self._set(volume=v / 100))
        h.addWidget(self.volume)
        h.addWidget(self.vol_label)
        self.add("vol", "Alarm volume", vol, hint="Cannot be set to zero.")
        self.refresh(a)

    def _num(self, key, text, value, unit, lo, hi, hint, field) -> NumberEdit:
        ne = NumberEdit(value, lo, hi)
        ne.committed.connect(lambda v: self._set(**{field: v}))
        self.add(key, text, ne, unit, hint)
        return ne

    def _set(self, **patch) -> None:
        a: AlarmSettings = self.engine.settings.alarms
        if all(getattr(a, k) == v for k, v in patch.items()):
            return
        self.engine.update_settings(alarms=dataclasses.replace(a, **patch))
        self.refresh(self.engine.settings.alarms)

    def refresh(self, a: AlarmSettings) -> None:
        self.source.set_value(a.source)
        self.source_hint.setText(SOURCE_HINT[a.source])
        self.high.set(a.high_mm_hg)
        self.low_on.blockSignals(True)
        self.low_on.setChecked(a.low_mm_hg is not None)
        self.low_on.blockSignals(False)
        self.show_row("low", a.low_mm_hg is not None)
        if a.low_mm_hg is not None:
            self.low.set(a.low_mm_hg)
        self.show_row("delayb", a.source == "etco2")
        self.show_row("delays", a.source == "live")
        self.delay_b.set(a.delay_breaths)
        self.delay_s.set(a.delay_sec)
        self.hyst.set(a.hysteresis_mm_hg)
        self.apnea.set(a.apnea_sec)
        self.silence.set(a.silence_sec)
        if not self.volume.isSliderDown():
            self.volume.blockSignals(True)
            self.volume.setValue(round(a.volume * 100))
            self.volume.blockSignals(False)
            self.vol_label.setText(f"{round(a.volume * 100)} %")
