"""How far the CO2 signal lags the flow signal.

A sidestream capnograph pulls gas down a sample line, so its CO2 reaches the analyser
one to several seconds after the flow that carried it passed the pneumotach (a
mainstream sensor lags much less). Breaths are found on the flow signal, so unless CO2
is shifted back by that lag, each expiration's CO2 peak lands in the next breath's
inspiration: EtCO2 is taken from the wrong breath and FiCO2 reads the tail of the
previous plateau. The pipeline shifts CO2 by ``ProcessingOptions.co2_delay_ms``; this
module measures the lag from ordinary breathing.

The estimate is the median, over breaths, of the time from the start of expiration
(flow falls through −deadband) to the CO2 rising through halfway between its inspired
and end-tidal levels. It includes the anatomical dead-space washout (phase I of the
capnogram) as well as the transport delay, so it slightly over-corrects, which is the
harmless direction: under-correction is what moves CO2 peaks into the wrong breath.
It is only valid while the lag is shorter than one breath.
"""

from __future__ import annotations

import math
from typing import Sequence


def _pct(sorted_v: list[float], q: float) -> float:
    i = min(len(sorted_v) - 1, max(0, int(round(q * (len(sorted_v) - 1)))))
    return sorted_v[i]


def estimate_co2_delay(t: Sequence[float], flow: Sequence[float], co2: Sequence[float], deadband_lpm: float = 3, max_delay_sec: float = 6) -> tuple[float, int] | None:
    """Return (median lag in s, breaths used), or None with fewer than 3 usable breaths.

    t: sample times (s); flow: pneumotach flow (L/min, + = inspiration); co2: CO2 (mmHg).
    """
    n = min(len(t), len(flow), len(co2))
    vals = sorted(c for c in co2[:n] if math.isfinite(c))
    if n < 10 or len(vals) < 10:
        return None
    lo, hi = _pct(vals, 0.1), _pct(vals, 0.9)
    if hi - lo < 4:  # no capnogram to time (mmHg)
        return None
    mid = (lo + hi) / 2

    exp_starts: list[int] = []
    phase = "unknown"
    for i in range(n):
        f = flow[i]
        if not math.isfinite(f):
            continue
        if f > deadband_lpm:
            phase = "insp"
        elif f < -deadband_lpm and phase == "insp":
            phase = "exp"
            exp_starts.append(i)

    lags: list[float] = []
    for i0 in exp_starts:
        # the first upward crossing of the midpoint after expiration starts; CO2 may
        # still be high there (the previous plateau, delayed), so wait for it to dip
        below = False
        for j in range(i0, n):
            if t[j] - t[i0] > max_delay_sec:
                break
            c = co2[j]
            if not math.isfinite(c):
                continue
            if c < mid:
                below = True
            elif below:
                lags.append(t[j] - t[i0])
                break
    if len(lags) < 3:
        return None
    lags.sort()
    m = len(lags) // 2
    med = lags[m] if len(lags) % 2 else (lags[m - 1] + lags[m]) / 2
    return med, len(lags)
