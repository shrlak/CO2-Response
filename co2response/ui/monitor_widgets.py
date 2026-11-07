"""Custom-painted monitor widgets: the sweeping waveform and the large numeric tile."""

from __future__ import annotations

import math

import numpy as np
from pyqtgraph.functions import arrayToQPath
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from . import theme
from .theme import ui_font

TOP, BOTTOM, RIGHT_GUTTER = 32, 10, 44
TITLE_PX, TICK_PX = 18, 15


def expand_button(parent: QWidget, what: str) -> QPushButton:
    """The small button that enlarges a graph to fill the monitor (and back)."""
    b = QPushButton("\u2922", parent)
    b.setProperty("expand", True)
    b.setCheckable(True)
    b.setFixedSize(30, 24)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(f"Enlarge the {what} graph to fill the screen (click again, or Esc, to go back). Double-click the graph does the same.")
    b.setAccessibleName(f"Enlarge the {what} graph")
    b.toggled.connect(lambda on: b.setText("\u2921" if on else "\u2922"))
    return b


def _fmt_tick(t: float, signed: bool) -> str:
    s = f"{t:g}"
    return f"+{s}" if signed and t > 0 else s


class SweepWidget(QWidget):
    """Monitor-style sweep: the trace is drawn left to right and wraps, with an erase
    gap ahead of the pen, so old data does not move.

    Like a real monitor it only draws what is new: each frame adds the latest line
    segments to a persistent trace layer and repaints just that strip. The static
    background (grid, scale, limit) is cached. Everything is redrawn from the sample
    buffer only when the size, scale, colors or sweep speed change.
    """

    GAP = 18  # px erased ahead of the pen
    LW = 3.0

    def __init__(self, cid: str, label: str, unit: str) -> None:
        super().__init__()
        self.cid = cid
        self.label = label
        self.unit = unit
        self.vmin, self.vmax = 0.0, 1.0
        self.ticks: list[float] = []
        self.signed = False
        self.limit: float | None = None
        self.seconds = 12.0
        self.rate = 100.0
        self.tag: str | None = None
        self.in_alarm = False
        self._n = 1200
        self._buf = np.full(self._n, np.nan)
        self._pos = 0
        self._bg: QPixmap | None = None
        self._layer: QImage | None = None
        self._last: tuple[float, float] | None = None
        self._key = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(60)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAccessibleName(f"{label} waveform")
        #: called with no arguments when the graph is asked to enlarge (button or double-click)
        self.on_expand = None
        self.expand = expand_button(self, label)
        self.expand.clicked.connect(lambda _=False: self.on_expand and self.on_expand())
        self._place_expand()

    def _place_expand(self) -> None:
        f = ui_font(TITLE_PX, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        x = 10 + QFontMetrics(f).horizontalAdvance(self.label.upper()) + 8 + QFontMetrics(ui_font(TITLE_PX - 2)).horizontalAdvance(self.unit) + 12
        if self.expand.x() != x:
            self.expand.move(x, 5)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.on_expand:
            self.on_expand()

    # ---------- options

    def set_options(self, vmin: float, vmax: float, ticks: list[float], signed: bool, limit: float | None, seconds: float, rate: float) -> None:
        n = max(10, round(seconds * rate))
        if n != self._n:
            self._n = n
            self.clear()
        self.vmin, self.vmax, self.ticks, self.signed, self.limit = vmin, vmax, list(ticks), signed, limit
        self.seconds, self.rate = seconds, rate
        self._place_expand()
        if self._cache_key() != self._key:
            self.update()

    def set_tag(self, tag: str | None, in_alarm: bool) -> None:
        if (tag, in_alarm) != (self.tag, self.in_alarm):
            self.tag, self.in_alarm = tag, in_alarm
            self._key = None  # the tag and border live in the cached background
            self.update()

    def clear(self) -> None:
        self._buf = np.full(self._n, np.nan)
        self._pos = 0
        self._last = None
        self._key = None
        self.update()

    def _cache_key(self):
        return (self.width(), self.height(), self.devicePixelRatioF(), theme.current.name, self.vmin, self.vmax, tuple(self.ticks), self.signed, self.limit, self._n, self.unit, self.tag, self.in_alarm)

    # ---------- geometry

    def _y(self, v: float) -> float:
        h = self.height()
        c = min(self.vmax, max(self.vmin, v))
        return h - BOTTOM - (c - self.vmin) / (self.vmax - self.vmin) * (h - TOP - BOTTOM)

    def _pen(self) -> QPen:
        pen = QPen(QColor(theme.current.channel(self.cid)), self.LW)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    # ---------- new samples

    def push(self, values: list[float]) -> None:
        if not values:
            return
        if self._layer is None or self._cache_key() != self._key:
            # the layers will be rebuilt from the buffer at the next paint
            v = np.asarray(values[-self._n :], dtype=float)
            self._buf[(self._pos + np.arange(len(v))) % self._n] = v
            self._pos = (self._pos + len(values)) % self._n
            self._last = None
            self.update()
            return
        n, w, h = self._n, self.width(), self.height()
        lw, gap = self.LW, self.GAP
        g = QPainter(self._layer)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        g.setPen(self._pen())
        x_first = self._pos / n * w
        wrapped = False
        for v in values:
            i = self._pos
            x = i / n * w
            if i == 0:
                self._last = None
                wrapped = True
            # erase just ahead of the pen
            g.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            g.fillRect(QRectF(x + lw, 0, gap, h), Qt.GlobalColor.transparent)
            if x + lw + gap > w:
                g.fillRect(QRectF(0, 0, x + lw + gap - w, h), Qt.GlobalColor.transparent)
            g.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._buf[i] = v
            self._pos = (i + 1) % n
            if not math.isfinite(v):
                self._last = None
                continue
            y = self._y(v)
            if self._last is not None:
                g.drawLine(QPointF(*self._last), QPointF(x, y))
            self._last = (x, y)
        g.end()
        x_end = self._pos / n * w
        if wrapped or x_end < x_first:
            self.update()
        else:
            self.update(QRect(int(x_first - lw - 2), 0, int(x_end - x_first + lw + gap + 6), h))

    # ---------- painting

    def _rebuild(self) -> None:
        p = theme.current
        w, h = max(1, self.width()), max(1, self.height())
        dpr = self.devicePixelRatioF()
        self._key = self._cache_key()

        bg = QPixmap(int(w * dpr), int(h * dpr))
        bg.setDevicePixelRatio(dpr)
        bg.fill(QColor(p.bg))
        g = QPainter(bg)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        g.setPen(Qt.PenStyle.NoPen)
        g.setBrush(p.q("wave_bg"))
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        g.drawRoundedRect(rect, 4, 4)
        right = w - RIGHT_GUTTER
        g.setFont(ui_font(TICK_PX, QFont.Weight.DemiBold))
        fm = QFontMetrics(g.font())
        for t in self.ticks:
            y = int(round(self._y(t)))
            g.setPen(QPen(p.q("grid"), 1))
            g.drawLine(0, y, right, y)
            g.setPen(p.q("muted"))
            s = _fmt_tick(t, self.signed)
            g.drawText(int(w - 6 - fm.horizontalAdvance(s)), int(y + fm.ascent() / 2 - 1), s)
        if self.limit is not None and self.vmin < self.limit < self.vmax:
            y = int(round(self._y(self.limit)))
            pen = QPen(p.q("alarm_high"), 2.5)
            pen.setDashPattern([4, 3])
            g.setPen(pen)
            g.drawLine(0, y, right, y)
            g.setPen(p.q("alarm_high"))
            s = f"{round(self.limit, 1):g}"
            g.drawText(int(w - 6 - fm.horizontalAdvance(s)), int(y + fm.ascent() / 2 - 1), s)
        f = ui_font(TITLE_PX, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        g.setFont(f)
        g.setPen(QColor(p.channel(self.cid)))
        title = self.label.upper()
        g.drawText(10, 23, title)
        x = 10 + QFontMetrics(f).horizontalAdvance(title) + 8
        g.setFont(ui_font(TITLE_PX - 2))
        g.setPen(p.q("muted"))
        g.drawText(x, 23, self.unit)
        if self.tag:
            tf = ui_font(13, QFont.Weight.Bold)
            tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
            g.setFont(tf)
            tw = QFontMetrics(tf).horizontalAdvance(self.tag) + 12
            r = QRectF(w - RIGHT_GUTTER - 12 - tw, 7, tw, 20)
            g.setPen(QPen(p.q("muted"), 1))
            g.setBrush(Qt.BrushStyle.NoBrush)
            g.drawRoundedRect(r, 3, 3)
            g.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tag)
        g.setBrush(Qt.BrushStyle.NoBrush)
        if self.in_alarm:
            g.setPen(QPen(p.q("alarm_high"), 2))
            g.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 4, 4)
        else:
            g.setPen(QPen(p.q("border"), 1))
            g.drawRoundedRect(rect, 4, 4)
        g.end()
        self._bg = bg

        # the whole trace from the buffer, with the erase gap ahead of the pen
        layer = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        n = self._n
        ys = self._buf.copy()
        gap = math.ceil((self.GAP + self.LW) / w * n) + 1
        ys[(self._pos + np.arange(gap)) % n] = np.nan
        finite = np.isfinite(ys)
        if finite.any():
            xs = np.arange(n) / n * w
            yy = h - BOTTOM - (np.clip(np.where(finite, ys, self.vmin), self.vmin, self.vmax) - self.vmin) / (self.vmax - self.vmin) * (h - TOP - BOTTOM)
            connect = finite & np.roll(finite, -1)  # a segment joins i to i+1 only when both are real
            connect[-1] = False
            g = QPainter(layer)
            g.setRenderHint(QPainter.RenderHint.Antialiasing)
            g.setPen(self._pen())
            g.drawPath(arrayToQPath(xs, yy, connect=connect))
            g.end()
        self._layer = layer
        last = (self._pos - 1) % n
        self._last = (last / n * w, self._y(float(self._buf[last]))) if math.isfinite(self._buf[last]) and self._pos != 0 else None

    def paintEvent(self, e) -> None:  # noqa: N802
        if self._layer is None or self._cache_key() != self._key:
            self._rebuild()
        r = e.rect()
        g = QPainter(self)
        g.drawPixmap(QRectF(r), self._bg, self._src(r))
        g.drawImage(QRectF(r), self._layer, self._src(r))
        g.end()

    def _src(self, r: QRect) -> QRectF:
        d = self.devicePixelRatioF()
        return QRectF(r.x() * d, r.y() * d, r.width() * d, r.height() * d)


