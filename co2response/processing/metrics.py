"""Per-breath numbers: EtCO2 median, respiratory rate, minute ventilation, linear fit,
and the ventilatory response to CO2 (baseline vs rebreathing, slope, r, x-intercept).

The response is fitted to each breath's *own* minute ventilation (VT x 60 / period)
against that breath's EtCO2, the way CHOP's breath-by-breath analysis does. The
60 s average shown on the monitor would lag the EtCO2 it is paired with."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, NamedTuple, Sequence

import numpy as np

from ..jsfmt import naive_sum
from .breath import Breath

NAN = float("nan")


def median(xs: Sequence[float]) -> float:
    v = sorted(x for x in xs if math.isfinite(x))
    if not v:
        return NAN
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def etco2_median(breaths: Sequence[Breath], n: int = 3) -> float:
    """EtCO2 shown large: median of the last ``n`` breaths."""
    return median([b.etco2 for b in breaths[-n:]])


def respiratory_rate(breaths: Sequence[Breath], n: int = 4) -> float:
    """Breaths/min: 60 / mean period of the last ``n`` breaths."""
    p = [b.period_sec for b in breaths[-n:]]
    if not p:
        return NAN
    return 60 / (naive_sum(p) / len(p))


def minute_ventilation(breaths: Sequence[Breath], now_sec: float, window_sec: float = 60) -> float:
    """Minute ventilation, L/min, from breaths that ended in the trailing window.

    Σ VT / Σ period, which equals Σ VT over 60 s once the window is full and is not
    under-read during the first minute.
    """
    vt = 0.0
    span = 0.0
    for b in reversed(breaths):
        if b.t_end <= now_sec - window_sec:
            break
        if not math.isfinite(b.vt_ml):
            continue
        vt += b.vt_ml
        span += b.period_sec
    return ((vt / 1000) * 60) / span if span > 0 else NAN


class Fit(NamedTuple):
    slope: float
    intercept: float
    r2: float


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Fit | None:
    """Ordinary least squares y = slope·x + intercept."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    sx = sy = sxx = sxy = syy = 0.0
    for i in range(n):
        x, y = xs[i], ys[i]
        sx += x
        sy += y
        sxx += x * x
        sxy += x * y
        syy += y * y
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    slope = (n * sxy - sx * sy) / den
    intercept = (sy - slope * sx) / n
    r2den = den * (n * syy - sy * sy)
    r2 = (n * sxy - sx * sy) ** 2 / r2den if r2den > 0 else 0.0
    return Fit(slope, intercept, r2)


# ---------- ventilatory response to CO2 (Read's rebreathing method)

#: a marker whose label contains this word (any case) marks the start of rebreathing
REBREATHING_WORD = "rebreath"
#: the labels the test keys place; any label that reads like them works too
BASELINE_LABEL = "Baseline"
REBREATHING_LABEL = "Rebreathing"
END_LABEL = "End of test"
EMERGENCY_LABEL = "End of test (emergency stop)"

_WORD = re.compile(r"[a-z]+")


def marker_kind(label: str) -> str | None:
    """What a marker means for the test: 'baseline', 'start', 'end' or None.

    - end: "End of test", "Test ended", "Rebreathing stopped", "Stop rebreathing" …
      (a word starting with end/stop/finish, with "test" or "rebreath" in the label)
    - start: any other label containing "rebreath" ("Rebreathing", "CO2 rebreathing started")
    - baseline: a label containing "baseline"
    """
    low = label.lower()
    words = _WORD.findall(low)
    ends = any(w.startswith(("end", "stop", "finish")) for w in words)
    if ends and (REBREATHING_WORD in low or "test" in words):
        return "end"
    if REBREATHING_WORD in low:
        return "start"
    if "baseline" in low:
        return "baseline"
    return None


def is_rebreathing_marker(label: str) -> bool:
    return marker_kind(label) == "start"


