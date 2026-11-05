"""Canvas drawing from Python: the sweeping waveforms, the trend and response plots,
and the zoomable review charts."""

from __future__ import annotations

import math
from typing import Callable

import js
import numpy as np

from .dom import css_var, proxy

FONT = '"Barlow Semi Condensed", "Arial Narrow", sans-serif'


class Canvas:
    """A canvas whose backing store follows its on-screen size × devicePixelRatio."""

    def __init__(self, el) -> None:
        self.el = el
        self.ctx = el.getContext("2d")
        self.W = self.H = 0
        self.dpr = 1.0

    def fit(self) -> bool:
        r = self.el.getBoundingClientRect()
        dpr = float(js.window.devicePixelRatio or 1)
        W, H = max(1, round(r.width * dpr)), max(1, round(r.height * dpr))
        if (W, H, dpr) == (self.W, self.H, self.dpr):
            return False
        self.el.width, self.el.height = W, H
        self.W, self.H, self.dpr = W, H, dpr
        return True


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return []
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + step * 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def path_d(xs, ys, connect=None) -> str:
    """An SVG path string (one JS call to stroke thousands of points). Breaks at NaN."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    if connect is not None:
        ok_join = np.append(np.asarray(connect, bool)[:-1], False)
    else:
        ok_join = np.ones(len(xs), bool)
    out = []
    pen = False
    for x, y, good, join in zip(xs.tolist(), ys.tolist(), ok.tolist(), ok_join.tolist()):
        if not good:
            pen = False
            continue
        out.append(f"{'L' if pen else 'M'}{x:.1f} {y:.1f}")
        pen = join
    return "".join(out)


# ------------------------------------------------------------------ sweep


class Sweep:
    """Monitor-style sweep: drawn left to right with an erase gap ahead of the pen."""

    def __init__(self, fg, bg, color_var: str) -> None:
        self.fg, self.bg = Canvas(fg), Canvas(bg)
        self.color_var = color_var
        self.vmin, self.vmax = 0.0, 1.0
        self.ticks: list[float] = []
        self.signed = False
        self.limit: float | None = None
        self.n = 1200
        self.pending: list[float] = []
        self.pos = 0
        self.last: tuple[float, float] | None = None
        self.color = "#fff"
        self._opts = None

    def set_options(self, vmin, vmax, ticks, signed, limit, seconds, rate, theme: str) -> None:
        opts = (vmin, vmax, tuple(ticks), signed, limit, seconds, rate, theme)
        if opts == self._opts:
            return
        self._opts = opts
        self.vmin, self.vmax, self.ticks, self.signed, self.limit = vmin, vmax, list(ticks), signed, limit
        n = max(10, round(seconds * rate))
        if n != self.n:
            self.n = n
            self.clear()
        self.color = css_var(self.color_var) or "#fff"
        self.draw_background()

    def _y(self, v: float) -> float:
        d = self.fg.dpr
        top, bot = 26 * d, 8 * d
        c = min(self.vmax, max(self.vmin, v))
        return self.fg.H - bot - (c - self.vmin) / (self.vmax - self.vmin) * (self.fg.H - top - bot)

    def draw_background(self) -> None:
        self.fg.fit()
        self.bg.fit()
        g = self.bg.ctx
        d, W = self.bg.dpr, self.bg.W
        g.clearRect(0, 0, W, self.bg.H)
        g.font = f"{12 * d}px {FONT}"
        g.textAlign = "right"
        g.textBaseline = "middle"
        right = W - 34 * d
        grid, muted, alarm = css_var("--grid"), css_var("--muted"), css_var("--alarm-high")
        g.setLineDash(js.Array.new())
        for t in self.ticks:
            y = round(self._y(t)) + 0.5
            g.strokeStyle = grid
            g.lineWidth = d
            g.beginPath()
            g.moveTo(0, y)
            g.lineTo(right, y)
            g.stroke()
            g.fillStyle = muted
            g.fillText(f"+{t:g}" if self.signed and t > 0 else f"{t:g}", W - 6 * d, y)
        lim = self.limit
        if lim is not None and self.vmin < lim < self.vmax:
            y = round(self._y(lim)) + 0.5
            g.strokeStyle = alarm
            g.lineWidth = 1.5 * d
            g.setLineDash(js.Array.of(8 * d, 6 * d))
            g.beginPath()
            g.moveTo(0, y)
            g.lineTo(right, y)
            g.stroke()
            g.setLineDash(js.Array.new())
            g.fillStyle = alarm
            g.fillText(f"{round(lim, 1):g}", W - 6 * d, y)

    def push(self, values: list[float], frozen: bool) -> None:
        if frozen:
            return
        self.pending.extend(values)
        if len(self.pending) > 6000:
            del self.pending[:-6000]

    def clear(self) -> None:
        self.fg.ctx.clearRect(0, 0, self.fg.W, self.fg.H)
        self.pending.clear()
        self.pos = 0
        self.last = None

    def draw(self) -> None:
        if self.fg.fit():
            self.last = None
            self.draw_background()
        if not self.pending or not self.fg.W:
            return
        ctx = self.fg.ctx
        d, W, H = self.fg.dpr, self.fg.W, self.fg.H
        N = self.n
        gap, lw = 16 * d, 2 * d
        ctx.strokeStyle = self.color
        ctx.lineWidth = lw
        ctx.lineJoin = "round"
        ctx.lineCap = "round"
        ctx.beginPath()
        for v in self.pending:
            i = self.pos % N
            x = i / N * W
            if i == 0:
                self.last = None
            ctx.clearRect(x + lw, 0, gap, H)
            if x + lw + gap > W:
                ctx.clearRect(0, 0, x + lw + gap - W, H)
            self.pos += 1
            if not math.isfinite(v):
                self.last = None
                continue
            y = self._y(v)
            if self.last is not None:
                ctx.moveTo(*self.last)
                ctx.lineTo(x, y)
            self.last = (x, y)
        ctx.stroke()
        self.pending.clear()


# ------------------------------------------------------------------ plots


class Plot:
    """A plot area with axes. Subclasses draw the data."""

    PAD = (44, 12, 8, 24)  # left, right, top, bottom in CSS px

    def __init__(self, el) -> None:
        self.c = Canvas(el)
        self.x0, self.x1 = 0.0, 1.0
        self.y0, self.y1 = 0.0, 1.0

    @property
    def box(self) -> tuple[float, float, float, float]:
        d = self.c.dpr
        left, right, top, bottom = (p * d for p in self.PAD)
        return left, top, self.c.W - left - right, self.c.H - top - bottom

    def px(self, x: float) -> float:
        left, _, w, _ = self.box
        return left + (x - self.x0) / ((self.x1 - self.x0) or 1) * w

    def py(self, y: float, y0: float | None = None, y1: float | None = None) -> float:
        _, top, _, h = self.box
        a, b = (self.y0, self.y1) if y0 is None else (y0, y1)
        return top + h - (y - a) / ((b - a) or 1) * h

    def frame(self, xlabels: list[tuple[float, str]], yticks: list[float], yfmt: Callable[[float], str], right_ticks=None) -> None:
        g = self.c.ctx
        d = self.c.dpr
        g.clearRect(0, 0, self.c.W, self.c.H)
        left, top, w, h = self.box
        grid, muted = css_var("--grid"), css_var("--muted")
        g.font = f"{12 * d}px {FONT}"
        g.lineWidth = d
        g.strokeStyle = grid
        g.fillStyle = muted
        g.textAlign = "right"
        g.textBaseline = "middle"
        for t in yticks:
            y = round(self.py(t)) + 0.5
            g.beginPath()
            g.moveTo(left, y)
            g.lineTo(left + w, y)
            g.stroke()
            g.fillText(yfmt(t), left - 6 * d, y)
        if right_ticks:
            g.textAlign = "left"
            ticks, a, b, fmt = right_ticks
            for t in ticks:
                g.fillText(fmt(t), left + w + 6 * d, self.py(t, a, b))
        g.textAlign = "center"
        g.textBaseline = "top"
        for x, text in xlabels:
            X = round(self.px(x)) + 0.5
            g.beginPath()
            g.moveTo(X, top)
            g.lineTo(X, top + h)
            g.stroke()
            g.fillText(text, X, top + h + 5 * d)

    def clip(self) -> None:
        g = self.c.ctx
        left, top, w, h = self.box
        g.save()
        g.beginPath()
        g.rect(left, top, w, h)
        g.clip()

    def stroke(self, d: str, color: str, width: float = 1, alpha: float = 1, dash=None) -> None:
        if not d:
            return
        g = self.c.ctx
        g.globalAlpha = alpha
        g.strokeStyle = color
        g.lineWidth = width * self.c.dpr
        g.setLineDash(js.Array.of(*[x * self.c.dpr for x in dash]) if dash else js.Array.new())
        g.stroke(js.Path2D.new(d))
        g.setLineDash(js.Array.new())
        g.globalAlpha = 1

    def dots(self, xs, ys, color: str, size: float = 2.5) -> None:
        g = self.c.ctx
        g.fillStyle = color
        r = size * self.c.dpr
        parts = []
        for X, Y in zip(xs, ys):
            if math.isfinite(X) and math.isfinite(Y):
                parts.append(f"M{X + r:.1f} {Y:.1f}a{r:.1f} {r:.1f} 0 1 0 {-2 * r:.1f} 0a{r:.1f} {r:.1f} 0 1 0 {2 * r:.1f} 0")
        if parts:
            g.fill(js.Path2D.new("".join(parts)))


def time_labels(t0: float, t1: float, width_px: float) -> list[tuple[float, str]]:
    """Clock-time labels (HH:MM) for wall-clock seconds."""
    span = max(1.0, t1 - t0)
    step = next((s for s in (60, 120, 300, 600, 900, 1800, 3600, 7200, 14400) if span / s <= max(2, width_px / 90)), 14400)
    out = []
    t = math.ceil(t0 / step) * step
    while t <= t1:
        dt = js.Date.new(t * 1000)
        out.append((t, f"{dt.getHours():02d}:{dt.getMinutes():02d}"))
        t += step
    return out


def elapsed(s: float) -> str:
    t = max(0, round(s))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


class TrendChart(Plot):
    def draw(self, t0, t1, ymax, high, low, pts_t, pts_v, trace_t, trace_v, markers, alarms, pct: bool) -> None:
        self.c.fit()
        self.x0, self.x1, self.y0, self.y1 = t0, t1, 0.0, ymax
        _, _, w, _ = self.box
        self.frame(time_labels(t0, t1, w / self.c.dpr), nice_ticks(0, ymax, 3), (lambda v: f"{v:.1f}") if pct else (lambda v: f"{v:.0f}"))
        g = self.c.ctx
        d = self.c.dpr
        left, top, w, h = self.box
        self.clip()
        hi_c, med_c, co2, text = css_var("--alarm-high"), css_var("--alarm-med"), css_var("--co2"), css_var("--text")
        yh = self.py(high)
        g.globalAlpha = 0.1
        g.fillStyle = hi_c
        g.fillRect(left, top, w, max(0, yh - top))
        if low is not None:
            yl = self.py(low)
            g.fillStyle = med_c
            g.fillRect(left, yl, w, max(0, top + h - yl))
        g.globalAlpha = 1
        if len(trace_t) > 1:
            tt = np.asarray(trace_t)
            connect = np.append(np.diff(tt) <= 5, False)
            self.stroke(path_d([self.px(x) for x in trace_t], [self.py(v) for v in trace_v], connect), co2, 1, 0.25)
        self.stroke(f"M{left} {yh}L{left + w} {yh}", hi_c, 1.5, dash=(8, 6))
        if low is not None:
            yl = self.py(low)
            self.stroke(f"M{left} {yl}L{left + w} {yl}", med_c, 1.5, dash=(8, 6))
        self.dots([self.px(x) for x in pts_t], [self.py(v) for v in pts_v], co2)
        g.font = f"{12 * d}px {FONT}"
        g.textAlign = "left"
        g.textBaseline = "top"
        for mt, label in markers:
            X = round(self.px(mt)) + 0.5
            self.stroke(f"M{X} {top}L{X} {top + h}", text, 1, 0.55, (3, 3))
            g.fillStyle = text
            g.fillText(f"▼ {label}", X + 3 * d, top + 2 * d)
        for at, pri in alarms:
            X = self.px(at)
            g.fillStyle = hi_c if pri == "high" else med_c
            g.fill(js.Path2D.new(f"M{X} {top + h - d}L{X - 6 * d} {top + h - 12 * d}L{X + 6 * d} {top + h - 12 * d}Z"))
        g.restore()


class ResponseChart(Plot):
    def draw(self, xs: list[float], ys: list[float], fit) -> None:
        self.c.fit()
        self.x0 = math.floor((min(xs) if xs else 30) - 2)
        self.x1 = math.ceil((max(xs) if xs else 50) + 2)
        self.y0, self.y1 = 0.0, max(10, math.ceil((max(ys) if ys else 10) * 1.2))
        _, _, w, _ = self.box
        xt = nice_ticks(self.x0, self.x1, max(2, int(w / self.c.dpr / 70)))
        self.frame([(x, f"{x:g}") for x in xt], nice_ticks(self.y0, self.y1, 3), lambda v: f"{v:g}")
        self.clip()
        self.dots([self.px(x) for x in xs], [self.py(y) for y in ys], css_var("--pneumo"))
        if fit:
            self.stroke(f"M{self.px(self.x0)} {self.py(fit.slope * self.x0 + fit.intercept)}L{self.px(self.x1)} {self.py(fit.slope * self.x1 + fit.intercept)}", css_var("--text"), 1.5)
        self.c.ctx.restore()


def minmax_decimate(t: np.ndarray, v: np.ndarray, i0: int, i1: int, buckets: int):
    """Reduce samples to a min and a max per pixel column, so peaks are never lost."""
    n = i1 - i0
    if n <= buckets * 2:
        return t[i0:i1], v[i0:i1].astype(float)
    edges = (i0 + np.arange(buckets + 1) * n // buckets).astype(int)
    xs, ys = [], []
    vv = v.astype(float)
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = vv[a:b]
        fin = seg[np.isfinite(seg)]
        xs += [t[a], t[b - 1]]
        ys += [fin.min(), fin.max()] if len(fin) else [np.nan, np.nan]
    return np.asarray(xs), np.asarray(ys)


class ReviewChart(Plot):
    """Elapsed time on x, one family of series on the left axis and one on the right.
    Dragging across it selects a time range."""

    PAD = (52, 52, 8, 24)

    def __init__(self, el, on_zoom: Callable[[float, float], None]) -> None:
        super().__init__(el)
        self.on_zoom = on_zoom
        self.series: list[tuple] = []  # (t, v, color, right, points)
        self.events: list[tuple[float, bool]] = []
        self.left_label = self.right_label = ""
        self._drag: tuple[float, float] | None = None
        el.addEventListener("pointerdown", proxy(self._down))
        el.addEventListener("pointermove", proxy(self._move))
        el.addEventListener("pointerup", proxy(self._up))

    def _x_of(self, ev) -> float:
        r = self.c.el.getBoundingClientRect()
        X = (ev.clientX - r.left) * self.c.dpr
        left, _, w, _ = self.box
        return self.x0 + (X - left) / (w or 1) * (self.x1 - self.x0)

    def _down(self, ev) -> None:
        x = self._x_of(ev)
        self._drag = (x, x)
        self.c.el.setPointerCapture(ev.pointerId)

    def _move(self, ev) -> None:
        if self._drag:
            self._drag = (self._drag[0], self._x_of(ev))
            self.draw()

    def _up(self, ev) -> None:
        if not self._drag:
            return
        a, b = sorted(self._drag)
        self._drag = None
        if abs(self.px(b) - self.px(a)) > 4 * self.c.dpr:
            self.on_zoom(a, b)
        else:
            self.draw()

    @staticmethod
    def _range(vals) -> tuple[float, float]:
        fin = [x for v in vals for x in (np.nanmin(v), np.nanmax(v)) if len(v) and math.isfinite(x)] if vals else []
        if not fin:
            return 0.0, 1.0
        lo, hi = min(fin), max(fin)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.06
        return lo - pad, hi + pad

    def draw(self, x0: float | None = None, x1: float | None = None) -> None:
        if x0 is not None:
            self.x0, self.x1 = x0, x1
        self.c.fit()
        left, top, w, h = self.box
        buckets = max(50, int(w))
        shown = []
        for t, v, color, right, points in self.series:
            i0 = int(np.searchsorted(t, self.x0, "left"))
            i1 = int(np.searchsorted(t, self.x1, "right"))
            xs, ys = minmax_decimate(t, v, i0, i1, buckets) if not points else (t[i0:i1], np.asarray(v[i0:i1], float))
            shown.append((xs, ys, color, right, points))
        ly0, ly1 = self._range([s[1] for s in shown if not s[3]])
        ry0, ry1 = self._range([s[1] for s in shown if s[3]])
        self.y0, self.y1 = ly0, ly1
        xt = nice_ticks(self.x0, self.x1, max(2, int(w / self.c.dpr / 90)))
        rt = nice_ticks(ry0, ry1, 4)
        self.frame([(x, elapsed(x)) for x in xt], nice_ticks(ly0, ly1, 4), lambda v: f"{v:g}", (rt, ry0, ry1, lambda v: f"{v:g}") if any(s[3] for s in shown) else None)
        self.clip()
        g = self.c.ctx
        d = self.c.dpr
        for et, alarm in self.events:
            if self.x0 <= et <= self.x1:
                X = round(self.px(et)) + 0.5
                self.stroke(f"M{X} {top}L{X} {top + h}", css_var("--alarm-high") if alarm else css_var("--text"), 1, 0.6, (3, 3))
        for xs, ys, color, right, points in shown:
            a, b = (ry0, ry1) if right else (ly0, ly1)
            PX = [self.px(x) for x in xs.tolist()]
            PY = [self.py(y, a, b) for y in ys.tolist()]
            self.stroke(path_d(PX, PY), color, 1)
            if points:
                self.dots(PX, PY, color, 2)
        if self._drag:
            a, b = sorted(self._drag)
            g.globalAlpha = 0.18
            g.fillStyle = css_var("--co2")
            g.fillRect(self.px(a), top, self.px(b) - self.px(a), h)
            g.globalAlpha = 1
        g.restore()
        g.save()
        g.fillStyle = css_var("--muted")
        g.font = f"{12 * d}px {FONT}"
        g.textBaseline = "top"
        g.textAlign = "left"
        g.fillText(self.left_label, left + 4 * d, top + 2 * d)
        g.textAlign = "right"
        g.fillText(self.right_label, left + w - 4 * d, top + 2 * d)
        g.restore()
