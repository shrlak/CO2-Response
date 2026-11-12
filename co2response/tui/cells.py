"""Smooth pictures in plain text, for terminals that cannot show images (macOS Terminal,
the Windows console host, tmux): a picture is drawn at a high resolution with the web
page's line widths, font and colours, then each character cell is matched to the block
character (2 x 2 quadrants, or eighths of the cell across or down) and the two colours,
its foreground and background, that look most like that part of the picture. Edges get
in-between colours, so lines and digits read smooth instead of stepped.

Every terminal font has the block characters (U+2580-U+259F), and most terminals draw
them themselves to fill the cell exactly, so neighbouring cells join without gaps.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw
from rich.color import Color
from rich.style import Style
from rich.text import Text

#: one character cell, drawn as CW x CH pixels (terminal cells are about twice as tall as wide)
CW, CH = 12, 24
#: each cell is matched on an 8 x 8 grid of samples
_SX, _SY = 8, 8


def _glyphs() -> tuple[list[str], np.ndarray]:
    """The block characters and, for each, which of the 8 x 8 samples it covers."""
    chars: list[str] = []
    masks: list[np.ndarray] = []
    yy, xx = np.mgrid[0:_SY, 0:_SX]

    def add(ch: str, m: np.ndarray) -> None:
        chars.append(ch)
        masks.append(m.reshape(-1))

    quad = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"
    for i, ch in enumerate(quad):
        if i in (0, 15):
            continue  # uniform cells are handled apart
        ul, ur, ll, lr = (i >> 0) & 1, (i >> 1) & 1, (i >> 2) & 1, (i >> 3) & 1
        top, left = yy < _SY // 2, xx < _SX // 2
        m = (top & left & bool(ul)) | (top & ~left & bool(ur)) | (~top & left & bool(ll)) | (~top & ~left & bool(lr))
        add(ch, m)
    for k, ch in enumerate("▁▂▃▅▆▇", start=1):  # lower eighths (▄ is a quadrant shape already)
        k = k if k < 4 else k + 1
        add(ch, yy >= _SY - k)
    for k, ch in enumerate("▏▎▍▋▊▉", start=1):  # left eighths (▌ likewise)
        k = k if k < 4 else k + 1
        add(ch, xx < k)
    add("▔", yy < 1)
    add("▕", xx >= _SX - 1)
    return chars, np.array(masks, dtype=np.float32)


GLYPHS, MASKS = _glyphs()


def _drawn(top: float, bottom: float) -> np.ndarray:
    """Each glyph as a terminal draws it when its block characters leave ``top`` and
    ``bottom`` (fractions of the cell) uncovered: the glyph squeezed between them, the
    background showing above and below. Values are how much of each sample it covers."""
    h = 1.0 - top - bottom
    R = np.zeros((_SY, _SY), dtype=np.float32)  # R[sample row, glyph row]: overlap
    for y in range(_SY):
        a0, a1 = y / _SY, (y + 1) / _SY
        for j in range(_SY):
            b0, b1 = top + h * j / _SY, top + h * (j + 1) / _SY
            R[y, j] = max(0.0, min(a1, b1) - max(a0, b0)) * _SY
    m = MASKS.reshape(len(GLYPHS), _SY, _SX)
    return np.einsum("yj,kjx->kyx", R, m).reshape(len(GLYPHS), -1).astype(np.float32)


#: the glyphs as this terminal draws them (see ``set_gap``), and which may be used
DRAWN = MASKS
ALLOWED = np.ones(len(GLYPHS), dtype=bool)


def set_gap(top: float = 0.0, bottom: float = 0.0) -> None:
    """Tell the fit that block characters do not fill the cell's height. macOS Terminal
    with SF Mono leaves about 3/16 of the cell above and 1/16 below, where the background
    colour shows (the background always fills the whole cell). Each cell is then matched
    to the glyph and colours that look closest as drawn, gaps included, so shapes are not
    striped across or left with loose hairlines."""
    global DRAWN
    DRAWN = MASKS if top <= 0 and bottom <= 0 else _drawn(top, bottom)


#: the terminal colour names of the channels stand for the browser's colours
_WEB = {
    "yellow1": (255, 214, 10),
    "deep_sky_blue1": (56, 217, 245),
    "spring_green2": (91, 227, 125),
    "green3": (91, 227, 125),
    "white": (230, 235, 240),
    "red": (255, 59, 48),
    "gold1": (255, 159, 10),
}


@lru_cache(maxsize=256)
def colour_of(style) -> tuple[int, int, int]:
    """The colour of a rich style ("bold green3", "#ff3b30", "grey50") as (r, g, b)."""
    if isinstance(style, tuple):
        return style
    words = [w for w in str(style).split() if w not in ("bold", "dim", "italic", "reverse")]
    name = words[-1] if words else "white"
    if name in _WEB:
        return _WEB[name]
    try:
        c = Color.parse(name).get_truecolor()
    except Exception:  # noqa: BLE001  (an unknown name: plain text colour)
        return _WEB["white"]
    return (c.red, c.green, c.blue)


@lru_cache(maxsize=8192)
def _style(fg: tuple[int, int, int] | None, bg: tuple[int, int, int]) -> Style:
    return Style(color=Color.from_rgb(*fg) if fg else None, bgcolor=Color.from_rgb(*bg))


#: True: edges in in-between colours (terminals with 24-bit colour). False: every cell in
#: exactly two colours, the stroke's and the background's, since a terminal with 256
#: colours rounds the in-between ones to whatever is nearest and the edges get specks.
BLEND = True


def _fit(s: np.ndarray, busy_min: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each cell's samples ``s`` (n, 64, channels): the best glyph (-1: one colour), its
    foreground and its background colour. Each glyph covers each sample by some amount m
    (``DRAWN``), and the cell is fitted as background + m x (foreground - background) by
    least squares."""
    n, P, _ = s.shape
    mean = s.mean(1)
    sse_uniform = ((s - mean[:, None, :]) ** 2).sum((1, 2))
    best = np.full(n, -1)
    fg_best, bg_best = mean.copy(), mean.copy()
    # only cells that are not one colour need a block character: fit every glyph at once
    busy = np.nonzero(sse_uniform > busy_min * P)[0]
    if len(busy):
        M = DRAWN
        sb = s[busy]
        Sx = sb.sum(1)  # (m, c)
        Sxx = (sb**2).sum(1)
        Sm = M.sum(1)  # (k,)
        Smm = (M**2).sum(1)
        Smx = np.einsum("kp,mpc->mkc", M, sb)  # (m, k, c); not optimize=True: BLAS would spread it over every core for no gain
        den = np.maximum(P * Smm - Sm**2, 1e-6)
        d = (P * Smx - Sm[None, :, None] * Sx[:, None, :]) / den[None, :, None]
        b = (Sx[:, None, :] - d * Sm[None, :, None]) / P
        sse = (Sxx[:, None, :] - b * Sx[:, None, :] - d * Smx).sum(2)
        sse[:, ~ALLOWED] = np.inf
        k = sse.argmin(1)
        idx = np.arange(len(busy))
        better = sse[idx, k] < sse_uniform[busy] - 1e-3 * P
        sel = busy[better]
        best[sel] = k[better]
        fg_best[sel] = (b + d)[idx, k][better]
        bg_best[sel] = b[idx, k][better]
    return best, fg_best, bg_best