class TestWindow(NamedTuple):
    """The latest test's times, on the markers' clock; None where a marker is missing."""

    #: start of the baseline: its marker, or the end of the previous test
    baseline: float | None
    #: start of rebreathing
    start: float | None
    #: end of the test (the first end marker after ``start``)
    end: float | None


def test_window(markers: Iterable[tuple[float, str]]) -> TestWindow:
    """Find the latest test among (time, label) markers.

    Rebreathing starts at the last start marker and ends at the first end marker after
    it. The baseline starts at the last baseline marker before the start, or at the
    end of an earlier test (so one test's recovery is not the next one's baseline
    unless nothing else is marked). A baseline marker placed after the latest test
    opens a new one: that test's rebreathing has not started yet."""
    ms = sorted(((t, marker_kind(label)) for t, label in markers), key=lambda m: m[0])
    starts = [t for t, k in ms if k == "start"]
    last_base = max((t for t, k in ms if k == "baseline"), default=None)
    if not starts or (last_base is not None and last_base > starts[-1]):
        return TestWindow(last_base, None, None)
    start = starts[-1]
    end = next((t for t, k in ms if k == "end" and t > start), None)
    before = [t for t, k in ms if t < start and k in ("baseline", "end")]
    return TestWindow(max(before) if before else None, start, end)


# these two are imported by the tests, where their names look like tests to pytest
test_window.__test__ = False  # type: ignore[attr-defined]
TestWindow.__test__ = False  # type: ignore[attr-defined]


class RespPoint(NamedTuple):
    etco2: float
    #: this breath's own minute ventilation (VT x 60 / period), L/min
    mv: float
    t: float = NAN
    #: identifies the breath to whoever supplied it (for excluding it by hand)
    key: object = None


@dataclass
class CO2Response:
    """The result of a rebreathing test, laid out as CHOP reports it.

    Baseline breaths (before the rebreathing marker) are averaged, not fitted; the
    MV-vs-EtCO2 line is fitted to the rebreathing breaths only: those after the
    first ``skip_sec`` of rebreathing and before the end-of-test marker, and not
    excluded by hand. Without a marker every breath is fitted and ``split`` is False.
    """

    baseline: list[RespPoint]
    #: the breaths the line is fitted to
    rebreathing: list[RespPoint]
    split: bool
    fit: Fit | None
    #: Pearson correlation coefficient, signed like the slope
    r: float
    #: EtCO2 (mmHg) where the fitted line reaches MV = 0
    x_intercept: float
    baseline_etco2: float
    baseline_mv: float
    #: breaths in the first ``skip_sec`` of rebreathing, and after the end of the test
    skipped: list[RespPoint] = field(default_factory=list)
    #: breaths taken out by hand
    excluded: list[RespPoint] = field(default_factory=list)
    skip_sec: float = 0.0
    window: TestWindow = TestWindow(None, None, None)
    #: the calibration at the start of rebreathing (a part of ``skipped``): the breaths
    #: watched before the response is recorded, and when it ended (None: still going, or
    #: no calibration)
    calibration: list[RespPoint] = field(default_factory=list)
    calibration_end: float | None = None

    @property
    def slope(self) -> float:
        return self.fit.slope if self.fit else NAN


