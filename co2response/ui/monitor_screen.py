"""The monitor screen: three waveform rows with their numbers, the trend, and the soft keys."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..engine.monitor import MonitorEngine, Snapshot
from ..processing.co2 import mmhg_to_pct
from .alarm_form import AlarmForm
from .monitor_widgets import NumericTile, SweepWidget
from .sim_panel import SimPanel
from .trend import TrendPanel
from .widgets import button, fmt, hbox, label, mss, scroll_page, set_prop

#: relative heights of the CO2, flow and pneumotach rows
ROW_STRETCH = (130, 100, 100)


class SoftKey(QPushButton):
    """A large key with a second, smaller line of text."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("softkey")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)
        self.main = QLabel()
        self.sub = QLabel()
        for lab in (self.main, self.sub):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(lab)
        self.clicked.connect(lambda _=False: on_click())
        self._key = None

    def set(self, main: str, sub: str, state: str | None = None, palette=None) -> None:
        key = (main, sub, state, palette.name if palette else None)
        if key == self._key:
            return
        self._key = key
        self.main.setText(main.upper())
        self.sub.setText(sub)
        set_prop(self, "state", state or "")
        if palette is not None:
            color = {"armed": palette.ok, "need": palette.advisory, "active": palette.alarm_med, "rec": palette.alarm_high}.get(state or "", palette.text)
            self.main.setStyleSheet(f"font-weight:600; font-size:15px; letter-spacing:1px; color:{color};")
            self.sub.setStyleSheet(f"font-size:12px; color:{palette.muted};")
        self.setAccessibleName(f"{main}. {sub}")