def _cells(a: np.ndarray, cols: int, rows: int) -> np.ndarray:
    """A picture (rows*8, cols*8, channels) as (cells, 64 samples, channels)."""
    ch = a.shape[-1]
    return a.reshape(rows, _SY, cols, _SX, ch).transpose(0, 2, 1, 3, 4).reshape(rows * cols, _SX * _SY, ch)


def to_text(rgb: np.ndarray, cols: int, rows: int, overlay: dict[tuple[int, int], tuple[str, str]] | None = None, bg=(0, 0, 0)) -> Text:
    """``rgb``: a float picture (rows*8, cols*8, 3) in 0..255, as ``rows`` lines of ``cols``
    cells, the edges in in-between colours. ``overlay`` puts characters (with a rich
    style) on given (row, col) cells, on the background ``bg``: labels stay sharp text."""
    best, fg, bgc = _fit(_cells(rgb, cols, rows), 40.0)
    # colours to whole numbers, a little coarser so neighbouring cells share styles
    fq = np.clip(np.round(fg / 3) * 3, 0, 255).astype(int).tolist()
    bq = np.clip(np.round(bgc / 3) * 3, 0, 255).astype(int).tolist()
    return _emit(best, fq, bq, cols, rows, overlay, bg)


def to_text_exact(alpha: np.ndarray, colour: np.ndarray, cols: int, rows: int, overlay: dict[tuple[int, int], tuple[str, str]] | None = None, bg=(0, 0, 0)) -> Text:
    """The same in two colours to a cell: where the strokes cover a sample (``alpha`` 0..1,
    (rows*8, cols*8)) at least half, their ``colour``; elsewhere ``bg``."""
    a = _cells(alpha[..., None], cols, rows)
    w = a[..., 0]  # (n, 64): how much stroke each sample should show
    # each glyph either way round (stroke on the glyph, or on the rest of the cell), or the
    # cell all background or all stroke: whichever, as the terminal draws it, is closest
    M = DRAWN
    Saa = (w**2).sum(1)[:, None]
    Sam = np.einsum("np,kp->nk", w, M)  # (n, k); einsum, not @: one core (see _fit)
    Smm = (M**2).sum(1)[None, :]
    Sa = w.sum(1)[:, None]
    P = float(w.shape[1])
    e_on = Saa - 2 * Sam + Smm  # stroke where the glyph is
    e_off = Saa - 2 * (Sa - Sam) + (P - 2 * M.sum(1)[None, :] + Smm)  # stroke around it
    e_on[:, ~ALLOWED] = np.inf
    e_off[:, ~ALLOWED] = np.inf
    e_bg, e_st = Saa[:, 0], ((w - 1) ** 2).sum(1)
    k_on, k_off = e_on.argmin(1), e_off.argmin(1)
    idx = np.arange(len(w))
    choices = np.stack([e_bg, e_st, e_on[idx, k_on], e_off[idx, k_off]], 1)
    pick_kind = choices.argmin(1)
    best = np.where(pick_kind == 2, k_on, np.where(pick_kind == 3, k_off, -1))
    on_f = (pick_kind == 2) | (pick_kind == 1)
    on_b = (pick_kind == 3) | (pick_kind == 1)
    c = _cells(colour, cols, rows)
    # the cell's stroke colour: that of its most covered sample (the brighter on a tie), so a
    # line crossing a grid line keeps its own colour rather than a mixture
    pick = (w * 1000 + c.sum(2) / 3).argmax(1)
    stroke = c[np.arange(len(pick)), pick]
    bg_a = np.array(bg, dtype=np.float32)
    fcol = np.where(on_f[:, None], stroke, bg_a)
    bcol = np.where(on_b[:, None], stroke, bg_a)
    # a stroke colour in steps, so a line keeps one colour along its length
    fq = np.clip(np.round(fcol / 8) * 8, 0, 255).astype(int).tolist()
    bq = np.clip(np.round(bcol / 8) * 8, 0, 255).astype(int).tolist()
    return _emit(best, fq, bq, cols, rows, overlay, bg)