def co2_response(
    points: Iterable[tuple],
    window: TestWindow | float | None,
    skip_sec: float = 0.0,
    excluded: Callable[[object], bool] | None = None,
    calibration_breaths: int = 0,
    calibration_sec: float = 0.0,
) -> CO2Response:
    """Ventilatory response from accepted breaths.

    ``points`` are (t, EtCO2 mmHg, MV L/min[, key]) with MV the breath's own minute
    ventilation. ``window`` (see ``test_window``) is on the same clock as t; a bare
    number is the start of rebreathing, and None means it was not marked. Breaths with
    a missing value are left out. ``excluded(key)`` says which breaths were taken out
    by hand.

    The start of rebreathing is calibrated first: the first ``calibration_breaths``
    breaths and the first ``calibration_sec`` seconds, whichever lasts longer, are watched
    and not fitted (``calibration``). The fit starts after both that and ``skip_sec``.
    """
    if not isinstance(window, TestWindow):
        window = TestWindow(None, window, None)
    start, end = window.start, window.end
    base: list[RespPoint] = []
    reb: list[RespPoint] = []
    skipped: list[RespPoint] = []
    out: list[RespPoint] = []
    calib: list[RespPoint] = []
    cal_end: float | None = None
    points = list(points)
    if start is not None and math.isfinite(start) and (calibration_breaths > 0 or calibration_sec > 0):
        after = sorted(p[0] for p in points if math.isfinite(p[0]) and p[0] >= start and math.isfinite(p[1]) and math.isfinite(p[2]) and (end is None or p[0] < end))
        cal_end = start + calibration_sec
        if calibration_breaths > 0:
            # the time of the last calibration breath; before there are that many the calibration goes on
            cal_end = max(cal_end, after[calibration_breaths - 1]) if len(after) >= calibration_breaths else None
    calibrating = start is not None and math.isfinite(start) and (calibration_breaths > 0 or calibration_sec > 0)

    def in_calibration(t: float) -> bool:
        # with too few breaths yet (cal_end is None) every breath is still calibration
        return calibrating and (cal_end is None or t <= cal_end)

    for pt in points:
        t, et, mv = pt[0], pt[1], pt[2]
        key = pt[3] if len(pt) > 3 else None
        if not (math.isfinite(et) and math.isfinite(mv)):
            continue
        rp = RespPoint(et, mv, t, key)
        if window.baseline is not None and t < window.baseline:
            continue  # before this test's baseline: another test, or settling in
        if (start is None and window.baseline is not None) or (start is not None and t < start):
            (out if excluded and excluded(key) else base).append(rp)
        elif start is not None and (t < start + skip_sec or (end is not None and t >= end) or in_calibration(t)):
            skipped.append(rp)
            if in_calibration(t) and not (end is not None and t >= end):
                calib.append(rp)
        else:
            (out if excluded and excluded(key) else reb).append(rp)
    fit = linear_fit([p.etco2 for p in reb], [p.mv for p in reb])
    r = NAN
    x_int = NAN
    if fit:
        r = math.copysign(math.sqrt(fit.r2), fit.slope)
        if abs(fit.slope) > 1e-12:
            x_int = -fit.intercept / fit.slope
    return CO2Response(
        baseline=base,
        rebreathing=reb,
        split=start is not None or window.baseline is not None,
        fit=fit,
        r=r,
        x_intercept=x_int,
        baseline_etco2=sum(p.etco2 for p in base) / len(base) if base else NAN,
        baseline_mv=sum(p.mv for p in base) / len(base) if base else NAN,
        skipped=skipped,
        excluded=out,
        skip_sec=skip_sec if start is not None else 0.0,
        window=window,
        calibration=calib,
        calibration_end=cal_end,
    )


def body_surface_m2(height_cm: float | None, weight_kg: float | None) -> float:
    """Body surface area, m² (Mosteller): √(height cm × weight kg / 3600)."""
    if not height_cm or not weight_kg or height_cm <= 0 or weight_kg <= 0:
        return NAN
    return math.sqrt(height_cm * weight_kg / 3600)


def normalized_slopes(slope: float, height_cm: float | None, weight_kg: float | None) -> dict[str, float]:
    """The response slope per kg (mL/min/mmHg/kg) and per m² of body surface
    (L/min/mmHg/m²), for comparing subjects of different size. NaN where the size
    was not entered."""
    bsa = body_surface_m2(height_cm, weight_kg)
    return {
        "per_kg_ml": slope * 1000 / weight_kg if weight_kg and weight_kg > 0 and math.isfinite(slope) else NAN,
        "per_m2": slope / bsa if math.isfinite(bsa) and math.isfinite(slope) else NAN,
        "bsa_m2": bsa,
    }


