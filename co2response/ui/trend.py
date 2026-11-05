"""EtCO2 trend (one point per breath, limit band, markers, alarm marks) and the
ventilatory-response plot (MV against EtCO2 with a linear fit)."""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
)

from ..engine.monitor import MonitorEngine, Snapshot
from ..processing.co2 import mmhg_to_pct
from ..processing.metrics import linear_fit
from . import theme
from .theme import ui_font
from .widgets import fmt, set_combo


def style_plot(pw: pg.PlotWidget) -> None:
    p = theme.current
    pw.setBackground(p.panel)
    pi = pw.getPlotItem()
    pi.hideButtons()
    pi.setMenuEnabled(False)
    for name in ("left", "bottom", "right"):
        ax = pi.getAxis(name)
        ax.setPen(pg.mkPen(p.grid))
        ax.setTextPen(pg.mkPen(p.muted))
        ax.setStyle(tickFont=ui_font(12), tickLength=-4)
    pi.showGrid(x=True, y=True, alpha=0.25)


class TrendPanel(QFrame):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        self.setProperty("panel", True)
        self.setMinimumHeight(140)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(16)
        self.tabs = QButtonGroup(self)
        for i, text in enumerate(("ETCO2 TREND", "VENTILATORY RESPONSE")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("tab", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.tabs.addButton(b, i)
            head.addWidget(b)
        self.tabs.button(0).setChecked(True)
        self.tabs.idClicked.connect(self._tab)
        head.addStretch(1)
        self.meta = QLabel()
        self.meta.setProperty("role", "muted")
        self.meta.setStyleSheet("font-size:14px;")
        head.addWidget(self.meta)
        wl = QLabel("Window")
        wl.setProperty("role", "muted")
        head.addWidget(wl)
        self.window = QComboBox()
        for v, t in ((15, "15 min"), (30, "30 min"), (120, "2 h"), (0, "All")):
            self.window.addItem(t, v)
        self.window.setAccessibleName("Trend window")
        self.window.activated.connect(lambda i: engine.update_settings(trend_minutes=self.window.itemData(i)))
        head.addWidget(self.window)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        self.trend = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.resp = pg.PlotWidget()
        for pw in (self.trend, self.resp):
            pw.getPlotItem().setMouseEnabled(False, False)
            pw.getPlotItem().getViewBox().setMouseEnabled(False, False)
            self.stack.addWidget(pw)
        lay.addWidget(self.stack, 1)

        ti = self.trend.getPlotItem()
        self.high_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.low_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.high_line = pg.InfiniteLine(angle=0, movable=False)
        self.low_line = pg.InfiniteLine(angle=0, movable=False)
        self.trace = pg.PlotCurveItem()
        self.points = pg.ScatterPlotItem(size=5)
        self.alarm_marks = pg.ScatterPlotItem(symbol="t", size=12)
        for it in (self.high_band, self.low_band, self.trace, self.high_line, self.low_line, self.points, self.alarm_marks):
            ti.addItem(it)
        self.marker_lines: list[pg.InfiniteLine] = []
        ri = self.resp.getPlotItem()
        self.resp_points = pg.ScatterPlotItem(size=5)
        self.resp_fit = pg.PlotCurveItem()
        ri.addItem(self.resp_points)
        ri.addItem(self.resp_fit)
        ri.setLabel("bottom", "EtCO2 (mmHg)")
        ri.setLabel("left", "MV (L/min)")
        self._key = None
        self.apply_theme()

    def _tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._key = None

    def apply_theme(self) -> None:
        p = theme.current
        for pw in (self.trend, self.resp):
            style_plot(pw)
        for band, color in ((self.high_band, p.alarm_high), (self.low_band, p.alarm_med)):
            c = QColor(color)
            c.setAlphaF(0.1)
            band.setBrush(c)
            for line in band.lines:
                line.setPen(pg.mkPen(None))
        for line, color in ((self.high_line, p.alarm_high), (self.low_line, p.alarm_med)):
            pen = QPen(QColor(color), 2)
            pen.setCosmetic(True)
            pen.setDashPattern([4, 3])
            line.setPen(pen)
        c = QColor(p.co2)
        c.setAlphaF(0.3)
        self.trace.setPen(pg.mkPen(c, width=1))
        self.points.setBrush(pg.mkBrush(p.co2))
        self.points.setPen(pg.mkPen(p.co2))
        self.resp_points.setBrush(pg.mkBrush(p.pneumo))
        self.resp_points.setPen(pg.mkPen(p.pneumo))
        self.resp_fit.setPen(pg.mkPen(p.text, width=1.5))
        for pw in (self.resp,):
            for ax in ("left", "bottom"):
                pw.getPlotItem().getAxis(ax).label.setDefaultTextColor(QColor(p.muted))
        self._key = None

    def refresh(self, s: Snapshot) -> None:
        st = s.settings
        set_combo(self.window, st.trend_minutes)
        key = (int(s.now // 1000), s.trend_version, self.stack.currentIndex(), st.trend_minutes, st.co2_unit, st.alarms.high_mm_hg, st.alarms.low_mm_hg, st.co2_scale_max_mm_hg, theme.current.name)
        if key == self._key:
            return
        self._key = key
        e = self.engine
        pb, wv = st.processing.barometric_mm_hg, st.processing.water_vapor
        pct = st.co2_unit == "%"

        def disp(mm):
            return mmhg_to_pct(mm, pb, wv) if pct else mm

        now_s = s.now / 1000
        minutes = st.trend_minutes
        t0 = now_s - minutes * 60 if minutes > 0 else (e.trend[0].t if e.trend else now_s - 60)
        window = [q for q in e.trend if q.t >= t0]
        # noise removal: breaths left out as noisy, artifact or outlier are not drawn
        pts = [q for q in window if q.quality == "ok"]
        left_out = len(window) - len(pts)
        removed = f" · {left_out} noisy breath{'s' if left_out != 1 else ''} left out" if left_out else ""

        if self.stack.currentIndex() == 0:
            p = theme.current
            ymax = disp(st.co2_scale_max_mm_hg)
            pi = self.trend.getPlotItem()
            pi.setXRange(t0, now_s, padding=0)
            pi.setYRange(0, ymax, padding=0)
            hi = disp(st.alarms.high_mm_hg)
            self.high_band.setRegion((hi, max(hi, ymax * 2)))
            self.high_line.setValue(hi)
            low = st.alarms.low_mm_hg
            self.low_band.setVisible(low is not None)
            self.low_line.setVisible(low is not None)
            if low is not None:
                self.low_band.setRegion((-ymax, disp(low)))
                self.low_line.setValue(disp(low))
            # continuous CO2 behind the breath points, broken where data stopped
            tt = np.asarray(e.trace_t)
            vv = np.asarray(e.trace_v)
            keep = tt >= t0
            tt, vv = tt[keep], vv[keep]
            if len(tt) > 1:
                connect = np.append(np.diff(tt) <= 5, False)
                self.trace.setData(tt, np.asarray([disp(v) for v in vv]) if pct else vv, connect=connect)
            else:
                self.trace.setData([], [])
            xs = [q.t for q in pts if math.isfinite(q.etco2)]
            ys = [disp(q.etco2) for q in pts if math.isfinite(q.etco2)]
            self.points.setData(xs, ys)
            # alarm marks along the bottom, event markers as dashed lines
            al = [(a[0] / 1000, a[1]) for a in e.alarm_starts if a[0] / 1000 >= t0]
            self.alarm_marks.setData(
                [a[0] for a in al],
                [ymax * 0.05] * len(al),
                brush=[pg.mkBrush(p.alarm_high if a[1] == "high" else p.alarm_med) for a in al],
                pen=[pg.mkPen(None)] * len(al),
            )
            for line in self.marker_lines:
                pi.removeItem(line)
            self.marker_lines = []
            pen = QPen(QColor(p.text), 1)
            pen.setDashPattern([3, 3])
            pen.setCosmetic(True)
            for m in e.markers:
                if m.t / 1000 < t0:
                    continue
                line = pg.InfiniteLine(m.t / 1000, angle=90, pen=pen, label=f"▼ {m.label}", labelOpts={"position": 0.92, "color": p.text, "anchors": [(0, 0.5), (0, 0.5)]})
                pi.addItem(line)
                self.marker_lines.append(line)
            span = f"{minutes:g} min" if minutes > 0 else "whole session"
            self.meta.setText(f"{span} · one point per breath · {len(pts)} breaths{removed}")
        else:
            xy = sorted(((q.etco2, q.mv) for q in pts if math.isfinite(q.etco2) and math.isfinite(q.mv)), key=lambda a: a[0])
            xs = [a[0] for a in xy]
            ys = [a[1] for a in xy]
            fit = linear_fit(xs, ys)
            self.resp_points.setData(xs, ys)
            pi = self.resp.getPlotItem()
            if fit:
                self.resp_fit.setData(xs, [fit.slope * x + fit.intercept for x in xs])
                self.meta.setText(f"MV (L/min) vs EtCO2 (mmHg) · slope {fmt(fit.slope, 2)} L/min/mmHg · r² {fmt(fit.r2, 2)} · n {len(xs)}{removed}")
            else:
                self.resp_fit.setData([], [])
                self.meta.setText("MV vs EtCO2 · needs at least 3 breaths")
            lo = math.floor((min(xs) if xs else 30) - 2)
            hi = math.ceil((max(xs) if xs else 50) + 2)
            pi.setXRange(lo, hi, padding=0)
            pi.setYRange(0, max(10, math.ceil((max(ys) if ys else 10) * 1.2)), padding=0)
