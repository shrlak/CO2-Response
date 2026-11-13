"""The terminal monitor drawn as real pictures, like the browser page: anti-aliased traces,
the browser's colours and its Barlow typeface. Used in terminals that can show images
(Sixel: iTerm2, VS Code with images on, WezTerm, Windows Terminal; Kitty's graphics
protocol); elsewhere the monitor stays in text (``canvas.py``).

Every function returns a ``PIL.Image`` of the requested size in pixels. Drawing happens
at twice the size and is scaled down, which smooths lines and the edges of the digits.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .canvas import SweepBuffer, nice_ticks

SS = 2  # supersampling

#: the browser's dark theme (site/styles.css)
BG = (0, 0, 0)
PANEL = (11, 15, 20)
GRID = (42, 54, 67)
BORDER = (38, 49, 61)
TEXT = (230, 235, 240)
MUTED = (134, 147, 161)
CO2 = (255, 214, 10)
FLOW = (56, 217, 245)
PNEUMO = (91, 227, 125)
ALARM_HIGH = (255, 59, 48)
ALARM_MED = (255, 159, 10)
COLORS = {"co2": CO2, "flow": FLOW, "pneumo": PNEUMO, "text": TEXT, "muted": MUTED, "high": ALARM_HIGH, "medium": ALARM_MED}

FONTS = Path(__file__).resolve().parent.parent / "fonts"


@lru_cache(maxsize=64)
def font(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONTS / f"BarlowSemiCondensed-{weight}.ttf"), max(6, size))
    except OSError:
        return ImageFont.load_default()


class Pic:
    """A picture drawn at ``SS`` times its size."""

    def __init__(self, w: int, h: int, bg=BG) -> None:
        self.w, self.h = max(8, int(w)), max(8, int(h))
        self.im = Image.new("RGB", (self.w * SS, self.h * SS), bg)
        self.d = ImageDraw.Draw(self.im)

    def text(self, x: float, y: float, s: str, size: float, fill, weight: str = "SemiBold", anchor: str = "la") -> None:
        self.d.text((x * SS, y * SS), s, font=font(int(size * SS), weight), fill=fill, anchor=anchor)

    def text_w(self, s: str, size: float, weight: str = "SemiBold") -> float:
        return font(int(size * SS), weight).getlength(s) / SS

    def line(self, pts: Sequence[tuple[float, float]], fill, width: float = 1.0) -> None:
        if len(pts) > 1:
            self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=max(1, round(width * SS)), joint="curve")

    def dashed(self, x0: float, y0: float, x1: float, y1: float, fill, width: float = 1.0, on: float = 8, off: float = 6) -> None:
        length = math.hypot(x1 - x0, y1 - y0)
        if length <= 0:
            return
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        t = 0.0
        while t < length:
            e = min(length, t + on)
            self.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)], fill, width)
            t = e + off

    def dot(self, x: float, y: float, r: float, fill=None, outline=None, width: float = 1.0) -> None:
        self.d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=fill, outline=outline, width=max(1, round(width * SS)))

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill=None, outline=None, width: float = 1.0, radius: float = 0) -> None:
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=radius * SS, fill=fill, outline=outline, width=max(1, round(width * SS)))

    def done(self) -> Image.Image:
        return self.im.resize((self.w, self.h), Image.Resampling.LANCZOS)


def _fmt(v: float, signed: bool = False) -> str:
    s = f"{v:g}"
    return f"+{s}" if signed and v > 0 else s


# ------------------------------------------------------------------ the sweep


def wave(
    sweep: SweepBuffer, w: int, h: int, vmin: float, vmax: float, color, title: str, unit: str, ticks: Iterable[float] = (), signed: bool = False, limit: float | None = None, tag: str = "", in_alarm: bool = False
) -> Image.Image:
    """A sweeping waveform as the browser draws it: the trace written left to right with
    a gap ahead of the pen, the scale at the right, the warning limit dashed in red."""
    p = Pic(w, h)
    top, bottom, gutter = 34.0, 10.0, 46.0
    right = w - gutter
    title_px = max(13.0, min(20.0, h / 9))

    def y(v: float) -> float:
        c = min(vmax, max(vmin, v))
        return h - bottom - (c - vmin) / (vmax - vmin) * (h - top - bottom)

    for t in ticks:
        yy = y(t)
        p.line([(0, yy), (right, yy)], GRID, 1)
        p.text(w - 6, yy, _fmt(t, signed), title_px * 0.8, MUTED, anchor="rm")
    if limit is not None and vmin < limit < vmax:
        yy = y(limit)
        p.dashed(0, yy, right, yy, ALARM_HIGH, 2.2, 9, 6)
        p.text(w - 6, yy, _fmt(round(limit, 1)), title_px * 0.8, ALARM_HIGH, anchor="rm")
    # the trace from the buffer, broken at the erase gap and wherever data is missing
    n = sweep.n
    ys = sweep.buf.copy()
    gap = max(1, math.ceil((18 / max(1.0, right)) * n))
    ys[(sweep.pos + np.arange(gap)) % n] = np.nan
    cols = max(2, int(right))
    edges = (np.arange(cols + 1) * n) // cols
    seg: list[tuple[float, float]] = []
    for i in range(cols):
        chunk = ys[edges[i] : max(edges[i] + 1, edges[i + 1])]
        chunk = chunk[np.isfinite(chunk)]
        if not len(chunk):
            p.line(seg, color, 2.6)
            seg = []
            continue
        x = i * right / cols
        if len(chunk) > 2:  # several samples in one column: show their range
            lo, hi = float(chunk.min()), float(chunk.max())
            first = float(chunk[0])
            seg.append((x, y(first)))
            if hi - lo > (vmax - vmin) * 0.01:
                p.line([(x, y(lo)), (x, y(hi))], color, 2.6)
            seg.append((x, y(float(chunk[-1]))))
        else:
            for v in chunk:
                seg.append((x, y(float(v))))
    p.line(seg, color, 2.6)
    p.text(10, 8, title.upper(), title_px, color, "Bold")
    tw = p.text_w(title.upper(), title_px, "Bold")
    p.text(10 + tw + 8, 8 + title_px * 0.08, unit, title_px * 0.85, MUTED, "Medium")
    if tag:
        tw = p.text_w(tag, title_px * 0.7, "Bold")
        x0 = right - 12 - tw - 12
        p.rect(x0, 8, x0 + tw + 12, 8 + title_px * 1.1, outline=MUTED, width=1, radius=3)
        p.text(x0 + 6 + tw / 2, 8 + title_px * 0.55, tag, title_px * 0.7, MUTED, "Bold", anchor="mm")
    p.rect(0.5, 0.5, w - 1, h - 1, outline=ALARM_HIGH if in_alarm else BORDER, width=2 if in_alarm else 1, radius=4)
    return p.done()


# ------------------------------------------------------------------ a number


def tile(w: int, h: int, label: str, unit: str, value: str, color, note: str = "", limits: str = "", alarm=None) -> Image.Image:
    """One number the way the browser shows it: its name and unit at the top, the value
    as large as the tile allows (right-aligned), a note underneath."""
    p = Pic(w, h, PANEL)
    head = max(12.0, min(18.0, h / 9))
    p.text(12, 8, label.upper(), head, color, "Bold")
    lw = p.text_w(label.upper(), head, "Bold")
    p.text(12 + lw + 6, 8 + head * 0.06, unit, head * 0.92, MUTED, "Medium")
    if limits:
        p.text(w - 10, 8, limits, head * 0.9, MUTED, "Medium", anchor="ra")
    if note:
        p.text(12, h - 8, note, head * 0.85, MUTED, "Medium", anchor="ld")
    # the value: as tall as fits between the label and the note, and no wider than the tile
    room_h = h - (8 + head * 1.4) - ((head * 1.3 + 8) if note else 8)
    room_w = w - 24
    size = max(10.0, room_h * 1.25)
    while size > 10 and p.text_w(value, size, "Bold") > room_w:
        size *= 0.92
    cy = 8 + head * 1.4 + room_h / 2
    p.text(w - 14, cy, value, size, alarm or color, "Bold", anchor="rm")
    p.rect(0.5, 0.5, w - 1, h - 1, outline=alarm or BORDER, width=3 if alarm else 1, radius=4)
    return p.done()


# ------------------------------------------------------------------ plots


class Axes:
    def __init__(self, p: Pic, x0: float, x1: float, y0: float, y1: float, pad=(54.0, 16.0, 30.0, 34.0)) -> None:
        self.p = p
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.left, self.right, self.top, self.bottom = pad
        self.W = p.w - self.left - self.right
        self.H = p.h - self.top - self.bottom

    def px(self, x: float) -> float:
        return self.left + (x - self.x0) / ((self.x1 - self.x0) or 1) * self.W

    def py(self, y: float) -> float:
        return self.top + self.H - (y - self.y0) / ((self.y1 - self.y0) or 1) * self.H

    def frame(self, xticks: list[tuple[float, str]], yticks: list[float], xlabel: str = "", ylabel: str = "") -> None:
        p = self.p
        size = 14.0
        for t in yticks:
            yy = self.py(t)
            p.line([(self.left, yy), (self.left + self.W, yy)], GRID, 1)
            p.text(self.left - 8, yy, _fmt(t), size, MUTED, anchor="rm")
        for x, label in xticks:
            xx = self.px(x)
            p.line([(xx, self.top), (xx, self.top + self.H)], GRID, 1)
            p.text(xx, self.top + self.H + 6, label, size, MUTED, anchor="ma")
        if xlabel:
            p.text(self.left + self.W - 6, self.top + self.H - 6, xlabel, 13, MUTED, anchor="rd")
        if ylabel:
            p.text(self.left + 6, self.top + 4, ylabel, 13, MUTED)


def response(w: int, h: int, res, title: str = "") -> Image.Image:
    """Each breath's MV against its EtCO2, as the browser draws it: rebreathing breaths
    filled, baseline breaths as rings, the smooth curve (robust monotone P-spline) over the dashed
    least-squares line, and the labels of the lab's figures."""
    from ..processing.metrics import response_curve, response_labels

    p = Pic(w, h, PANEL)
    reb, base, skip, out, fit = res.rebreathing, res.baseline, res.skipped, res.excluded, res.fit
    every = reb + base + skip + out
    from ..processing.metrics import response_axes

    x0, x1, y0, y1 = response_axes(res)
    ax = Axes(p, x0, x1, y0, y1)
    ax.frame([(t, _fmt(t)) for t in nice_ticks(x0, x1, max(2, int(ax.W / 70)))], nice_ticks(y0, y1, max(3, int(ax.H / 55))), "etCO2 (mmHg)", "MV (liters/min)")
    if title:
        p.text(w / 2, 8, title, 15, TEXT, "Bold", anchor="ma")
    for q in skip:
        p.dot(ax.px(q.etco2), ax.py(q.mv), 3.5, outline=MUTED, width=1.4)
    for q in base:
        p.dot(ax.px(q.etco2), ax.py(q.mv), 5, outline=MUTED, width=2)
    for q in reb:
        p.dot(ax.px(q.etco2), ax.py(q.mv), 5, fill=PNEUMO)
    for q in out:
        X, Y = ax.px(q.etco2), ax.py(q.mv)
        p.line([(X - 5, Y - 5), (X + 5, Y + 5)], ALARM_HIGH, 2.4)
        p.line([(X - 5, Y + 5), (X + 5, Y - 5)], ALARM_HIGH, 2.4)
    if fit and reb:
        a, b = min(q.etco2 for q in reb), max(q.etco2 for q in reb)
        p.dashed(ax.px(a), ax.py(fit.slope * a + fit.intercept), ax.px(b), ax.py(fit.slope * b + fit.intercept), MUTED, 1.6, 7, 6)
        cx, cy = response_curve([q.etco2 for q in reb], [q.mv for q in reb])
        p.line([(ax.px(u), ax.py(v)) for u, v in zip(cx, cy)], TEXT, 3)
    fit_text, base_text = response_labels(res)
    lx, ly = ax.left + 12, ax.top + 22
    widest = 0.0
    for i, part in enumerate(fit_text.split(" · ") if fit_text else []):
        p.text(lx, ly + 19 * i, part, 15, TEXT, "Bold" if i == 0 else "Medium")
        widest = max(widest, p.text_w(part, 15, "Bold"))
    if base_text and base:
        bx = max(ax.left + 4, ax.px(min(q.etco2 for q in base)) - 6)
        above = ax.py(max(q.mv for q in base)) - 46
        below = ax.py(min(q.mv for q in base)) + 10
        if above >= ax.top + 64:
            by = above
        elif below + 38 <= ax.py(0):
            by = below
        else:
            bx, by = lx + widest + 28, ly
        for i, part in enumerate(base_text.split(" · ")):
            p.text(bx, by + 19 * i, part, 15, TEXT, "Bold" if i == 0 else "Medium")
    if not every:
        p.text(w / 2, h / 2, "MV of each breath vs its EtCO2 appears here as breaths arrive", 15, MUTED, anchor="mm")
    return p.done()


