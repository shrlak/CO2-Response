"""Drawing in a terminal: a canvas of Unicode braille cells (2 x 4 dots per character),
text laid over it, and the monitor's three kinds of graph drawn on it: the sweeping
waveform, the trend against time and the MV-vs-EtCO2 response plot.

Everything here is plain Python and returns rich ``Text`` lines, so it can be tested
without a terminal and works in any terminal that shows Unicode (Windows Terminal,
macOS Terminal, the VS Code terminal, most Linux terminals).
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from rich.text import Text

# the bit of each dot in a braille cell, by [column][row]
_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


class Canvas:
    """``w`` x ``h`` character cells; dots are addressed in (2w) x (4h) pixels with
    y = 0 at the top."""

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self.bits = [[0] * self.w for _ in range(self.h)]
        self.dot_style: list[list[str | None]] = [[None] * self.w for _ in range(self.h)]
        self.chars: dict[tuple[int, int], tuple[str, str]] = {}

    @property
    def px_w(self) -> int:
        return 2 * self.w

    @property
    def px_h(self) -> int:
        return 4 * self.h

    def dot(self, x: int, y: int, style: str) -> None:
        if 0 <= x < 2 * self.w and 0 <= y < 4 * self.h:
            r, c = y >> 2, x >> 1
            self.bits[r][c] |= _BITS[x & 1][y & 3]
            self.dot_style[r][c] = style

    def vline(self, x: int, y0: int, y1: int, style: str) -> None:
        if y0 > y1:
            y0, y1 = y1, y0
        for y in range(max(0, y0), min(4 * self.h - 1, y1) + 1):
            self.dot(x, y, style)

    def line(self, x0: float, y0: float, x1: float, y1: float, style: str) -> None:
        """A straight line in dot coordinates."""
        x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        for _ in range(4 * (self.w + self.h) * 4):
            self.dot(x0, y0, style)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def hdash(self, y: int, style: str, x0: int = 0, x1: int | None = None, on: int = 3, off: int = 2) -> None:
        """A dashed horizontal line (an alarm limit)."""
        x1 = 2 * self.w if x1 is None else x1
        for x in range(x0, x1):
            if (x - x0) % (on + off) < on:
                self.dot(x, y, style)

    def text(self, col: int, row: int, s: str, style: str = "") -> None:
        """Characters over the dots, starting at a cell."""
        if not 0 <= row < self.h:
            return
        for i, ch in enumerate(s):
            c = col + i
            if 0 <= c < self.w:
                self.chars[(row, c)] = (ch, style)

    def text_right(self, col_end: int, row: int, s: str, style: str = "") -> None:
        self.text(col_end - len(s), row, s, style)

    def lines(self) -> list[Text]:
        out = []
        for r in range(self.h):
            t = Text(no_wrap=True, overflow="crop")
            run: list[str] = []
            run_style: str | None = None
            for c in range(self.w):
                if (r, c) in self.chars:
                    ch, st = self.chars[(r, c)]
                else:
                    b = self.bits[r][c]
                    ch, st = (chr(0x2800 + b), self.dot_style[r][c] or "") if b else (" ", "")
                if st != run_style and run:
                    t.append("".join(run), run_style or None)
                    run = []
                run_style = st
                run.append(ch)
            if run:
                t.append("".join(run), run_style or None)
            out.append(t)
        return out

    def render(self) -> Text:
        return Text("\n", no_wrap=True).join(self.lines())


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick values covering lo..hi."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step - 1e-9) * step
    out, v = [], first
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _fmt(v: float) -> str:
    return f"{v:g}" if abs(v) >= 1 or v == 0 else f"{v:.2g}"


# ------------------------------------------------------------------ the sweep


class SweepBuffer:
    """The samples on a sweeping waveform: a ring the length of one screen, written
    left to right, like a bedside monitor (the old trace stays put and is erased just
    ahead of the pen)."""

    def __init__(self, n: int = 1200) -> None:
        self.n = max(10, n)
        self.buf = np.full(self.n, np.nan)
        self.pos = 0

    def resize(self, n: int) -> None:
        n = max(10, n)
        if n != self.n:
            self.n = n
            self.clear()

    def clear(self) -> None:
        self.buf = np.full(self.n, np.nan)
        self.pos = 0

    def push(self, values: Sequence[float]) -> None:
        if not len(values):
            return
        v = np.asarray(values[-self.n :], dtype=float)
        start = self.pos + len(values) - len(v)
        self.buf[(start + np.arange(len(v))) % self.n] = v
        self.pos = (self.pos + len(values)) % self.n


def draw_sweep(cv: Canvas, sweep: SweepBuffer, vmin: float, vmax: float, style: str, gutter: int = 6, ticks: Iterable[float] = (), signed: bool = False, limit: float | None = None) -> None:
    """The waveform on ``cv``, leaving ``gutter`` cells at the right for the scale."""
    W = max(2, 2 * (cv.w - gutter))
    H = cv.px_h
    top, bottom = 4, 1  # dots kept free above (the title row) and below

    def ydot(v: float) -> int:
        c = min(vmax, max(vmin, v))
        return round(H - 1 - bottom - (c - vmin) / (vmax - vmin) * (H - 1 - top - bottom))

    for t in ticks:
        y = ydot(t)
        cv.hdash(y, "grey23", 0, W, 1, 3)
        cv.text_right(cv.w, y // 4, ("+" if signed and t > 0 else "") + _fmt(t), "grey50")
    if limit is not None and vmin < limit < vmax:
        y = ydot(limit)
        cv.hdash(y, "red", 0, W)
        cv.text_right(cv.w, y // 4, _fmt(limit), "bold red")

    n = sweep.n
    buf = sweep.buf.copy()
    # the erase gap just ahead of the pen
    gap = max(1, math.ceil(0.03 * n))
    buf[(sweep.pos + np.arange(gap)) % n] = np.nan
    starts = (np.arange(W) * n) // W
    starts = np.unique(np.clip(starts, 0, n - 1))
    lo = np.fmin.reduceat(buf, starts)
    hi = np.fmax.reduceat(buf, starts)
    ends = np.append(starts[1:], n) - 1
    last = buf[ends]
    prev = math.nan
    for i, s in enumerate(starts):
        x = int(s * W // n)
        a, b = lo[i], hi[i]
        if not (math.isfinite(a) and math.isfinite(b)):
            prev = math.nan
            continue
        if math.isfinite(prev):  # join to the previous column
            a, b = min(a, prev), max(b, prev)
        cv.vline(x, ydot(a), ydot(b), style)
        prev = last[i]


# ------------------------------------------------------------------ plots on axes


class Axes:
    """A plot area inside a canvas, with tick labels on the left and below."""

    def __init__(self, cv: Canvas, x0: float, x1: float, y0: float, y1: float, left: int = 6, bottom: int = 2, top: int = 0, right: int = 1) -> None:
        self.cv = cv
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.c0, self.c1 = left, max(left + 2, cv.w - right)  # cells of the plot area
        self.r0, self.r1 = top, max(top + 2, cv.h - bottom)

    def px(self, x: float) -> float:
        return 2 * self.c0 + (x - self.x0) / (self.x1 - self.x0) * (2 * (self.c1 - self.c0) - 1)

    def py(self, y: float) -> float:
        return 4 * self.r1 - 1 - (y - self.y0) / (self.y1 - self.y0) * (4 * (self.r1 - self.r0) - 1)

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return int(self.px(x)) // 2, int(self.py(y)) // 4

    def frame(self, xticks: list[float], yticks: list[float], xfmt=_fmt, yfmt=_fmt, xlabel: str = "", ylabel: str = "") -> None:
        cv = self.cv
        # an axis box, as in the lab's figures: the left and bottom axes with tick marks
        xl, yb = 2 * self.c0, 4 * self.r1 - 1
        cv.vline(xl, 4 * self.r0, yb, "grey50")
        cv.line(xl, yb, 2 * self.c1 - 1, yb, "grey50")
        for t in yticks:
            y = round(self.py(t))
            cv.dot(xl + 1, y, "grey50")
            cv.text_right(self.c0 - 1, y // 4, yfmt(t), "grey50")
        row = self.r1
        for t in xticks:
            x = round(self.px(t))
            cv.dot(x, yb - 1, "grey50")
            s = xfmt(t)
            cv.text(max(self.c0, min(self.c1 - len(s), x // 2 - len(s) // 2)), row, s, "grey50")
        if xlabel:
            cv.text(max(self.c0, (self.c0 + self.c1) // 2 - len(xlabel) // 2), row + 1, xlabel, "grey62")
        if ylabel:
            cv.text(0, max(0, self.r0 - 1), ylabel, "grey62")


def draw_response(cv: Canvas, res, title: str = "") -> None:
    """Each breath's own MV against its EtCO2: rebreathing breaths filled (●) with the
    least-squares line through them, the baseline breaths hollow (○), breaths outside
    the fit faint (·) and the ones taken out by hand as crosses, with the labels of
    the lab's figures ("Response slope = … · r = …", "Baseline Ave = … · MV = …")."""
    from ..processing.metrics import response_labels

    every = res.baseline + res.rebreathing + res.skipped + res.excluded
    xs = [p.etco2 for p in every] or [30.0, 50.0]
    ys = [p.mv for p in every] or [0.0, 10.0]
    x0, x1 = math.floor(min(xs) - 2), math.ceil(max(xs) + 2)
    y1 = max(10.0, math.ceil(max(ys) * 1.4))
    top = 1 if title else 0
    ax = Axes(cv, x0, x1, 0, y1, left=5, bottom=2, top=top + 1)
    ax.frame(nice_ticks(x0, x1, max(2, (ax.c1 - ax.c0) // 8)), nice_ticks(0, y1, max(2, (ax.r1 - ax.r0) // 2)), xlabel="etCO2 (mmHg)", ylabel="MV (L/min)")
    if title:
        cv.text(max(0, cv.w // 2 - len(title) // 2), 0, title, "bold")
    if res.fit and res.rebreathing:
        a = min(p.etco2 for p in res.rebreathing)
        b = max(p.etco2 for p in res.rebreathing)
        f = res.fit
        cv.line(ax.px(a), ax.py(f.slope * a + f.intercept), ax.px(b), ax.py(f.slope * b + f.intercept), "white")
    for pts, ch, st in ((res.skipped, "·", "grey50"), (res.baseline, "○", "bold grey85"), (res.rebreathing, "●", "bold green3"), (res.excluded, "x", "bold red")):
        for p in pts:
            c, r = ax.cell(p.etco2, min(y1, p.mv))
            if ax.c0 <= c < ax.c1 and ax.r0 <= r < ax.r1:
                cv.text(c, r, ch, st)
    fit_text, base_text = response_labels(res)
    for i, part in enumerate(fit_text.split(" · ") if fit_text else []):
        cv.text(ax.c0 + 2, ax.r0 + i, part, "bold" if i == 0 else "")
    if base_text:
        c, r = ax.cell(min(p.etco2 for p in res.baseline), max(p.mv for p in res.baseline))
        parts = base_text.split(" · ")
        lowest = ax.cell(0, min(p.mv for p in res.baseline))[1]
        # above the baseline breaths when there is room under the slope label, else below them
        r = r - len(parts) - 1 if r - len(parts) - 1 >= ax.r0 + 3 else min(ax.r1 - len(parts), lowest + 2)
        for i, part in enumerate(parts):
            cv.text(max(ax.c0, min(c, ax.c1 - len(part))), r + i, part, "bold" if i == 0 else "")
    if not every:
        msg = "MV of each breath vs its EtCO2 appears here as breaths arrive"
        cv.text(max(ax.c0, (ax.c0 + ax.c1) // 2 - len(msg) // 2), (ax.r0 + ax.r1) // 2, msg[: ax.c1 - ax.c0], "grey50")


def draw_trend(cv: Canvas, t: Sequence[float], v: Sequence[float], t0: float, t1: float, ymax: float, style: str, ylabel: str, limits: Sequence[tuple[float, str]] = (), markers: Sequence[tuple[float, str]] = ()) -> None:
    """One value per breath against time (seconds on any clock; t0..t1 is shown)."""
    ax = Axes(cv, t0, t1, 0, ymax, left=5, bottom=2, top=1)

    def clock(sec: float) -> str:
        sec = max(0.0, sec - t0)
        return f"{int(sec // 60)}:{int(sec % 60):02d}"

    ax.frame(nice_ticks(t0, t1, max(2, (ax.c1 - ax.c0) // 9)), nice_ticks(0, ymax, max(2, (ax.r1 - ax.r0) // 2)), xfmt=clock, ylabel=ylabel)
    for y, st in limits:
        if 0 < y < ymax:
            cv.hdash(round(ax.py(y)), st, 2 * ax.c0, 2 * ax.c1)
    for mt, label in markers:
        if t0 <= mt <= t1:
            x = round(ax.px(mt))
            for y in range(4 * ax.r0, 4 * ax.r1, 2):
                cv.dot(x, y, "grey62")
            cv.text(min(ax.c1 - 1, x // 2 + 1), ax.r0, label[: max(1, ax.c1 - x // 2 - 1)], "grey62")
    prev = None
    for a, b in zip(t, v):
        if not (t0 <= a <= t1 and math.isfinite(b)):
            prev = None
            continue
        p = (ax.px(a), ax.py(min(ymax, b)))
        if prev is not None:
            cv.line(*prev, *p, style)
        else:
            cv.dot(round(p[0]), round(p[1]), style)
        prev = p


# ------------------------------------------------------------------ big digits

_FONT = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": (" ██", "  █", "  █", "  █", "  █"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", " ██", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    "-": ("   ", "   ", "███", "   ", "   "),
    ".": ("   ", "   ", "   ", "   ", " █ "),
    " ": ("   ", "   ", "   ", "   ", "   "),
}


def big_digits(s: str) -> list[str]:
    """``s`` in 5-line block digits (other characters as blanks)."""
    rows = ["", "", "", "", ""]
    for ch in s:
        glyph = _FONT.get(ch, _FONT[" "])
        for i in range(5):
            rows[i] += glyph[i] + " "
    return [r.rstrip() for r in rows]
