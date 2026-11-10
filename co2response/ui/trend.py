"""The trend (EtCO2, MV or RR against time: one point per breath, limit bands, markers,
alarm marks), the ventilatory-response plot (each breath's MV against its EtCO2 with a
linear fit; click a point to leave it out) and the keys that run a rebreathing test."""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
)

from ..engine.monitor import MonitorEngine, Snapshot
from ..processing.co2 import mmhg_to_pct
from ..processing.metrics import co2_response_summary
from . import theme
from .monitor_widgets import expand_button
from .response_plot import ResponsePlot, style_plot
from .theme import ui_font
from .widgets import button, fmt, label, set_combo, set_prop

#: what the trend tab can plot against time: setting value → (tab text, axis label)
SERIES = {"etco2": ("ETCO2 TREND", "EtCO2"), "mv": ("MV TREND", "MV (L/min, 60 s average)"), "rr": ("RR TREND", "RR (/min)")}


class TrendPanel(QFrame):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        self.setProperty("panel", True)
        self.setMinimumHeight(150)
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
        self.meta.setStyleSheet("font-size:15px;")
        self.meta.setMinimumWidth(40)
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._meta_text = ""
        head.addWidget(self.meta, 1)
        self.series = QComboBox()
        for v, t in (("etco2", "EtCO2"), ("mv", "MV"), ("rr", "RR")):
            self.series.addItem(t, v)
        self.series.setAccessibleName("Trend of")
        self.series.setToolTip("What the trend tab plots against time")
        self.series.activated.connect(lambda i: engine.update_settings(trend_series=self.series.itemData(i)))
        head.addWidget(self.series)
        wl = QLabel("Window")
        wl.setProperty("role", "muted")
        head.addWidget(wl)
        self.window = QComboBox()
        for v, t in ((5, "5 min"), (15, "15 min"), (30, "30 min"), (120, "2 h"), (0, "All")):
            self.window.addItem(t, v)
        self.window.setAccessibleName("Trend window")
        self.window.activated.connect(lambda i: engine.update_settings(trend_minutes=self.window.itemData(i)))
        head.addWidget(self.window)
        #: enlarges the trend to fill the monitor; the monitor screen connects it
        self.expand = expand_button(self, "trend")
        head.addWidget(self.expand)
        lay.addLayout(head)

        # the rebreathing test: three keys and where the test stands
        test = QHBoxLayout()
        test.setSpacing(8)
        self.k_baseline = button("Start baseline", engine.start_baseline, small=True, tooltip="Breaths from now until rebreathing starts are the baseline")
        self.k_start = button("Start rebreathing", engine.start_rebreathing, small=True, tooltip="Press as the valve is turned to the bag")
        self.k_end = button("End test", engine.end_test, small=True, tooltip="Press as the valve is turned back to room air")
        self.k_emergency = button(
            "EMERGENCY STOP", engine.emergency_stop, small=True, kind="danger", tooltip="Ends the test now. With a valve output on the board, the valve goes to room air at once"
        )
        for b in (self.k_baseline, self.k_start, self.k_end, self.k_emergency):
            test.addWidget(b)
        self.test_text = label("", "muted")
        self.test_text.setStyleSheet("font-size:15px; font-weight:600;")
        self.test_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        test.addWidget(self.test_text, 1)
        lay.addLayout(test)

        self.stack = QStackedWidget()
        self.trend = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.resp = ResponsePlot(self._toggle_breath)
        self.trend.getPlotItem().setMouseEnabled(False, False)
        self.trend.getPlotItem().getViewBox().setMouseEnabled(False, False)
        for pw in (self.trend, self.resp):
            self.stack.addWidget(pw)
        lay.addWidget(self.stack, 1)

        ti = self.trend.getPlotItem()
        self.high_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.low_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.high_line = pg.InfiniteLine(angle=0, movable=False)
        self.stop_line = pg.InfiniteLine(angle=0, movable=False)
        self.low_line = pg.InfiniteLine(angle=0, movable=False)
        self.trace = pg.PlotCurveItem()
        #: joins the points of the MV and RR trends
        self.joined = pg.PlotCurveItem()
        self.points = pg.ScatterPlotItem(size=9)
        self.alarm_marks = pg.ScatterPlotItem(symbol="t", size=17)
        for it in (self.high_band, self.low_band, self.trace, self.joined, self.high_line, self.stop_line, self.low_line, self.points, self.alarm_marks):
            ti.addItem(it)
        self.marker_lines: list[pg.InfiniteLine] = []
        # the response plot's items, by the names the rest of the panel uses
        self.resp_points, self.resp_base, self.resp_skip, self.resp_out, self.resp_fit = self.resp.fitted, self.resp.base, self.resp.skip, self.resp.out, self.resp.fit
        self._key = None
        self.apply_theme()

    def set_meta(self, text: str) -> None:
        """The caption, cut short with an ellipsis when it does not fit (full text in the tooltip)."""
        self._meta_text = text
        self.meta.setToolTip(text)
        self.meta.setText(self.meta.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(40, self.meta.width())))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self._meta_text:
            self.set_meta(self._meta_text)

    def _tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._key = None

    def _toggle_breath(self, key) -> None:
        """A click on a breath in the response plot takes it out of the fit, or puts it back."""
        if self.engine.toggle_breath(int(key)):
            self._key = None

    def _point_clicked(self, item, points, ev=None) -> None:
        self.resp.point_clicked(item, points, ev)

    def apply_theme(self) -> None:
        p = theme.current
        style_plot(self.trend)
        self.resp.apply_theme()
        for band, color in ((self.high_band, p.alarm_high), (self.low_band, p.alarm_med)):
            c = QColor(color)
            c.setAlphaF(0.1)
            band.setBrush(c)
            for line in band.lines:
                line.setPen(pg.mkPen(None))
        for line, color in ((self.high_line, p.alarm_high), (self.low_line, p.alarm_med)):
            pen = QPen(QColor(color), 2.5)
            pen.setCosmetic(True)
            pen.setDashPattern([4, 3])
            line.setPen(pen)
        self.stop_line.setPen(pg.mkPen(p.alarm_high, width=2.5))  # the stop limit: a solid line
        c = QColor(p.co2)
        c.setAlphaF(0.3)
        self.trace.setPen(pg.mkPen(c, width=1.5))
        self.points.setBrush(pg.mkBrush(p.co2))
        self.points.setPen(pg.mkPen(p.co2))
        self._key = None

    def _refresh_test(self, s: Snapshot) -> None:
        ts = s.test
        for b, on, active in ((self.k_baseline, ts.can_baseline, ts.phase == "baseline"), (self.k_start, ts.can_start, ts.phase == "rebreathing"), (self.k_end, ts.can_end, False)):
            if b.isEnabled() != on:
                b.setEnabled(on)
            set_prop(b, "kind", "primary" if active else "")
        if self.k_emergency.isEnabled() != ts.can_emergency:
            self.k_emergency.setEnabled(ts.can_emergency)
        if self.test_text.toolTip() != ts.text:
            self.test_text.setToolTip(ts.text)
        self.test_text.setText(self.test_text.fontMetrics().elidedText(ts.text, Qt.TextElideMode.ElideRight, max(40, self.test_text.width())))

    def refresh(self, s: Snapshot) -> None:
        st = s.settings
        set_combo(self.window, st.trend_minutes)
        set_combo(self.series, st.trend_series)
        self._refresh_test(s)
        series = st.trend_series if st.trend_series in SERIES else "etco2"
        on_trend = self.stack.currentIndex() == 0
        if self.series.isVisible() != on_trend:
            self.series.setVisible(on_trend)
        if self.tabs.button(0).text() != SERIES[series][0]:
            self.tabs.button(0).setText(SERIES[series][0])
        key = (
            int(s.now // 1000), s.trend_version, self.stack.currentIndex(), st.trend_minutes, st.co2_unit, st.alarms.high_mm_hg, st.alarms.low_mm_hg,
            st.alarms.stop_mm_hg, st.co2_scale_max_mm_hg, series, theme.current.name,
        )  # fmt: skip
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

        if on_trend:
            p = theme.current
            pi = self.trend.getPlotItem()
            pi.setXRange(t0, now_s, padding=0)
            co2 = series == "etco2"
            for it in (self.high_band, self.high_line, self.trace):
                it.setVisible(co2)
            self.joined.setVisible(not co2)
            low = st.alarms.low_mm_hg if co2 else None
            stop = st.alarms.stop_mm_hg if co2 else None
            self.low_band.setVisible(low is not None)
            self.low_line.setVisible(low is not None)
            self.stop_line.setVisible(stop is not None)
            if co2:
                ymax = disp(st.co2_scale_max_mm_hg)
                if stop is not None:
                    ymax = max(ymax, disp(stop) * 1.06)  # the stop limit is always on the plot
                hi = disp(st.alarms.high_mm_hg)
                self.high_band.setRegion((hi, max(hi, ymax * 2)))
                self.high_line.setValue(hi)
                if stop is not None:
                    self.stop_line.setValue(disp(stop))
                if low is not None:
                    self.low_band.setRegion((-ymax, disp(low)))
                    self.low_line.setValue(disp(low))
                # continuous CO2 behind the breath points, without the stretches of rejected
                # breaths, broken there and where data stopped
                tt, vv, join = e.trend_trace(t0)
                if len(tt) > 1:
                    self.trace.setData(np.asarray(tt), np.asarray([disp(v) for v in vv]), connect=np.asarray(join))
                else:
                    self.trace.setData([], [])
                fin = [(q.t, disp(q.etco2)) for q in pts if math.isfinite(q.etco2)]
                color = p.co2
            else:
                fin = [(q.t, q.mv if series == "mv" else q.rr) for q in pts if math.isfinite(q.mv if series == "mv" else q.rr)]
                ymax = max(10.0, math.ceil(max((v for _, v in fin), default=10) * 1.2))
                color = p.pneumo if series == "mv" else p.text
                self.joined.setPen(pg.mkPen(color, width=2))
                self.joined.setData([a for a, _ in fin], [b for _, b in fin])
            pi.setYRange(0, ymax, padding=0)
            self.points.setBrush(pg.mkBrush(color))
            self.points.setPen(pg.mkPen(color))
            self.points.setData([a for a, _ in fin], [b for _, b in fin])
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
            pen = QPen(QColor(p.text), 1.5)
            pen.setDashPattern([3, 3])
            pen.setCosmetic(True)
            for m in e.markers:
                if m.t / 1000 < t0:
                    continue
                line = pg.InfiniteLine(m.t / 1000, angle=90, pen=pen, label=f"▼ {m.label}", labelOpts={"position": 0.92, "color": p.text, "anchors": [(0, 0.5), (0, 0.5)]})
                line.label.setFont(ui_font(14, QFont.Weight.DemiBold))
                pi.addItem(line)
                self.marker_lines.append(line)
            span = f"{minutes:g} min" if minutes > 0 else "whole session"
            self.set_meta(f"{SERIES[series][1]} · {span} · one point per breath · {len(fin)} breaths{removed}")
        else:
            # Read's method: baseline breaths averaged, the line fitted to rebreathing only
            res = e.co2_response(t0)
            self.resp.set_response(res)
            hint = " · click a point to leave it out" if res.baseline or res.rebreathing else ""
            self.set_meta(co2_response_summary(res, fmt) + (removed if res.fit else "") + hint)
