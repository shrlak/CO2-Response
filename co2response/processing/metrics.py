"""Per-breath numbers: EtCO2 median, respiratory rate, minute ventilation, linear fit."""

from __future__ import annotations

import math
from typing import NamedTuple, Sequence

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
    #: Pearson correlation coefficient, with the sign of the slope
    r: float = NAN
    #: x where the line crosses y = 0. For MV against EtCO2 this is the extrapolated
    #: EtCO2 at zero ventilation (the "x-intercept" CHOP and Read report)
    x_intercept: float = NAN
    #: standard error of the slope
    slope_se: float = NAN
    #: half-width of the slope's 95 % confidence interval (Student t, n − 2 df)
    slope_ci95: float = NAN
    n: int = 0


# two-sided 95 % Student t critical values for 1–30 degrees of freedom
_T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def t95(df: int) -> float:
    """Two-sided 95 % critical value of Student's t (Cornish–Fisher above 30 df)."""
    if df < 1:
        return NAN
    if df <= 30:
        return _T95[df - 1]
    z = 1.959964
    return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df * df)


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Fit | None:
    """Ordinary least squares y = slope·x + intercept, with r, the x-intercept and the
    slope's standard error and 95 % confidence interval."""
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
    r = math.copysign(math.sqrt(min(1.0, r2)), slope)
    x_int = -intercept / slope if slope != 0 else NAN
    # residual variance about the line, then the slope's standard error
    syy_c = syy - sy * sy / n
    sxx_c = den / n
    sse = max(0.0, syy_c - slope * slope * sxx_c)
    se = math.sqrt(sse / (n - 2) / sxx_c) if sxx_c > 0 else NAN
    return Fit(slope, intercept, r2, r, x_int, se, t95(n - 2) * se, n)
