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
