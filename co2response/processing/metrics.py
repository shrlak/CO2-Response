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

    @property
    def slope(self) -> float:
        return self.fit.slope if self.fit else NAN


def co2_response(
    points: Iterable[tuple],
    window: TestWindow | float | None,
    skip_sec: float = 0.0,
    excluded: Callable[[object], bool] | None = None,
) -> CO2Response:
    """Ventilatory response from accepted breaths.

    ``points`` are (t, EtCO2 mmHg, MV L/min[, key]) with MV the breath's own minute
    ventilation. ``window`` (see ``test_window``) is on the same clock as t; a bare
    number is the start of rebreathing, and None means it was not marked. Breaths with
    a missing value are left out. ``excluded(key)`` says which breaths were taken out
    by hand.
    """
    if not isinstance(window, TestWindow):
        window = TestWindow(None, window, None)
    start, end = window.start, window.end
    base: list[RespPoint] = []
    reb: list[RespPoint] = []
    skipped: list[RespPoint] = []
    out: list[RespPoint] = []
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
        elif start is not None and (t < start + skip_sec or (end is not None and t >= end)):
            skipped.append(rp)
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
