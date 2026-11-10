"""Review a saved session: summary, zoomable charts, exports and the event list.
Drag across any chart to zoom all of them to that time range."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..engine.monitor import MonitorEngine
from ..storage.report import response_stats, session_response
from ..storage.session_file import (
    ParsedSession,
    Range,
    breaths_csv,
    clean_breaths,
    event_type,
    events_csv,
    summarize,
    write_session,
)
from . import theme
from .settings.common import downloads_dir, save_text
from .settings.data import export_report, export_session
from .theme import ui_font
from .widgets import StatGrid, button, duration, elapsed, fmt, hbox, label, scroll_page


class ZoomBox(pg.ViewBox):
    """A view box where dragging selects a time range (and nothing else moves)."""

    zoomed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__(enableMenu=False)
        self.setMouseEnabled(False, False)
        self._sel: pg.LinearRegionItem | None = None

    def mouseDragEvent(self, ev, axis=None) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        x0 = self.mapSceneToView(ev.buttonDownScenePos()).x()
        x1 = self.mapSceneToView(ev.scenePos()).x()
        if self._sel is None:
            c = QColor(theme.current.co2)
            c.setAlphaF(0.18)
            self._sel = pg.LinearRegionItem((x0, x1), movable=False, brush=c)
            self.addItem(self._sel, ignoreBounds=True)
        self._sel.setRegion((min(x0, x1), max(x0, x1)))
        if ev.isFinish():
            self.removeItem(self._sel)
            self._sel = None
            if abs((ev.scenePos() - ev.buttonDownScenePos()).x()) > 4:
                self.zoomed.emit(min(x0, x1), max(x0, x1))

    def wheelEvent(self, ev, axis=None) -> None:  # noqa: N802
        ev.ignore()


class ElapsedAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [elapsed(v) for v in values]


class DualPlot(pg.PlotWidget):
    """Time on x, one series family on the left axis and another on the right."""

    def __init__(self, left: str, right: str, height: int, on_zoom) -> None:
        self.vb = ZoomBox()
        super().__init__(viewBox=self.vb, axisItems={"bottom": ElapsedAxis("bottom")})
        self.setFixedHeight(height)
        pi = self.getPlotItem()
        pi.hideButtons()
        pi.setMenuEnabled(False)
        pi.showAxis("right")
        pi.setLabel("left", left)
        pi.setLabel("right", right)
        self.vb2 = pg.ViewBox(enableMenu=False)
        self.vb2.setMouseEnabled(False, False)
        pi.scene().addItem(self.vb2)
        pi.getAxis("right").linkToView(self.vb2)
        self.vb2.setXLink(pi)
        self.vb.sigResized.connect(lambda: self.vb2.setGeometry(self.vb.sceneBoundingRect()))
        self.vb.zoomed.connect(on_zoom)
        self.legend = pi.addLegend(offset=(10, 4), labelTextSize="11pt")
        self.legend.setColumnCount(4)
        self.events: list[pg.InfiniteLine] = []
        self.style()

    def style(self) -> None:
        p = theme.current
        self.setBackground(p.panel)
        pi = self.getPlotItem()
        for name in ("left", "right", "bottom"):
            ax = pi.getAxis(name)
            ax.setPen(pg.mkPen(p.grid))
            ax.setTextPen(pg.mkPen(p.muted))
            ax.setStyle(tickFont=ui_font(14))
            ax.label.setDefaultTextColor(QColor(p.muted))
        pi.showGrid(x=True, y=True, alpha=0.3)
        self.legend.setLabelTextColor(p.muted)

    def curve(self, x, y, color: str, right: bool = False, name: str = "", points: bool = False) -> pg.PlotDataItem:
        it = pg.PlotDataItem(np.asarray(x, float), np.asarray(y, float), pen=pg.mkPen(color, width=2), name=name, connect="finite")
        if points:
            it.setSymbol("o")
            it.setSymbolSize(6)
            it.setSymbolBrush(color)
            it.setSymbolPen(None)
        if right:
            self.vb2.addItem(it)
            self.legend.addItem(it, name)
        else:
            self.getPlotItem().addItem(it)  # named items join the legend by themselves
        # only after the curve is in a view: draw at most a few points per pixel
        it.setDownsampling(auto=True, method="peak")
        it.setClipToView(True)
        return it

    def set_events(self, events, rng: Range) -> None:
        p = theme.current
        pi = self.getPlotItem()
        for line in self.events:
            pi.removeItem(line)
        self.events = []
        for e in events:
            t = event_type(e.text)
            if t not in ("ALARM_START", "MARK") or not (rng.t0 <= e.time_s <= rng.t1):
                continue
            pen = QPen(QColor(p.alarm_high if t == "ALARM_START" else p.text), 1)
            pen.setDashPattern([3, 3])
            pen.setCosmetic(True)
            c = pen.color()
            c.setAlphaF(0.6)
            pen.setColor(c)
            line = pg.InfiniteLine(e.time_s, angle=90, pen=pen)
            line.setToolTip(e.text)
            pi.addItem(line, ignoreBounds=True)
            self.events.append(line)

    def set_range(self, rng: Range) -> None:
        self.getPlotItem().setXRange(rng.t0, rng.t1, padding=0)
        for vb in (self.vb, self.vb2):
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
            vb.setAutoVisible(y=True)


class ReviewScreen(QFrame):
    def __init__(self, engine: MonitorEngine, sid: str, window) -> None:
        super().__init__()
        self.engine, self.sid, self.window = engine, sid, window
        self.setProperty("panel", True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 12, 18)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        outer.addWidget(scroll_page(self.body))
        self.rec = engine.library.get(sid)
        self.parsed: ParsedSession | None = None
        self.zoom: Range | None = None
        try:
            if self.rec is None:
                raise ValueError("This session is no longer in the library.")
            self.parsed = engine.library.parse(sid)
        except (OSError, ValueError) as e:
            self.lay.addLayout(hbox(button("← Back to data", window.close_review, kind="primary"), stretch_end=True))
            self.lay.addWidget(label(f"Could not open this session: {e}", "err", wrap=True))
            self.lay.addStretch(1)
            return
        self._build()

    @property
    def full(self) -> Range:
        return Range(0, max(1.0, self.parsed.duration_s))

    def _build(self) -> None:
        s, rec, lay = self.parsed, self.rec, self.lay
        summ = summarize(s)
        head = QVBoxLayout()
        title = label(rec.file_name, "h2", selectable=True)
        started = datetime.fromtimestamp(s.started_ms / 1000).strftime("%d %b %Y %H:%M:%S") if s.started_ms else "?"
        conv = " (exports are converted to the current format)" if s.version < 4 else ""
        head.addWidget(title)
        head.addWidget(
            label(
                f"Study {s.study_code or '—'} · started {started} · {s.meta.get('source', 'unknown source')} · firmware {s.meta.get('firmware', '?')} · file format v{s.version}{conv}",
                "note",
                wrap=True,
            )
        )
        lay.addLayout(hbox(button("← Back to data", self.window.close_review, kind="primary"), head, 1, spacing=16))
        stats = StatGrid(180)
        stats.set_all(
            [
                ("Length", duration(summ.duration_s * 1000)),
                ("Breaths", f"{summ.breaths - summ.rejected}" + (f" ({summ.rejected} noisy left out)" if summ.rejected else "")),
                ("EtCO2 mean (min–max)", f"{fmt(summ.etco2_mean, 1)} ({fmt(summ.etco2_min)}–{fmt(summ.etco2_max)}) mmHg"),
                ("RR mean", f"{fmt(summ.rr, 1)} /min"),
                ("VT mean", f"{fmt(summ.vt)} mL"),
                ("MV mean", f"{fmt(summ.mv, 2)} L/min"),
                ("Alarms", str(summ.alarms)),
                ("Markers", str(summ.markers)),
                ("Samples", f"{summ.samples:,} at {summ.rate_hz} Hz"),
                ("Dropped samples", str(summ.dropped)),
                *response_stats(session_response(s)),
            ]
        )
        lay.addWidget(stats)
        self.zoom_note = label("", "note")
        self.whole_btn = button("Show whole session", lambda: self.set_zoom(None), small=True)
        lay.addLayout(hbox(self.zoom_note, self.whole_btn, stretch_end=True))

        p = theme.current
        b = clean_breaths(s)  # breaths left out as noisy, artifact or outlier are not drawn
        bt = [x.time_s for x in b]
        lay.addWidget(label("ETCO2 AND RR PER BREATH", "h3"))
        self.p1 = DualPlot("EtCO2 mmHg", "RR /min", 300, self._on_zoom)
        self.p1.curve(bt, [x.etco2 for x in b], p.co2, name="EtCO2", points=True)
        self.p1.curve(bt, [x.rr for x in b], p.text, right=True, name="RR")
        lay.addWidget(self.p1)
        lay.addWidget(label("TIDAL VOLUME AND MINUTE VENTILATION", "h3"))
        self.p2 = DualPlot("VT mL", "MV L/min", 280, self._on_zoom)
        self.p2.curve(bt, [x.vt for x in b], p.pneumo, name="VT", points=True)
        self.p2.curve(bt, [x.mv for x in b], p.flow, right=True, name="MV")
        lay.addWidget(self.p2)
        self.wave_title = label("WAVEFORMS", "h3")
        lay.addWidget(self.wave_title)
        self.p3 = DualPlot("CO2 mmHg", "Flow L/min", 340, self._on_zoom)
        self.p3.curve(s.time_s, s.co2_mmhg, p.co2, name="CO2")
        self.p3.curve(s.time_s, s.flow_lpm, p.flow, right=True, name="Flow")
        self.p3.curve(s.time_s, s.pneumo_lpm, p.pneumo, right=True, name="Pneumotach")
        lay.addWidget(self.p3)
        self.plots = (self.p1, self.p2, self.p3)

        lay.addWidget(label("EXPORT", "h3"))
        self.range_btns = [
            button("Zoomed range, all data (.csv)", self._export_range),
            button("Zoomed range, breaths (.csv)", lambda: self._export_range_table("breaths")),
            button("Zoomed range, events (.csv)", lambda: self._export_range_table("events")),
        ]
        replay = button("Replay on the monitor", self._replay)
        replay.setEnabled(s.n > 0)
        lay.addLayout(
            hbox(
                button("Whole session (one .csv)", lambda: export_session(self.window, self.engine, self.rec, "session"), kind="primary"),
                button("Breaths (.csv)", lambda: export_session(self.window, self.engine, self.rec, "breaths")),
                button("Alarms & events (.csv)", lambda: export_session(self.window, self.engine, self.rec, "events")),
                button("CO2 response report (.pdf)", lambda: export_report(self.window, self.rec.file_name, self.parsed)),
                *self.range_btns,
                replay,
                stretch_end=True,
            )
        )
        lay.addWidget(label(f"ALARMS AND EVENTS ({len(s.events)})", "h3"))
        self.events = QListWidget()
        self.events.setMinimumHeight(220)
        for e in s.events:
            it = QListWidgetItem(f"{elapsed(e.time_s)}   {e.text}")
            it.setData(Qt.ItemDataRole.UserRole, e.time_s)
            it.setToolTip("Zoom to 30 s either side")
            if event_type(e.text).startswith("ALARM"):
                it.setForeground(QColor(p.alarm_med))
            self.events.addItem(it)
        if not s.events:
            self.events.addItem("No events.")
        self.events.itemClicked.connect(self._event_clicked)
        lay.addWidget(self.events)
        lay.addStretch(1)
        self.set_zoom(None)

    # ---------- zoom

    def _on_zoom(self, a: float, b: float) -> None:
        f = self.full
        self.set_zoom(Range(max(f.t0, a), min(f.t1, b)))

    def _event_clicked(self, it: QListWidgetItem) -> None:
        t = it.data(Qt.ItemDataRole.UserRole)
        if t is not None:
            self.set_zoom(Range(max(0, t - 30), min(self.full.t1, t + 30)))

    def set_zoom(self, rng: Range | None) -> None:
        self.zoom = rng
        r = rng or self.full
        for pl in self.plots:
            pl.set_range(r)
            pl.set_events(self.parsed.events, r)
        self.zoom_note.setText(f"Drag across a chart to zoom. Showing {elapsed(r.t0)}–{elapsed(r.t1)}." if rng else "Drag across a chart to zoom. Showing the whole session.")
        self.whole_btn.setEnabled(rng is not None)
        self.wave_title.setText("WAVEFORMS" if rng else "WAVEFORMS (ZOOM IN FOR FULL DETAIL)")
        for b in self.range_btns:
            b.setEnabled(rng is not None)

    def apply_theme(self) -> None:
        for pl in getattr(self, "plots", ()):
            pl.style()

    # ---------- exports

    def _base(self) -> str:
        n = self.rec.file_name
        tag = f"_{round(self.zoom.t0)}-{round(self.zoom.t1)}s" if self.zoom else ""
        return (n[:-4] if n.lower().endswith(".csv") else n) + tag

    def _export_range(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save zoomed range", str(downloads_dir() / f"{self._base()}.csv"), "CSV files (*.csv)")
        if path:
            write_session(self.parsed, path, self.zoom, __version__)
            self.window.toast(f"Saved {Path(path).name}.")

    def _export_range_table(self, what: str) -> None:
        text = breaths_csv(self.parsed, self.zoom) if what == "breaths" else events_csv(self.parsed, self.zoom)
        p = save_text(self, f"{self._base()}_{what}.csv", text)
        if p:
            self.window.toast(f"Saved {p.name}.")

    def _replay(self) -> None:
        self.engine.connect("replay", session=self.parsed, file_name=self.rec.file_name, speed=1, use_file_calibration=True)
        self.window.show_monitor()