def _emit(best: np.ndarray, fq: list, bq: list, cols: int, rows: int, overlay: dict[tuple[int, int], tuple[str, str]] | None, bg) -> Text:
    overlay = overlay or {}
    bg_t = tuple(int(v) for v in bg)
    lines = []
    for r in range(rows):
        t = Text(no_wrap=True, overflow="crop", end="")
        run: list[str] = []
        run_style: Style | None = None
        for c in range(cols):
            i = r * cols + c
            if (r, c) in overlay:
                ch, st = overlay[(r, c)]
                style = _style(None, bg_t) + Style.parse(st) if st else _style(None, bg_t)
            else:
                f, b = tuple(fq[i]), tuple(bq[i])
                if best[i] < 0 or f == b:
                    ch, style = " ", _style(None, b if best[i] >= 0 else f)
                else:
                    ch, style = GLYPHS[best[i]], _style(f, b)
            if style != run_style and run:
                t.append("".join(run), run_style)
                run = []
            run_style = style
            run.append(ch)
        if run:
            t.append("".join(run), run_style)
        lines.append(t)
    return Text("\n", no_wrap=True, end="").join(lines)


class Smooth:
    """A picture of ``cols`` x ``rows`` cells, drawn in cell coordinates (x across in
    cells, y down in cells), shown as text with ``text()``. Thin strokes are brightened
    (``gain``) so a line about as thin as the browser's stays as bright as its colour."""

    def __init__(self, cols: int, rows: int, bg=(0, 0, 0), gain: float = 3.0) -> None:
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.bg = tuple(bg)
        self.gain = gain
        W, H = self.cols * CW, self.rows * CH
        self.col = Image.new("RGB", (W, H), (0, 0, 0))
        self.alpha = Image.new("L", (W, H), 0)
        self.dc = ImageDraw.Draw(self.col)
        self.da = ImageDraw.Draw(self.alpha)
        self.overlay: dict[tuple[int, int], tuple[str, str]] = {}

    @staticmethod
    def _p(x: float, y: float) -> tuple[float, float]:
        return x * CW, y * CH

    def line(self, pts: Sequence[tuple[float, float]], colour, width: float = 0.2) -> None:
        """A polyline through points in cell units; ``width`` in cell heights."""
        if len(pts) < 2:
            return
        xy = [self._p(x, y) for x, y in pts]
        w = max(1, round(width * CH))
        self.dc.line(xy, fill=tuple(colour), width=w, joint="curve")
        self.da.line(xy, fill=255, width=w, joint="curve")

    def dashed(self, x0: float, x1: float, y: float, colour, width: float = 0.14, on: float = 1.2, off: float = 0.8) -> None:
        self.dashed_line((x0, y), (x1, y), colour, width, on, off)

    def dashed_line(self, a: tuple[float, float], b: tuple[float, float], colour, width: float = 0.12, on: float = 1.2, off: float = 0.8) -> None:
        """A dashed line from ``a`` to ``b``; ``on`` and ``off`` in cell widths."""
        import math

        (x0, y0), (x1, y1) = a, b
        # lengths measured on screen, where a cell is about twice as tall as wide
        dx, dy = x1 - x0, (y1 - y0) * 2
        length = math.hypot(dx, dy)
        if length <= 0:
            return
        ux, uy = dx / length, dy / length / 2
        t = 0.0
        while t < length:
            e = min(length, t + on)
            self.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)], colour, width)
            t = e + off

    def dot(self, x: float, y: float, r: float, colour, outline: bool = False, width: float = 0.1) -> None:
        """A dot of radius ``r`` cell widths (round on screen)."""
        cx, cy = self._p(x, y)
        rr = r * CW
        box = [cx - rr, cy - rr, cx + rr, cy + rr]
        w = max(1, round(width * CH))
        if outline:
            self.dc.ellipse(box, outline=tuple(colour), width=w)
            self.da.ellipse(box, outline=255, width=w)
        else:
            self.dc.ellipse(box, fill=tuple(colour))
            self.da.ellipse(box, fill=255)

    def glyph_text(self, x: float, y: float, s: str, size: float, colour, font, anchor: str = "rm") -> None:
        """Text drawn as a picture (the large numbers): ``size`` in cell heights."""
        px, py = self._p(x, y)
        self.dc.text((px, py), s, font=font, fill=tuple(colour), anchor=anchor)
        self.da.text((px, py), s, font=font, fill=255, anchor=anchor)

    def text(self, row: int, col: int, s: str, style: str = "") -> None:
        """Sharp terminal characters over the picture, starting at a cell."""
        for i, ch in enumerate(s):
            c = col + i
            if 0 <= row < self.rows and 0 <= c < self.cols:
                self.overlay[(row, c)] = (ch, style)

    def text_right(self, row: int, col_end: int, s: str, style: str = "") -> None:
        self.text(row, col_end - len(s), s, style)

    def render(self) -> Text:
        size = (self.cols * _SX, self.rows * _SY)
        a = np.asarray(self.alpha.resize(size, Image.Resampling.BOX), dtype=np.float32) / 255.0
        c = np.asarray(self.col.resize(size, Image.Resampling.BOX), dtype=np.float32)
        with np.errstate(invalid="ignore", divide="ignore"):
            colour = np.where(a[..., None] > 0, c / np.maximum(a[..., None], 1e-6), 0.0)
        k = np.clip(a * self.gain, 0.0, 1.0)[..., None]
        if not BLEND:
            return to_text_exact(k[..., 0], np.clip(colour, 0, 255), self.cols, self.rows, self.overlay, self.bg)
        bg = np.array(self.bg, dtype=np.float32)
        out = bg * (1 - k) + np.clip(colour, 0, 255) * k
        return to_text(out, self.cols, self.rows, self.overlay, self.bg)


