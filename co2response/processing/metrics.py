"""Per-breath numbers: EtCO2 median, respiratory rate, minute ventilation, linear fit,
and the ventilatory response to CO2 (baseline vs rebreathing, slope, r, x-intercept)."""

from __future__ import annotations

import math
from dataclasses import dataclass
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


# ---------- ventilatory response to CO2 (Read's rebreathing method) ----------

#: a marker whose label contains this word (any case) marks the start of rebreathing
REBREATHING_WORD = "rebreath"


def is_rebreathing_marker(label: str) -> bool:
    return REBREATHING_WORD in label.lower()


@dataclass
class CO2Response:
    """The result of a rebreathing test, laid out as CHOP reports it.

    Baseline breaths (before the rebreathing marker) are averaged, not fitted; the
    MV-vs-EtCO2 line is fitted to the rebreathing breaths only. Without a marker every
    breath is fitted and ``split`` is False.
    """

    #: (EtCO2 mmHg, MV L/min) of the breaths before the rebreathing marker
    baseline: list[tuple[float, float]]
    #: (EtCO2 mmHg, MV L/min) of the breaths the line is fitted to
    rebreathing: list[tuple[float, float]]
    split: bool
    fit: Fit | None
    #: Pearson correlation coefficient, signed like the slope
    r: float
    #: EtCO2 (mmHg) where the fitted line reaches MV = 0
    x_intercept: float
    baseline_etco2: float
    baseline_mv: float

    @property
    def slope(self) -> float:
        return self.fit.slope if self.fit else NAN


def co2_response(points: Iterable[tuple[float, float, float]], rebreathing_start: float | None) -> CO2Response:
    """Ventilatory response from accepted breaths.

    ``points`` are (t, EtCO2 mmHg, MV L/min); ``rebreathing_start`` is on the same clock as
    t, or None when the start of rebreathing was not marked. Breaths with a missing value
    are skipped.
    """
    base: list[tuple[float, float]] = []
    reb: list[tuple[float, float]] = []
    for t, et, mv in points:
        if not (math.isfinite(et) and math.isfinite(mv)):
            continue
        (base if rebreathing_start is not None and t < rebreathing_start else reb).append((et, mv))
    fit = linear_fit([a for a, _ in reb], [b for _, b in reb])
    r = NAN
    x_int = NAN
    if fit:
        r = math.copysign(math.sqrt(fit.r2), fit.slope)
        if abs(fit.slope) > 1e-12:
            x_int = -fit.intercept / fit.slope
    return CO2Response(
        baseline=base,
        rebreathing=reb,
        split=rebreathing_start is not None,
        fit=fit,
        r=r,
        x_intercept=x_int,
        baseline_etco2=sum(a for a, _ in base) / len(base) if base else NAN,
        baseline_mv=sum(b for _, b in base) / len(base) if base else NAN,
    )


def co2_response_summary(res: CO2Response, fmt: Callable[[float, int], str]) -> str:
    """One line for under the MV-vs-EtCO2 plot (desktop and browser)."""
    if not res.fit:
        need = "needs at least 3 rebreathing breaths" if res.split else "needs at least 3 breaths"
        return f"MV vs EtCO2 · {need}"
    parts = [f"slope {fmt(res.fit.slope, 2)} L/min/mmHg · r {fmt(res.r, 2)} · x-int {fmt(res.x_intercept, 1)} mmHg · n {len(res.rebreathing)}"]
    if res.split:
        if res.baseline:
            parts.append(f"baseline {fmt(res.baseline_etco2, 1)} mmHg, {fmt(res.baseline_mv, 1)} L/min (n {len(res.baseline)})")
        else:
            parts.append("no baseline breaths in this window")
    else:
        parts.append("mark “Rebreathing” to set the baseline apart")
    return " · ".join(parts)
