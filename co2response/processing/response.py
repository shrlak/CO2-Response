"""The ventilatory response to CO2 from a Read rebreathing test.

The clinical readout is the slope of minute ventilation against end-tidal CO2. The
CHOP sleep lab (the method our mentors use) computes both **breath by breath**, over
the **rebreathing period only**, after leaving out the deep breaths the subject is
coached to take when the valve is turned to the bag. This module does that selection
and the baseline checks; ``metrics.linear_fit`` does the regression.

Pure: no UI and no clock. Times are whatever the caller uses (the engine uses wall s).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .metrics import Fit, linear_fit, median

NAN = float("nan")


def breath_mv(vt_ml: float, period_sec: float) -> float:
    """One breath's minute ventilation, L/min: VT × 60 / breath period (what CHOP's
    breath-by-breath program computes)."""
    if not (math.isfinite(vt_ml) and math.isfinite(period_sec)) or period_sec <= 0:
        return NAN
    return vt_ml / 1000 * 60 / period_sec


@dataclass
class ResponsePoint:
    #: when the breath ended (caller's clock, s)
    t: float
    etco2: float
    #: this breath's own minute ventilation, L/min (see ``breath_mv``)
    mv: float
    quality: str = "ok"


@dataclass
class ResponseResult:
    """MV-against-EtCO2 fit for one window of breaths."""

    #: 'rebreathing' (from a marked rebreathing period) or 'window' (every clean breath shown)
    basis: str
    fit: Fit | None
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    #: clean breaths at the start of rebreathing that were left out (coached breaths)
    skipped: int = 0
    #: breaths in the window left out by the noise screen
    rejected: int = 0


def _usable(p: ResponsePoint) -> bool:
    return p.quality == "ok" and math.isfinite(p.etco2) and math.isfinite(p.mv)


def response_fit(points: Iterable[ResponsePoint], t0: float, t1: float, skip: int = 0, basis: str = "rebreathing") -> ResponseResult:
    """Fit MV against EtCO2 for the clean breaths that ended in [t0, t1], leaving out
    the first ``skip`` clean ones."""
    inside = [p for p in points if t0 <= p.t <= t1]
    clean = [p for p in inside if _usable(p)]
    skip = max(0, int(skip))
    used = clean[skip:]
    xy = sorted(((p.etco2, p.mv) for p in used), key=lambda a: a[0])
    xs = [a for a, _ in xy]
    ys = [b for _, b in xy]
    return ResponseResult(basis, linear_fit(xs, ys), xs, ys, min(skip, len(clean)), sum(1 for p in inside if p.quality != "ok"))


@dataclass
class Baseline:
    """Room-air breathing before the switch to the bag."""

    #: median EtCO2 and mean per-breath MV of the clean breaths in the window
    etco2: float = NAN
    mv: float = NAN
    n: int = 0
    #: max − min EtCO2 over the last ``n_stable`` clean breaths, mmHg
    etco2_range: float = NAN
    #: steady enough to start rebreathing (see ``baseline``)
    stable: bool = False


def baseline(points: Sequence[ResponsePoint], now: float, window_sec: float = 60, n_stable: int = 6, max_range_mm_hg: float = 3) -> Baseline:
    """Baseline EtCO2 and MV over the trailing ``window_sec``, and whether it is stable:
    at least ``n_stable`` clean breaths in the window whose last ``n_stable`` EtCO2
    values lie within ``max_range_mm_hg`` of each other (DTM 6.2: switch to the bag
    "once stable baseline EtCO2 has been achieved")."""
    recent = [p for p in points if now - window_sec <= p.t <= now and _usable(p)]
    if not recent:
        return Baseline()
    last = [p.etco2 for p in recent[-n_stable:]]
    rng = max(last) - min(last)
    mvs = [p.mv for p in recent]
    return Baseline(
        etco2=median([p.etco2 for p in recent]),
        mv=sum(mvs) / len(mvs),
        n=len(recent),
        etco2_range=rng,
        stable=len(recent) >= n_stable and rng <= max_range_mm_hg,
    )


def describe(res: ResponseResult, test_number: int = 0) -> str:
    """One line for the Ventilatory response tab (desktop and browser)."""
    f = res.fit
    left = f" · {res.rejected} noisy breath{'s' if res.rejected != 1 else ''} left out" if res.rejected else ""
    if res.basis == "rebreathing":
        head = f"Rebreathing {test_number}" if test_number else "Rebreathing"
        skip = f" · {res.skipped} coached breath{'s' if res.skipped != 1 else ''} left out" if res.skipped else ""
    else:
        head = "All clean breaths (Start rebreathing fits one test)"
        skip = ""
    if f is None:
        return f"{head} · MV vs EtCO2 needs at least 3 breaths{skip}{left}"
    ci = f" (95 % CI ± {f.slope_ci95:.2f})" if math.isfinite(f.slope_ci95) else ""
    # the x-intercept means something only for a rising response
    xi = f" · x-int {f.x_intercept:.1f} mmHg" if f.slope > 0 and math.isfinite(f.x_intercept) else ""
    return f"{head} · slope {f.slope:.2f} L/min/mmHg{ci} · r {f.r:.2f}{xi} · n {f.n}{skip}{left}"