def co2_response_summary(res: CO2Response, fmt: Callable[[float, int], str]) -> str:
    """One line for under the MV-vs-EtCO2 plot (desktop and browser)."""
    left = []
    if res.skipped:
        left.append(f"{len(res.skipped)} outside the fit")
    if res.excluded:
        left.append(f"{len(res.excluded)} excluded by hand")
    tail = f" · {', '.join(left)}" if left else ""
    if not res.fit:
        need = "needs at least 3 rebreathing breaths" if res.split else "needs at least 3 breaths"
        if res.split and res.skip_sec and len(res.rebreathing) < 3:
            need += f" after the first {res.skip_sec:g} s"
        return f"MV vs EtCO2 · {need}{tail}"
    parts = [f"slope {fmt(res.fit.slope, 2)} L/min/mmHg · r {fmt(res.r, 2)} · x-int {fmt(res.x_intercept, 1)} mmHg · n {len(res.rebreathing)}"]
    if res.split:
        if res.baseline:
            parts.append(f"baseline {fmt(res.baseline_etco2, 1)} mmHg, {fmt(res.baseline_mv, 1)} L/min (n {len(res.baseline)})")
        else:
            parts.append("no baseline breaths in this window")
    else:
        parts.append("mark “Rebreathing” to set the baseline apart")
    return " · ".join(parts) + tail


def stability(points: Sequence[tuple[float, float]], min_breaths: int = 6, etco2_sd: float = 1.5, mv_cv: float = 0.2) -> tuple[bool, str]:
    """Is the baseline steady enough to start rebreathing? ``points`` are the recent
    baseline breaths as (EtCO2 mmHg, MV L/min). Steady means at least ``min_breaths``
    breaths with an EtCO2 standard deviation of at most ``etco2_sd`` mmHg and an MV
    coefficient of variation of at most ``mv_cv``. Returns (steady, a short reason)."""
    pts = [(a, b) for a, b in points if math.isfinite(a) and math.isfinite(b)]
    n = len(pts)
    if n < min_breaths:
        return False, f"{n} of {min_breaths} breaths"
    me = sum(a for a, _ in pts) / n
    mm = sum(b for _, b in pts) / n
    sd = math.sqrt(sum((a - me) ** 2 for a, _ in pts) / (n - 1))
    cv = math.sqrt(sum((b - mm) ** 2 for _, b in pts) / (n - 1)) / mm if mm > 0 else math.inf
    ok = sd <= etco2_sd and cv <= mv_cv
    return ok, f"EtCO2 ±{sd:.1f} mmHg, MV ±{cv * 100:.0f} %"


def response_labels(res: CO2Response) -> tuple[str, str]:
    """The two labels drawn on the MV-vs-EtCO2 graph, worded as in the lab's figures:

        Response slope = 1.244 (L/min)/mmHg · r = 0.848
        Baseline Ave = 43.64 mmHg · MV = 6.63 L/min

    Either is "" when there is nothing to say (no fit; no baseline breaths)."""
    fit = f"Response slope = {res.slope:.3f} (L/min)/mmHg · r = {res.r:.3f}" if res.fit else ""
    base = f"Baseline Ave = {res.baseline_etco2:.2f} mmHg · MV = {res.baseline_mv:.2f} L/min" if res.baseline else ""
    return fit, base