def trend(w: int, h: int, t: Sequence[float], v: Sequence[float], t0: float, t1: float, ymax: float, color, ylabel: str, limits: Sequence[tuple[float, tuple]] = (), markers: Sequence[tuple[float, str]] = ()) -> Image.Image:
    """One value per breath against time: a line with a dot on every breath."""
    p = Pic(w, h, PANEL)
    ax = Axes(p, t0, t1, 0, ymax)

    def clock(sec: float) -> str:
        sec = max(0.0, sec - t0)
        return f"{int(sec // 60)}:{int(sec % 60):02d}"

    ax.frame([(x, clock(x)) for x in nice_ticks(t0, t1, max(2, int(ax.W / 90)))], nice_ticks(0, ymax, max(3, int(ax.H / 55))), "", ylabel)
    for y, col in limits:
        if 0 < y < ymax:
            p.dashed(ax.left, ax.py(y), ax.left + ax.W, ax.py(y), col, 2, 9, 6)
    for mt, label in markers:
        if t0 <= mt <= t1:
            X = ax.px(mt)
            p.dashed(X, ax.top, X, ax.top + ax.H, TEXT, 1.2, 4, 4)
            p.text(X + 4, ax.top + 2, f"▼ {label}", 13, TEXT)
    pts = [(ax.px(a), ax.py(min(ymax, b))) for a, b in zip(t, v) if t0 <= a <= t1 and math.isfinite(b)]
    p.line(pts, color, 2)
    for X, Y in pts:
        p.dot(X, Y, 4, fill=color)
    return p.done()