class NumericTile(QWidget):
    """A large number with its label, unit, alarm limits and a qualifier line."""

    def __init__(self, cid: str, label: str, unit: str, big: bool = False) -> None:
        super().__init__()
        self.cid = cid
        self.label = label
        self.unit = unit
        self.big = big
        self.value = "--"
        self.stale = False
        self.alarm: str | None = None  # 'high' | 'medium'
        self.flash = False
        self.limits: tuple[str, str] | None = None
        self.qualifier = ""
        self.raw = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(120, 70)
        self.setAccessibleName(label)

    def set(self, value: str, unit: str | None = None, stale: bool = False, alarm: str | None = None, flash: bool = False, limits=None, qualifier: str = "", raw: str = "") -> None:
        state = (value, unit or self.unit, stale, alarm, flash, limits, qualifier, raw)
        if state != (self.value, self.unit, self.stale, self.alarm, self.flash, self.limits, self.qualifier, self.raw):
            self.value, self.unit, self.stale, self.alarm, self.flash, self.limits, self.qualifier, self.raw = state
            self.setAccessibleDescription(f"{self.label} {value} {self.unit}")
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.current
        w, h = self.width(), self.height()
        g = QPainter(self)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        g.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bg = p.alarm_high if self.alarm == "high" else p.alarm_med if self.alarm == "medium" else p.panel
        on = p.on_alarm if self.alarm == "high" else p.on_med if self.alarm == "medium" else None
        g.setPen(Qt.PenStyle.NoPen)
        g.setBrush(QColor(bg))
        g.drawRoundedRect(QRectF(0, 0, w, h), 4, 4)

        color = QColor(on or p.channel(self.cid))
        muted = QColor(on or p.muted)
        text = QColor(on or p.text)

        # label + unit
        f = ui_font(14, QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        g.setFont(f)
        g.setPen(color)
        title = self.label.upper()
        g.drawText(12, 21, title)
        x = 12 + QFontMetrics(f).horizontalAdvance(title) + 6
        g.setFont(ui_font(14))
        g.setPen(muted)
        g.drawText(x, 21, self.unit)
        if self.limits:
            g.setFont(ui_font(13))
            fm = QFontMetrics(g.font())
            hi, lo = self.limits
            g.setPen(text)
            g.drawText(w - 12 - fm.horizontalAdvance(hi), 20, hi)
            g.setPen(muted)
            g.drawText(w - 12 - fm.horizontalAdvance(lo), 36, lo)

        # qualifier line
        bottom = h
        if self.qualifier or self.raw:
            g.setFont(ui_font(13))
            fm = QFontMetrics(g.font())
            g.setPen(muted)
            g.drawText(QRectF(12, h - 22, w - 24, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fm.elidedText(self.qualifier, Qt.TextElideMode.ElideRight, w - (130 if self.raw else 24)))
            if self.raw:
                g.setFont(theme.mono_font(12))
                g.drawText(QRectF(12, h - 22, w - 24, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.raw)
            bottom = h - 22

        # the number, as large as fits
        area = QRectF(12, 26, w - 24, max(10, bottom - 28))
        target = min(area.height() * (0.92 if self.big else 0.85), 150 if self.big else 72)
        vf = ui_font(max(12, int(target)), QFont.Weight.DemiBold)
        fm = QFontMetrics(vf)
        while fm.horizontalAdvance(self.value) > area.width() and vf.pixelSize() > 12:
            vf.setPixelSize(int(vf.pixelSize() * 0.9))
            fm = QFontMetrics(vf)
        g.setFont(vf)
        vc = QColor(muted if self.stale and not self.alarm else (on or p.channel(self.cid)))
        if self.flash:
            vc.setAlphaF(0.3)
        g.setPen(vc)
        align = (Qt.AlignmentFlag.AlignCenter if self.big else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter
        g.drawText(area, align, self.value)
        g.end()