def lowess_curve(xs: Sequence[float], ys: Sequence[float], frac: float = 0.85, iters: int = 2, n_out: int = 48) -> tuple[list[float], list[float]]:
    """A curve through the points that may bend: LOWESS (locally weighted scatterplot
    smoothing, Cleveland 1979). Around each point a straight line is fitted by weighted
    least squares to the nearest ``frac`` of the points (tricube weights; 85 % by default,
    so the curve bends only with the overall shape, not breath by breath), and ``iters``
    robust passes down-weight points far from the curve (bisquare weights), so a single
    erratic breath does not drag it. Returns the curve at ``n_out`` EtCO2 values across
    the points' range; empty when there are fewer than 4 points or no spread in EtCO2."""
    pts = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pts) < 4:
        return [], []
    pts.sort()
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    if x[-1] - x[0] < 1e-6:
        return [], []
    n = len(x)
    r = min(n, max(4, math.ceil(frac * n)))
    robust = np.ones(n)

    def at(x0: float) -> float:
        d = np.abs(x - x0)
        h = max(float(np.sort(d)[r - 1]), 1e-9)
        w = np.clip(1 - (d / h) ** 3, 0, None) ** 3 * robust
        sw = w.sum()
        if sw <= 0:
            return math.nan
        mx, my = (w * x).sum() / sw, (w * y).sum() / sw
        sxx = (w * (x - mx) ** 2).sum()
        b = (w * (x - mx) * (y - my)).sum() / sxx if sxx > 1e-12 else 0.0
        return my + b * (x0 - mx)

    for _ in range(iters):
        res = y - np.array([at(v) for v in x])
        s = float(np.median(np.abs(res)))
        if s <= 1e-12:
            break
        u = np.clip(res / (6 * s), -1, 1)
        robust = (1 - u**2) ** 2
    grid = np.linspace(x[0], x[-1], n_out)
    return grid.tolist(), [at(v) for v in grid]


def _bspline_design(u: np.ndarray, nseg: int, deg: int = 3) -> np.ndarray:
    """Cubic B-spline basis on [0, 1] with ``nseg`` equal segments, at the points ``u``
    (Eilers & Marx: differences of truncated powers). Shape (len(u), nseg + deg)."""
    dx = 1.0 / nseg
    knots = dx * np.arange(-deg, nseg + deg + 1)
    t = u[:, None] - knots[None, :]
    power = np.where(t > 0, t, 0.0) ** deg
    d = np.diff(np.eye(len(knots)), n=deg + 1, axis=0) / (math.factorial(deg) * dx**deg)
    return (-1) ** (deg + 1) * power @ d.T


