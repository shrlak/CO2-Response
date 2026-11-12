"""The ventilatory-response plot: each breath's own MV against its EtCO2.

Fitted breaths are filled, the baseline is hollow rings, breaths outside the fit are
faint rings, and breaths taken out by hand are crosses. A click on a breath calls
``on_click(key)``. The monitor's trend panel and the review screen both use it.
"""

from __future__ import annotations

from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

from ..processing.metrics import CO2Response, lowess_curve, response_axes, response_labels
from . import theme
from .theme import ui_font


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
        ax.setStyle(tickFont=ui_font(15, QFont.Weight.DemiBold), tickLength=-5)
    pi.showGrid(x=True, y=True, alpha=0.35)


class ResponsePlot(pg.PlotWidget):
    def __init__(self, on_click: Callable[[object], None] | None = None) -> None:
        super().__init__()
        self.on_click = on_click
        pi = self.getPlotItem()
        pi.setMouseEnabled(False, False)
        pi.getViewBox().setMouseEnabled(False, False)
        self.fitted = pg.ScatterPlotItem(size=10)
        # baseline breaths (before the rebreathing marker): hollow, not fitted
        self.base = pg.ScatterPlotItem(size=11)
        # the first seconds of rebreathing and the breaths after the end: faint, not fitted
        self.skip = pg.ScatterPlotItem(size=7)
        # breaths taken out by hand: crosses
        self.out = pg.ScatterPlotItem(size=12, symbol="x")
        self.fit = pg.PlotCurveItem()
        # the curve (LOWESS) through the rebreathing breaths; the straight least-squares
        # line the slope comes from stays, dashed
        self.curve = pg.PlotCurveItem()
        for it in (self.skip, self.base, self.fitted, self.out, self.fit, self.curve):
            pi.addItem(it)
        for it in (self.base, self.fitted, self.out):
            it.sigClicked.connect(self.point_clicked)
        pi.setLabel("bottom", "etCO2 (mmHg)")
        pi.setLabel("left", "MV (liters/min)")
        # the labels of the lab's figures: the slope and r, and the baseline average
        self.fit_label = pg.TextItem(anchor=(0, 0))
        self.base_label = pg.TextItem(anchor=(0, 1))
        for it in (self.fit_label, self.base_label):
            pi.addItem(it, ignoreBounds=True)
        self.apply_theme()

    def point_clicked(self, _item, points, _ev=None) -> None:
        for pt in points[:1]:
            if pt.data() is not None and self.on_click is not None:
                self.on_click(pt.data())

    def apply_theme(self) -> None:
        p = theme.current
        style_plot(self)
        self.fitted.setBrush(pg.mkBrush(p.pneumo))
        self.fitted.setPen(pg.mkPen(p.pneumo))
        self.base.setBrush(pg.mkBrush(None))
        self.base.setPen(pg.mkPen(p.muted, width=2))
        self.skip.setBrush(pg.mkBrush(None))
        faint = QColor(p.muted)
        faint.setAlphaF(0.55)
        self.skip.setPen(pg.mkPen(faint, width=1.5))
        self.out.setBrush(pg.mkBrush(p.alarm_high))
        self.out.setPen(pg.mkPen(p.alarm_high))
        self.fit.setPen(pg.mkPen(p.muted, width=1.5, style=Qt.PenStyle.DashLine))
        self.curve.setPen(pg.mkPen(p.text, width=3))

        for it in (self.fit_label, self.base_label):
            it.setColor(QColor(p.text))
            it.setFont(ui_font(15, QFont.Weight.DemiBold))
        for ax in ("left", "bottom"):
            lab = self.getPlotItem().getAxis(ax).label
            lab.setDefaultTextColor(QColor(p.muted))
            lab.setFont(ui_font(14, QFont.Weight.DemiBold))

    def set_response(self, res: CO2Response) -> None:
        def put(item, pts) -> None:
            item.setData(x=[q.etco2 for q in pts], y=[q.mv for q in pts], data=[q.key for q in pts])

        put(self.fitted, res.rebreathing)
        put(self.base, res.baseline)
        put(self.skip, res.skipped)
        put(self.out, res.excluded)
        if res.fit:
            rx = [q.etco2 for q in res.rebreathing]
            ends = [min(rx), max(rx)]
            self.fit.setData(ends, [res.fit.slope * x + res.fit.intercept for x in ends])
            cx, cy = lowess_curve(rx, [q.mv for q in res.rebreathing])
            self.curve.setData(cx, cy)
        else:
            self.fit.setData([], [])
            self.curve.setData([], [])

        pi = self.getPlotItem()
        x0, x1, y0, y1 = response_axes(res)  # both axes scale to the breaths
        pi.setXRange(x0, x1, padding=0)
        pi.setYRange(y0, y1, padding=0)
        fit_text, base_text = response_labels(res)
        self.fit_label.setText(fit_text.replace(" · ", "\n"))
        self.fit_label.setPos(x0 + 0.03 * (x1 - x0), y1 * 0.97)
        self.base_label.setText(base_text.replace(" · ", "\n"))
        if res.baseline:
            self.base_label.setPos(min(q.etco2 for q in res.baseline) - 0.5, min(y1 * 0.9, max(q.mv for q in res.baseline) + 0.08 * y1))