# ------------------------------------------------------------------ the monitor's pieces


def wave(
    sweep, cols: int, rows: int, vmin: float, vmax: float, colour, ticks: Sequence[float] = (), signed: bool = False, limit: float | None = None, gutter: int = 6, bg=(0, 0, 0)
) -> Text:
    """The sweeping waveform as a thin line, the way the browser draws it: written left to
    right with a gap ahead of the pen, grid lines at the ticks, the scale at the right
    and the warning limit dashed in red."""
    import math

    from .pixels import ALARM_HIGH, GRID

    sm = Smooth(cols, rows, bg)
    right = max(1.0, cols - gutter - 0.5)
    top, bottom = 0.75, 0.35  # cells kept clear above (the border title) and below

    def y(v: float) -> float:
        c = min(vmax, max(vmin, v))
        return rows - bottom - (c - vmin) / (vmax - vmin) * (rows - top - bottom)

    def label(t: float) -> str:
        s = f"{t:g}"
        return f"+{s}" if signed and t > 0 else s

    grid = GRID if BLEND else (48, 48, 48)  # a grey that 256 colours have
    for t in ticks:
        yy = y(t)
        sm.line([(0, yy), (right, yy)], grid, 0.06)
        sm.text_right(min(rows - 1, int(yy)), cols, label(t), "#86939f")
    if limit is not None and vmin < limit < vmax:
        yy = y(limit)
        sm.dashed(0, right, yy, ALARM_HIGH, 0.12)
        sm.text_right(min(rows - 1, int(yy)), cols, f"{round(limit, 1):g}", "bold #ff3b30")
    n = sweep.n
    ys = sweep.buf.copy()
    gap = max(1, math.ceil(0.03 * n))
    ys[(sweep.pos + np.arange(gap)) % n] = np.nan
    steps = max(2, int(right * 4))  # four points to a cell across
    edges = (np.arange(steps + 1) * n) // steps
    seg: list[tuple[float, float]] = []
    for i in range(steps):
        chunk = ys[edges[i] : max(edges[i] + 1, edges[i + 1])]
        chunk = chunk[np.isfinite(chunk)]
        x = i * right / steps
        if not len(chunk):
            sm.line(seg, colour)
            seg = []
            continue
        lo, hi = float(chunk.min()), float(chunk.max())
        if len(chunk) > 2 and hi - lo > (vmax - vmin) * 0.02:  # a steep part: its whole range
            seg.append((x, y(float(chunk[0]))))
            sm.line([(x, y(lo)), (x, y(hi))], colour)
            seg.append((x, y(float(chunk[-1]))))
        else:
            seg.append((x, y(float(chunk.mean()))))
    sm.line(seg, colour)
    return sm.render()