def response_curve(xs: Sequence[float], ys: Sequence[float], n_out: int = 200) -> tuple[list[float], list[float]]:
    """The smooth curve through the MV-vs-EtCO2 breaths: a robust, monotone, penalized
    cubic spline (a P-spline, Eilers & Marx 1996) with its smoothness chosen by the data.

    - **Penalized B-spline**: a cubic spline whose roughness (second differences of its
      coefficients) is penalized by ``lambda``, so the curve is continuous in value, slope
      and curvature and cannot wiggle with every breath.
    - **lambda by generalized cross-validation**, then the smoothest ``lambda`` whose
      score is within 5 % of the best (the "one-standard-error" rule): the curve bends
      only where the breaths as a whole bend.
    - **Robust**: Tukey bisquare weights from the residuals (re-weighted 4 times, scale
      from the median absolute deviation) so a cough or a missed breath does not drag it.
    - **Monotone**: minute ventilation does not fall as EtCO2 rises in this test, so the
      curve is constrained to run in the direction of the overall trend (an asymmetric
      penalty on steps the wrong way, iterated until it holds).

    Returns the curve at ``n_out`` EtCO2 values from the lowest to the highest breath;
    empty with fewer than 4 breaths or no spread in EtCO2."""
    pts = sorted((float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y))
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < 1e-6:
        return [], []
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    n = len(x)
    x0, span = x[0], x[-1] - x[0]
    u = (x - x0) / span
    nseg = int(min(12, max(4, round(n / 4))))
    basis = _bspline_design(u, nseg)
    nb = basis.shape[1]
    d2 = np.diff(np.eye(nb), n=2, axis=0)
    rough = d2.T @ d2 + 1e-9 * np.eye(nb)
    d1 = np.diff(np.eye(nb), n=1, axis=0)
    direction = 1.0 if float(np.polyfit(u, y, 1)[0]) >= 0 else -1.0
    lams = 10.0 ** np.linspace(-4, 4, 33)

    def pick_lambda(w: np.ndarray) -> float:
        scores = []
        for lam in lams:
            a = basis.T @ (w[:, None] * basis) + lam * rough
            hat = basis @ np.linalg.solve(a, basis.T * w)  # n x n, H = B (B'WB + lP)^-1 B'W
            rss = float(np.sum(w * (y - hat @ y) ** 2))
            dof = n - float(np.trace(hat))
            scores.append(n * rss / max(dof, 1e-3) ** 2)
        best = min(scores)
        return float(max(lam for lam, s in zip(lams, scores) if s <= best * 1.05 + 1e-12))

    def fit(lam: float, w: np.ndarray, v: np.ndarray | None = None) -> np.ndarray:
        a = basis.T @ (w[:, None] * basis) + lam * rough
        if v is not None:
            a = a + 1e6 * d1.T @ (v[:, None] * d1)
        return np.linalg.solve(a, basis.T @ (w * y))

    w = np.ones(n)
    lam = pick_lambda(w)
    for _ in range(4):
        res = y - basis @ fit(lam, w)
        scale = 1.4826 * float(np.median(np.abs(res - np.median(res))))
        scale = max(scale, 0.02 * float(np.std(y)), 1e-9)
        z = np.clip(res / (4.685 * scale), -1, 1)
        w = np.maximum((1 - z**2) ** 2, 1e-3)
        lam = pick_lambda(w)
    coef = fit(lam, w)
    v = np.zeros(nb - 1)
    for _ in range(30):  # steps against the trend get a heavy penalty until none are left
        wrong = (direction * np.diff(coef)) < -1e-9
        if not wrong.any() or np.array_equal(wrong.astype(float), v):
            break
        v = wrong.astype(float)
        coef = fit(lam, w, v)
    grid = np.linspace(0.0, 1.0, n_out)
    return (x0 + grid * span).tolist(), (_bspline_design(grid, nseg) @ coef).tolist()


def response_axes(res: CO2Response) -> tuple[float, float, float, float]:
    """The ranges of the MV-vs-EtCO2 graph, scaled to the breaths on it (x0, x1, y0, y1):
    each axis runs from the lowest to the highest value, with a tenth of the spread kept
    free on both sides, rounded out to a whole mmHg (x) or half L/min (y). The curve is
    included, so it is never cut off. With no breaths yet, a typical range."""
    every = res.baseline + res.rebreathing + res.skipped + res.excluded
    xs = [p.etco2 for p in every]
    ys = [p.mv for p in every]
    if res.rebreathing:
        cx, cy = response_curve([p.etco2 for p in res.rebreathing], [p.mv for p in res.rebreathing])
        xs += cx
        ys += cy
    if not xs:
        return 30.0, 50.0, 0.0, 10.0
    # a margin of a tenth of the spread, and never narrower than 8 mmHg / 5 L/min: a few
    # breaths close together are not blown up to fill the graph
    cx0, cx1, cy0, cy1 = min(xs), max(xs), min(ys), max(ys)
    px, py = (cx1 - cx0) * 0.1, (cy1 - cy0) * 0.12
    x0, x1 = cx0 - px, cx1 + px
    y0, y1 = cy0 - py, cy1 + py
    if x1 - x0 < 8:
        x0, x1 = (x0 + x1) / 2 - 4, (x0 + x1) / 2 + 4
    if y1 - y0 < 5:
        y0, y1 = (y0 + y1) / 2 - 2.5, (y0 + y1) / 2 + 2.5
    return float(math.floor(x0)), float(math.ceil(x1)), max(0.0, math.floor(y0 * 2) / 2), float(math.ceil(y1 * 2) / 2)
