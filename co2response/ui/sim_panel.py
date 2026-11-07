"""The simulation panel beside the monitor (desktop): ready-made situations, a slider or
switch for every simulator value, and the values set next to what the monitor reads.

Built from the same lists as the browser's panel (``co2response/sim/controls.py``).
Sliders change the running simulator while they are dragged.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..engine.monitor import MonitorEngine, Snapshot
from ..sim.controls import (
    GROUPS,
    READBACK,
    SITUATION_BY_ID,
    SITUATION_GROUPS,
    SITUATIONS,
    Slider,
    Switch,
    fmt_value,
    monitor_values,
    scenario_note,
    situation_label,
    slider_index,
    slider_value,
    switch_on,
)
from .widgets import button, label, scroll_page, set_prop


def _num(v, d: int) -> str:
    if v is None or not math.isfinite(v):
        return "--"
    s = f"{v:.{d}f}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s


def clipping_text(sensors: list[str]) -> str:
    if not sensors:
        return ""
    what = " and ".join(sensors)
    return f"Peak flow is beyond the {what}'s range: the signal clips at the ADC rail, so breaths and volumes are lost. Lower VT or RR, or lengthen inspiration (I:E)."


STALE_CHANNEL = {"etco2": "co2", "fico2": "co2", "vt": "pneumo", "mv": "pneumo", "peak": "flow"}


class SliderRow(QWidget):
    def __init__(self, spec: Slider, on_change: Callable[[str, float], None]) -> None:
        super().__init__()
        self.spec = spec
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(2)
        name = QLabel(spec.label)
        self.value = QLabel()
        self.value.setStyleSheet("font-weight:700;")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, slider_index(spec, spec.hi))
        self.slider.setAccessibleName(spec.label)
        self.slider.setMinimumHeight(24)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(lambda i: self._changed(i, on_change))
        lay.addWidget(name, 0, 0)
        lay.addWidget(self.value, 0, 1)
        lay.addWidget(self.slider, 1, 0, 1, 2)
        if spec.hint:
            lay.addWidget(label(spec.hint, "note", wrap=True), 2, 0, 1, 2)
        lay.setColumnStretch(0, 1)

    def _changed(self, i: int, on_change) -> None:
        v = slider_value(self.spec, i)
        self.value.setText(fmt_value(self.spec, v))
        on_change(self.spec.key, v)

    def set(self, v: float) -> None:
        if self.slider.isSliderDown():
            return
        i = slider_index(self.spec, v)
        if self.slider.value() != i:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)
        self.value.setText(fmt_value(self.spec, v))


class SimPanel(QFrame):
    """Docked at the right of the monitor; hidden until the Simulation key opens it."""

    def __init__(self, engine: MonitorEngine, on_close: Callable[[], None]) -> None:
        super().__init__()
        self.engine = engine
        self.setObjectName("simpanel")
        self.setFixedWidth(380)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 6, 10)
        outer.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("SIMULATION")
        title.setObjectName("simtitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(button("Close", on_close, small=True))
        outer.addLayout(head)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(6)

        self.start_box = QFrame()
        self.start_box.setObjectName("simstart")
        sb = QVBoxLayout(self.start_box)
        sb.setContentsMargins(10, 8, 10, 8)
        sb.addWidget(label("The simulator is not the data source. Choose a situation, or start the simulator, to drive the monitor with the values below.", "note", wrap=True))
        sb.addWidget(button("Start simulator", lambda: engine.connect("simulator"), kind="primary"), 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.start_box)

        self.current = QLabel()
        self.current.setStyleSheet("font-size:17px; font-weight:700;")
        self.desc = label("", "note", wrap=True)
        self.warn = label("", "warn", wrap=True)
        lay.addWidget(label("SITUATION", "k"))
        lay.addWidget(self.current)
        lay.addWidget(self.desc)
        lay.addWidget(self.warn)

        rb = QGridLayout()
        rb.setHorizontalSpacing(10)
        rb.setVerticalSpacing(2)
        for c, h in ((1, "SET"), (2, "MONITOR")):
            lab = label(h, "k")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight)
            rb.addWidget(lab, 0, c)
        self.set_labels: dict[str, QLabel] = {}
        self.mon_labels: dict[str, QLabel] = {}
        for r, (k, name, unit, _) in enumerate(READBACK, start=1):
            rb.addWidget(QLabel(f"<b>{name}</b> <span style='font-size:12px'>{unit}</span>"), r, 0)
            for col, store in ((1, self.set_labels), (2, self.mon_labels)):
                v = QLabel("--")
                v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                v.setProperty("readback", "set" if col == 1 else "monitor")
                v.setMinimumWidth(70)
                store[k] = v
                rb.addWidget(v, r, col)
        rb.setColumnStretch(0, 1)
        lay.addLayout(rb)

        lay.addWidget(self._section("SITUATIONS"))
        self.sit_buttons: dict[str, QPushButton] = {}
        for g in SITUATION_GROUPS:
            lay.addWidget(label(g.upper(), "k"))
            grid = QGridLayout()
            grid.setSpacing(5)
            for i, sit in enumerate(x for x in SITUATIONS if x.group == g):
                b = QPushButton(sit.label)
                b.setProperty("sit", True)
                b.setCheckable(True)
                b.setToolTip(sit.description)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, sid=sit.id: self._situation(sid))
                self.sit_buttons[sit.id] = b
                grid.addWidget(b, i // 2, i % 2)
            lay.addLayout(grid)

        self.sliders: dict[str, SliderRow] = {}
        self.switches: dict[str, QCheckBox] = {}
        for g in GROUPS:
            lay.addWidget(self._section(g.title.upper()))
            for it in g.items:
                if isinstance(it, Slider):
                    row = SliderRow(it, lambda k, v: engine.set_sim(**{k: v}) if getattr(engine.settings.sim, k) != v else None)
                    self.sliders[it.key] = row
                    lay.addWidget(row)
                elif isinstance(it, Switch):
                    cb = QCheckBox(it.label)
                    cb.setStyleSheet("font-weight:600;")
                    cb.toggled.connect(lambda on, key=it.key: engine.set_sim_switch(key, on))
                    self.switches[it.key] = cb
                    lay.addWidget(cb)
                    if it.hint:
                        lay.addWidget(label(it.hint, "note", wrap=True))
        lay.addWidget(
            label(
                "The monitor measures its numbers from the simulated waveforms, as it would from real sensors, so they can differ a little "
                "from the values set here and take a few breaths to follow a change.",
                "note",
                wrap=True,
            )
        )
        lay.addStretch(1)
        outer.addWidget(scroll_page(body), 1)
        self.hide()

    @staticmethod
    def _section(text: str) -> QWidget:
        w = QLabel(text)
        w.setObjectName("simsection")
        return w

    def _situation(self, sid: str) -> None:
        self.engine.apply_situation(sid)
        self.refresh(self.engine.snapshot())

    def refresh(self, s: Snapshot) -> None:
        if not self.isVisible():
            return
        sim = s.settings.sim
        self.start_box.setVisible(s.source_kind != "simulator")
        self.current.setText(situation_label(sim))
        sit = SITUATION_BY_ID.get(sim.preset)
        self.desc.setText(sit.description if sit and not sim.adjusted else "Values changed by hand. Choose a situation to start again from its values.")
        for sid, b in self.sit_buttons.items():
            if b.isChecked() != (sid == sim.preset):
                b.setChecked(sid == sim.preset)
        for k, row in self.sliders.items():
            row.set(float(getattr(sim, k)))
        for k, cb in self.switches.items():
            on = switch_on(sim, k)
            if cb.isChecked() != on:
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
        exp = self.engine.sim_expected()
        mon = monitor_values(s.numbers)
        for k, _, _, d in READBACK:
            self.set_labels[k].setText(_num(exp.get(k), d) if exp else "--")
            ch = STALE_CHANNEL.get(k)
            self.mon_labels[k].setText("---" if ch and s.stale.get(ch) else _num(mon[k], d))
        warn = " ".join(x for x in (clipping_text(exp.get("clipping", []) if exp else []), scenario_note(sim)) if x)
        self.warn.setText(warn)
        self.warn.setVisible(bool(warn))
        set_prop(self, "running", s.source_kind == "simulator")