def _digit_height(size: float) -> float:
    """How tall the digits are, in pixels, at a font size."""
    from .pixels import font

    b = font(int(size), "Bold").getbbox("0123456789", anchor="ls")
    return float(b[3] - b[1])


#: the digits drawn at this width (terminal tiles are narrow for their height)
SQUEEZE = 0.85


def number_size(value: str, cols: int, rows: int) -> float:
    """The largest font size (pixels) at which ``value`` fits ``cols`` x ``rows`` cells,
    with the digits a whole number of rows tall (so their top and bottom edges fall on the
    edges of cells and stay crisp)."""
    from .pixels import font

    if cols < 2 or rows < 1:
        return 0.0
    room_w = (cols - 1.0) * CW / SQUEEZE
    unit = CH / _digit_height(100.0) * 100.0  # the font size of digits one row tall
    for n in range(max(1, round(rows * 0.86)), 0, -1):
        if font(int(n * unit), "Bold").getlength(value) <= room_w:
            return n * unit
    size = unit
    while size > 6 and font(int(size), "Bold").getlength(value) > room_w:
        size *= 0.95
    return size


def number(value: str, cols: int, rows: int, colour, bg=(13, 17, 23), size: float | None = None) -> Text:
    """A number in the browser's font (Barlow Semi Condensed Bold, a little narrower),
    right-aligned and centred top to bottom, with smooth edges; ``size`` the font size in
    pixels (all the tiles take the same), at most what fits."""
    from .pixels import font

    fit = number_size(value, cols, rows)
    size = fit if size is None else min(size, fit)
    sm = Smooth(cols, rows, bg, gain=1.0)
    if size > 6:
        f = font(int(size), "Bold")
        box = f.getbbox(value, anchor="ls")
        top = f.getbbox("0123456789", anchor="ls")[1]  # the digits' top, above the baseline
        w, h = int(box[2] - box[0]), int(box[3] - top)
        mask = Image.new("L", (max(1, w), max(1, h)), 0)
        ImageDraw.Draw(mask).text((-box[0], -top), value, font=f, fill=255, anchor="ls")
        mask = mask.resize((max(1, round(w * SQUEEZE)), max(1, h)), Image.Resampling.LANCZOS)
        n = max(1, round(_digit_height(int(size)) / CH))
        x = round((cols - 0.5) * CW) - mask.width
        y = ((rows - n) // 2) * CH  # the digits' top on the edge of a cell
        sm.col.paste(tuple(colour), (x, y), mask)
        sm.alpha.paste(255, (x, y), mask)
    return sm.render()
