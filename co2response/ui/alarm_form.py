"""Alarm limits, delays and audio; used in the monitor's drawer and in Settings > Alarms.

The rows come from ``co2response.forms.ALARM_FIELDS`` (shared with the browser page);
only the volume slider is built here."""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSlider, QWidget

from ..engine.monitor import MonitorEngine
from ..forms import ALARM_FIELDS
from ..settings import AlarmSettings
from .spec_form import SpecForm
from .widgets import label


class AlarmForm(SpecForm):
    def __init__(self, engine: MonitorEngine) -> None:
        self.engine = engine
        super().__init__(ALARM_FIELDS, lambda: engine.settings.alarms, self._set)

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
        self.volume.sliderReleased.connect(lambda: self._set({"volume": self.volume.value() / 100}))
        self.volume.valueChanged.connect(lambda v: None if self.volume.isSliderDown() else self._set({"volume": v / 100}))
        h.addWidget(self.volume)
        h.addWidget(self.vol_label)
        self.add("vol", "Alarm volume", vol, hint="Cannot be set to zero.")
        self.refresh(engine.settings.alarms)

    def _set(self, patch: dict) -> None:
        a: AlarmSettings = self.engine.settings.alarms
        if all(getattr(a, k) == v for k, v in patch.items()):
            return
        self.engine.update_settings(alarms=dataclasses.replace(a, **patch))

    def refresh(self, a: AlarmSettings | None = None, snapshot=None) -> None:
        a = self.engine.settings.alarms if a is None else a
        super().refresh(a, snapshot)
        vol = getattr(self, "volume", None)
        if vol is not None and not vol.isSliderDown():
            vol.blockSignals(True)
            vol.setValue(round(a.volume * 100))
            vol.blockSignals(False)
            self.vol_label.setText(f"{round(a.volume * 100)} %")