class Overlay(QWidget):
    """Covers its parent and keeps the same size."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False


class ConnectOverlay(Overlay):
    def __init__(self, parent: QWidget, screen: "MonitorScreen") -> None:
        super().__init__(parent)
        self.setObjectName("backdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("overlayCard")
        card.setMaximumWidth(600)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        self.title = label("No signal source", "h1")
        self.detail = label("", "err", wrap=True)
        self.waiting = label("Waiting for the Arduino to be plugged back in…", "warn")
        info = label(
            "Connect the Arduino over USB, or start the simulator to try the monitor without hardware. Waveforms and alarms start as soon as data arrives.",
            "muted",
            wrap=True,
        )
        self.reconnect = button("", screen.reconnect_known, kind="primary")
        self.choose = button("Connect Arduino (USB)", screen.choose_port, kind="primary")
        row = hbox(self.reconnect, self.choose, button("Start simulator", screen.start_simulator), button("More sources…", lambda: screen.open_settings.emit("connection")))
        for w in (self.title, self.detail, self.waiting, info):
            lay.addWidget(w)
        lay.addLayout(row)
        outer.addWidget(card)

    def update_from(self, s: Snapshot) -> None:
        self.title.setText("Connection lost" if s.status == "error" else "No signal source")
        self.detail.setText(s.status_detail)
        self.detail.setVisible(bool(s.status_detail))
        self.waiting.setVisible(s.awaiting_reconnect)
        self.reconnect.setVisible(bool(s.known_port))
        self.reconnect.setText(f"Reconnect {s.known_port}" if s.known_port else "")
        self.choose.setText("Choose another port…" if s.known_port else "Connect Arduino (USB)")
        set_prop(self.choose, "kind", "" if s.known_port else "primary")


class AlarmDrawer(Overlay):
    def __init__(self, parent: QWidget, engine: MonitorEngine, on_test: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setObjectName("backdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        self.panel = panel = QFrame()
        panel.setObjectName("drawer")
        panel.setFixedWidth(440)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 18, 20, 18)
        pl.setSpacing(12)
        pl.addWidget(label("ALARM LIMITS", "h2"))
        pl.addWidget(label("Changes apply immediately and are saved.", "note"))
        self.form = AlarmForm(engine)
        pl.addWidget(scroll_page(self.form), 1)
        self.done = button("Done", self.hide, kind="primary")
        pl.addLayout(hbox(button("Test alarm", on_test), 1, self.done))
        lay.addWidget(panel)
        self.hide()

    def mousePressEvent(self, e) -> None:  # noqa: N802 (a click outside the panel closes it)
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.hide()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class MarkPopover(QFrame):
    def __init__(self, parent: QWidget, engine: MonitorEngine) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setObjectName("popover")
        self.setFixedWidth(320)
        self.mid = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(label("Label for the marker just placed", "note"))
        self.edit = QLineEdit()
        self.edit.returnPressed.connect(self.save)
        lay.addWidget(self.edit)
        self.keep = button("", self.hide, small=True)
        lay.addLayout(hbox(button("Save label", self.save, kind="primary", small=True), self.keep, 1))
        self.hide()

    def open_for(self, mid: int, text: str, anchor: QWidget) -> None:
        self.mid = mid
        self.edit.setText(text)
        self.keep.setText(f"Keep “{text}”")
        pos = anchor.mapTo(self.parentWidget(), anchor.rect().topLeft())
        self.adjustSize()
        self.move(pos.x(), pos.y() - self.height() - 6)
        self.show()
        self.raise_()
        self.edit.setFocus()
        self.edit.selectAll()

    def save(self) -> None:
        self.engine.rename_marker(self.mid, self.edit.text())
        self.hide()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class MonitorScreen(QWidget):
    open_settings = Signal(str)
    toast = Signal(str)
    toggle_recording = Signal()
    choose_port_requested = Signal()

    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.rows = QWidget()
        grid = QGridLayout(self.rows)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.sweeps = {
            "co2": SweepWidget("co2", "CO2", "mmHg"),
            "flow": SweepWidget("flow", "Flow", "L/min"),
            "pneumo": SweepWidget("pneumo", "Pneumotach", "L/min"),
        }
        self.tiles = {
            "etco2": NumericTile("co2", "EtCO2", "mmHg", big=True),
            "rr": NumericTile("co2", "RR", "/min"),
            "fico2": NumericTile("co2", "FiCO2", "mmHg"),
            "flow": NumericTile("flow", "Flow", "L/min"),
            "peak": NumericTile("flow", "Peak insp", "L/min"),
            "vt": NumericTile("pneumo", "VT", "mL"),
            "mv": NumericTile("pneumo", "MV", "L/min"),
        }
        co2nums = QGridLayout()
        co2nums.setSpacing(6)
        co2nums.addWidget(self.tiles["etco2"], 0, 0, 2, 1)
        co2nums.addWidget(self.tiles["rr"], 0, 1)
        co2nums.addWidget(self.tiles["fico2"], 1, 1)
        co2nums.setColumnStretch(0, 135)
        co2nums.setColumnStretch(1, 100)
        rows = [("co2", co2nums), ("flow", hbox(self.tiles["flow"], self.tiles["peak"], spacing=6)), ("pneumo", hbox(self.tiles["vt"], self.tiles["mv"], spacing=6))]
        self.holders: dict[str, QWidget] = {}
        for r, (cid, nums) in enumerate(rows):
            grid.addWidget(self.sweeps[cid], r, 0)
            holder = QWidget()
            holder.setLayout(nums)
            nums.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(holder, r, 1)
            self.holders[cid] = holder
            self.sweeps[cid].on_expand = lambda cid=cid: self.toggle_focus(cid)
        # the graphs take the space; the numbers keep a readable column
        grid.setColumnStretch(0, 73)
        grid.setColumnStretch(1, 27)
        self.grid = grid
        for r, s in enumerate(ROW_STRETCH):
            grid.setRowStretch(r, s)

        # waveforms above, the trend below (a third of the height, growing with the
        # window), and the simulation panel docked at the right when it is open
        self.trend = TrendPanel(engine)
        self.trend.expand.clicked.connect(lambda _=False: self.toggle_focus("trend"))
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.rows, 19)
        left.addWidget(self.trend, 10)
        body = QHBoxLayout()
        body.setSpacing(6)
        body.addLayout(left, 1)
        self.sim = SimPanel(engine, self.toggle_sim)
        body.addWidget(self.sim)
        outer.addLayout(body, 1)
        #: one graph enlarged to fill the monitor ("co2", "flow", "pneumo", "trend"), or None
        self.focus: str | None = None

        keys = QHBoxLayout()
        keys.setSpacing(6)
        self.k_silence = SoftKey(engine.toggle_silence)
        self.k_audio = SoftKey(lambda: engine.arm_audio())
        self.k_mark = SoftKey(self.mark)
        self.k_rec = SoftKey(self.toggle_recording.emit)
        self.k_freeze = SoftKey(lambda: engine.set_frozen(not engine.frozen))
        self.k_limits = SoftKey(self.show_drawer)
        self.k_sim = SoftKey(self.toggle_sim)
        self.k_settings = SoftKey(lambda: self.open_settings.emit(""))
        for k in (self.k_silence, self.k_audio, self.k_mark, self.k_rec, self.k_freeze, self.k_limits, self.k_sim, self.k_settings):
            keys.addWidget(k, 1)
        outer.addLayout(keys)

        self.overlay = ConnectOverlay(self.rows, self)
        self.overlay.hide()
        self.drawer = AlarmDrawer(self, engine, self.test_alarm)
        self.popover = MarkPopover(self, engine)
        self._epoch = -1

    # ---------- actions

    def mark(self) -> None:
        m = self.engine.add_marker()
        self.popover.open_for(m.id, m.label, self.k_mark)

    def show_drawer(self) -> None:
        self.drawer.form.refresh(self.engine.settings.alarms)
        self.drawer.show()
        self.drawer.raise_()
        self.drawer.done.setFocus()

    def test_alarm(self) -> None:
        if not self.engine.audio.armed:
            self.toast.emit("No sound output is armed: press Arm audio first to hear the test tones.")
        self.engine.test_alarm()

    def start_simulator(self) -> None:
        self.engine.connect("simulator")

    def toggle_sim(self) -> None:
        self.show_sim(not self.sim.isVisible())

    def show_sim(self, on: bool = True) -> None:
        self.sim.setVisible(on)
        if on:
            self.sim.refresh(self.engine.snapshot())

    def toggle_focus(self, target: str) -> None:
        self.set_focus(None if self.focus == target else target)

    def set_focus(self, target: str | None) -> None:
        """Enlarge one graph to fill the monitor, or (None) go back to the full layout."""
        self.focus = target
        for r, (cid, sw) in enumerate(self.sweeps.items()):
            show = target in (None, cid)
            sw.setVisible(show)
            self.holders[cid].setVisible(show)
            sw.expand.setChecked(target == cid)
            self.grid.setRowStretch(r, ROW_STRETCH[r] if show else 0)
        self.rows.setVisible(target != "trend")
        self.trend.setVisible(target in (None, "trend"))
        self.trend.expand.setChecked(target == "trend")

    def reconnect_known(self) -> None:
        if not self.engine.reconnect_known_port():
            self.choose_port()

    def choose_port(self) -> None:
        self.choose_port_requested.emit()

    # ---------- drawing

    def frame(self) -> None:
        """~30 times a second: move new samples into the sweeps."""
        e = self.engine
        if e.wave_epoch != self._epoch:
            self._epoch = e.wave_epoch
            for sw in self.sweeps.values():
                sw.clear()
        waves = e.take_waves()
        for cid, sw in self.sweeps.items():
            if waves[cid]:
                sw.push(waves[cid])  # draws the new segments and repaints only that strip

    def refresh(self, s: Snapshot, blink_fast: bool) -> None:
        from . import theme

        p = theme.current
        st = s.settings
        n = s.numbers
        pb, wv = st.processing.barometric_mm_hg, st.processing.water_vapor
        pct = st.co2_unit == "%"

        def disp(mm):
            return mmhg_to_pct(mm, pb, wv) if pct else mm

        digits = 1 if pct else 0
        unit = "%" if pct else "mmHg"
        co2_max = disp(st.co2_scale_max_mm_hg)
        ticks = [t for t in (0, 2, 4, 6, 8, 10, 12) if t <= co2_max + 0.01] if pct else [t for t in (0, 20, 40, 60, 80, 100) if t <= co2_max]
        fs = st.flow_scale_lpm
        rate = s.rate_hz
        sw = self.sweeps
        sw["co2"].unit = unit
        sw["co2"].set_options(0, co2_max, ticks, False, disp(st.alarms.high_mm_hg), st.sweep_sec, rate)
        for cid in ("flow", "pneumo"):
            sw[cid].set_options(-fs, fs, [-fs, 0, fs], True, None, st.sweep_sec, rate)

        # the list is in priority order, so the stop alarm comes before the warning
        co2_alarm = next((a for a in s.alarms if a.id in ("co2Stop", "co2High", "co2Low") and a.state == "active"), None)
        apnea = next((a for a in s.alarms if a.id == "apnea" and a.state == "active"), None)
        tag = "FROZEN" if s.frozen else "SIMULATED" if s.source_kind == "simulator" else "REPLAY" if s.source_kind == "replay" else None
        sw["co2"].set_tag(tag, bool((co2_alarm and co2_alarm.priority == "high") or apnea))

        def lim(v):
            return "off" if v is None else fmt(disp(v), digits)

        idx = {cid: int(s.channels["channels"][cid]["index"]) for cid in ("co2", "flow", "pneumo")}

        def raw(cid):
            i = idx[cid]
            v = n.volts[i] if i < len(n.volts) else float("nan")
            return f"{v:.4f} V" if st.show_raw_volts and v == v else ""

        def stale_val(stale, v, d=0):
            return "---" if stale else fmt(v, d)

        t = self.tiles
        t["etco2"].set(
            stale_val(s.stale["co2"], disp(n.etco2), digits),
            unit,
            stale=s.stale["co2"],
            alarm=("high" if co2_alarm.priority == "high" else "medium") if co2_alarm else None,
            flash=bool(co2_alarm and not co2_alarm.acknowledged and blink_fast),
            limits=(lim(st.alarms.high_mm_hg) + (f" / {lim(st.alarms.stop_mm_hg)}" if st.alarms.stop_mm_hg is not None else ""), lim(st.alarms.low_mm_hg)),
            qualifier="no CO2 signal" if s.stale["co2"] else f"last breath left out ({n.rejected.split(':')[0]})" if n.rejected else f"median 3 br: {fmt(disp(n.etco2_median), digits)}",
            raw=raw("co2"),
        )
        t["rr"].set(fmt(n.rr), alarm="high" if apnea else None, flash=bool(apnea and not apnea.acknowledged and blink_fast), qualifier="no breath detected" if n.rr != n.rr and s.status == "connected" else "")
        t["fico2"].set(stale_val(s.stale["co2"], disp(n.fico2), digits), unit, stale=s.stale["co2"])
        t["flow"].set(stale_val(s.stale["flow"], n.flow), stale=s.stale["flow"], raw=raw("flow"))
        t["peak"].set(stale_val(s.stale["flow"], n.peak_flow), stale=s.stale["flow"])
        exp = f"exp {fmt(n.vte)} mL · " if n.vte == n.vte else ""
        t["vt"].set(stale_val(s.stale["pneumo"], n.vt), stale=s.stale["pneumo"], raw=raw("pneumo"), qualifier="" if s.stale["pneumo"] else f"{exp}flow {fmt(n.pneumo_flow)} L/min")
        t["mv"].set(stale_val(s.stale["pneumo"], n.mv, 1), stale=s.stale["pneumo"], qualifier="" if s.stale["pneumo"] else "60 s average")

        disconnected = s.status in ("idle", "error")
        if disconnected and self.focus == "trend":
            self.set_focus(None)  # the connect card lives over the waveforms
        self.overlay.setVisible(disconnected)
        if disconnected:
            self.overlay.raise_()
            self.overlay.update_from(s)

        self.trend.refresh(s)

        a = st.alarms
        silenced = s.silence_ms > 0
        self.k_silence.set("Cancel silence" if silenced else "Silence", f"{mss(s.silence_ms)} left" if silenced else f"{a.silence_sec:g} s", "active" if silenced else None, p)
        self.k_audio.set("Audio armed" if s.audio_armed else "Arm audio", f"tones via {s.audio_backend}" if s.audio_armed else "no sound output yet", "armed" if s.audio_armed else "need", p)
        self.k_mark.set("Mark event", "hotkey M", None, p)
        self.k_rec.set("■ Stop rec" if s.recording.active else "● Record", "finishes the CSV file" if s.recording.active else "one CSV per session", "rec" if s.recording.active else None, p)
        self.k_freeze.set("Unfreeze" if s.frozen else "Freeze", "waveforms only", "active" if s.frozen else None, p)
        low = f" · low {fmt(disp(a.low_mm_hg), digits)}" if a.low_mm_hg is not None else ""
        stop = f" · stop {fmt(disp(a.stop_mm_hg), digits)}" if a.stop_mm_hg is not None else ""
        self.k_limits.set("Alarm limits", f"{'warn' if stop else 'high'} {fmt(disp(a.high_mm_hg), digits)}{stop}{low}", None, p)
        sim_open = self.sim.isVisible()
        self.k_sim.set(
            "Simulation",
            "hide the controls" if sim_open else "situations and values" if s.source_kind == "simulator" else "try situations, no hardware",
            "active" if sim_open else None,
            p,
        )
        self.k_settings.set("Settings", "calibration, connection", None, p)
        self.sim.refresh(s)
        if self.drawer.isVisible():
            self.drawer.form.refresh(a, s)
