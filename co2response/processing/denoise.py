"""Removing noise before anything is interpreted.

Three stages, in the order the data meets them:

1. ``Despiker`` (every sample, every channel, on the raw volts): a centered Hampel
   filter. A sample that sits far outside the median of its neighbours (a glitchy
   ADC conversion, electrical interference) is replaced by that median. Real fast
   changes such as the capnogram upstroke are monotonic over the window, so the
   centre sample *is* the median and passes untouched. Costs a delay of
   ``half_window`` samples (30 ms at 100 Hz).

2. The low-pass filter in ``filter.py`` (broadband high-frequency noise).

3. ``BreathScreen`` (every breath, before it is used): each breath gets a quality:

   - ``noisy``    the signal noise during the breath is too large next to the
                  breath itself (signal-to-noise below 8) to trust its numbers
   - ``artifact`` impossible values, no alveolar plateau (a partial breath whose
                  EtCO2 under-reads), or inspiratory flow beyond the sensor's range
                  (its volume under-reads)
   - ``outlier``  an isolated EtCO2 jump: far from both the breath before and the
                  breath after, in the same direction. Decided one breath later,
                  because it needs the next breath; a real step change is followed
                  by more breaths at the new level and is kept.
   - ``ok``

   Only ``ok`` breaths feed the displayed numbers, the trend and response graphs,
   the review charts and the analysis exports. Rejected breaths are still recorded,
   with their quality, so nothing is thrown away.
"""

from __future__ import annotations

import collections
import math
from typing import Sequence

QUALITIES = ("ok", "noisy", "artifact", "outlier")
NAN = float("nan")

#: σ of white noise from the median |second difference|: median|N(0, 6σ²)| = 0.6745·√6·σ
D2_TO_SIGMA = 1.6522


def _median(xs: Sequence[float]) -> float:
    v = sorted(xs)
    n = len(v)
    if not n:
        return NAN
    m = n // 2
    return v[m] if n % 2 else (v[m - 1] + v[m]) / 2


def hampel(window: Sequence[float], k: float = 3.5, min_dev: float = 0.02, center: int | None = None) -> float:
    """The cleaned value of ``window[center]`` (default: the middle).

    It is replaced by the window median when it is further from it than ``k`` robust
    standard deviations (1.4826 × median absolute deviation) and than ``min_dev``.
    NaN samples are ignored; a NaN stays NaN.
    """
    c = window[len(window) // 2 if center is None else center]
    if not math.isfinite(c):
        return c
    fin = [x for x in window if math.isfinite(x)]
    med = _median(fin)
    mad = _median([abs(x - med) for x in fin])
    return med if abs(c - med) > max(k * 1.4826 * mad, min_dev) else c


class Despiker:
    """Hampel filter over ``2·half_window + 1`` samples for every channel at once.

    ``push`` takes one sample and returns the cleaned sample from ``half_window``
    samples ago (with whatever travelled with it), or None for the first
    ``half_window`` pushes. ``flush`` returns the samples still held back. Every
    sample comes out exactly once; near the ends the window is one-sided.
    """

    def __init__(self, half_window: int = 3, k: float = 3.5, min_dev: float = 0.02) -> None:
        self.h = half_window
        self.k = k
        self.min_dev = min_dev
        self._buf: collections.deque = collections.deque()
        self._next = 0  # index in _buf of the next sample to output
        self.replaced = 0

    def _emit(self):
        i = self._next
        lo = max(0, i - self.h)
        win = [self._buf[j] for j in range(lo, min(len(self._buf), i + self.h + 1))]
        tag, raw = self._buf[i]
        clean = []
        for ch in range(len(raw)):
            col = [s[1][ch] if ch < len(s[1]) else NAN for s in win]
            y = hampel(col, self.k, self.min_dev, i - lo)
            if y != raw[ch] and math.isfinite(raw[ch]):
                self.replaced += 1
            clean.append(y)
        self._next += 1
        while self._next > self.h:  # keep only the h samples the next window needs
            self._buf.popleft()
            self._next -= 1
        return tag, raw, clean

    def push(self, values: Sequence[float], tag=None):
        """Returns (tag, raw values, cleaned values) of a held-back sample, or None."""
        self._buf.append((tag, list(values)))
        if len(self._buf) - 1 - self._next >= self.h:
            return self._emit()
        return None

    def flush(self) -> list:
        """The samples still held back (at the end of a stream)."""
        out = []
        while self._next < len(self._buf):
            out.append(self._emit())
        self._buf.clear()
        self._next = 0
        return out

    def reset(self) -> None:
        self._buf.clear()
        self._next = 0


class NoiseMeter:
    """Collects |second differences| of one signal, for a robust noise estimate over
    a stretch (one breath). Slow breathing barely moves second differences, so the
    median measures the noise, not the breathing."""

    def __init__(self) -> None:
        self._d2: list[float] = []
        self._p1 = NAN
        self._p2 = NAN

    def push(self, y: float) -> None:
        if math.isfinite(y) and math.isfinite(self._p1) and math.isfinite(self._p2):
            self._d2.append(abs(y - 2 * self._p1 + self._p2))
        self._p2, self._p1 = self._p1, y

    def take(self) -> float:
        """Noise σ since the last call (NaN if too few samples), and start again."""
        d2, self._d2 = self._d2, []
        return _median(d2) / D2_TO_SIGMA if len(d2) >= 10 else NAN


class BreathScreen:
    """Decides each breath's quality before it is interpreted. See the module docstring."""

    SNR_MIN = 8.0  # normal signals are near 250, the simulator's "noisy" scenario near 50
    CO2_NOISE_FLOOR = 1.0  # mmHg: below this the CO2 signal is never called noisy
    FLOW_NOISE_FLOOR = 1.5  # L/min
    ETCO2_RANGE = (-2.0, 120.0)  # mmHg
    PLATEAU_MIN = 0.1  # fraction of expiration at ≥ 90 % of EtCO2
    OUTLIER_MIN = 5.0  # mmHg
    OUTLIER_K = 3.0  # robust SDs of the recent breath-to-breath spread
    CLIP_MAX_SEC = 0.02  # inspiratory flow out of range for longer than this: VT is not trusted

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._recent: list = []  # interpretable breaths (not noisy/artifact), newest last

    def reasons(self, b) -> tuple[str, str]:
        """(quality, explanation) for a just-finished breath, from that breath alone."""
        et, fi = b.etco2, b.fico2
        # noise first: a noisy signal also distorts the shape checks below
        if math.isfinite(et) and math.isfinite(b.noise_co2):
            # signal size from the plateau level, not the peak: noise inflates the peak
            top = b.level if math.isfinite(getattr(b, "level", NAN)) else et
            amp = top - (max(0.0, fi) if math.isfinite(fi) else 0.0)
            if b.noise_co2 > max(self.CO2_NOISE_FLOOR, amp / self.SNR_MIN):
                return "noisy", f"CO2 noise {b.noise_co2:.1f} mmHg"
        if b.detected_by == "flow" and math.isfinite(b.noise_flow):
            peak = b.peak_insp_lpm if math.isfinite(b.peak_insp_lpm) else 0.0
            if b.noise_flow > max(self.FLOW_NOISE_FLOOR, peak / self.SNR_MIN):
                return "noisy", f"flow noise {b.noise_flow:.1f} L/min"
        if math.isfinite(et) and not (self.ETCO2_RANGE[0] <= et <= self.ETCO2_RANGE[1]):
            return "artifact", f"EtCO2 {et:.0f} mmHg is outside 0–120"
        if b.detected_by == "flow" and math.isfinite(b.vt_ml) and b.vt_ml < 1:
            return "artifact", "no inspiration"
        if b.detected_by == "flow" and getattr(b, "clipped_sec", 0.0) > self.CLIP_MAX_SEC:
            return "artifact", f"flow out of the sensor's range for {b.clipped_sec:.2f} s of inspiration (volume under-reads)"
        if b.detected_by == "flow" and math.isfinite(et) and et > 5 and math.isfinite(b.plateau) and b.plateau < self.PLATEAU_MIN:
            return "artifact", "no alveolar plateau (partial breath)"
        return "ok", ""

    def screen(self, b):
        """Set ``b.quality``. Returns the *previous* breath if it has just turned out to
        be an isolated outlier (its quality is changed to "outlier"), else None."""
        if not self.enabled:
            b.quality, b.reason = "ok", ""
            return None
        b.quality, b.reason = self.reasons(b)
        if b.quality != "ok":
            return None
        flagged = None
        r = self._recent
        if len(r) >= 2 and r[-1].quality == "ok":
            prev, before = r[-1], r[-2]
            ets = [x.etco2 for x in r[-8:] if math.isfinite(x.etco2)]
            if math.isfinite(b.etco2) and all(math.isfinite(x) for x in (prev.etco2, before.etco2)) and len(ets) >= 3:
                steps = [abs(ets[i] - ets[i - 1]) for i in range(1, len(ets))]
                thr = max(self.OUTLIER_MIN, self.OUTLIER_K * 1.4826 * _median(steps))
                d1, d2 = prev.etco2 - before.etco2, prev.etco2 - b.etco2
                if abs(d1) > thr and abs(d2) > thr and (d1 > 0) == (d2 > 0):
                    prev.quality = "outlier"
                    prev.reason = f"isolated EtCO2 {prev.etco2:.0f} mmHg between {before.etco2:.0f} and {b.etco2:.0f}"
                    flagged = prev
        r.append(b)
        if flagged is not None:
            r.remove(flagged)
        del r[:-12]
        return flagged

    def reset(self) -> None:
        self._recent = []


def screen_series(etco2: Sequence[float], quality: Sequence[str] | None = None) -> list[str]:
    """Breath qualities for a recorded series that has none (files from earlier
    versions): out-of-range EtCO2 is an artifact and isolated jumps are outliers.
    Noise and plateau can only be judged live, from the samples."""
    from types import SimpleNamespace

    scr = BreathScreen()
    out = list(quality) if quality is not None else [""] * len(etco2)
    breaths = []
    for i, et in enumerate(etco2):
        b = SimpleNamespace(etco2=et, fico2=NAN, vt_ml=NAN, plateau=NAN, noise_co2=NAN, noise_flow=NAN, peak_insp_lpm=NAN, detected_by="co2", quality="ok", reason="", i=i)
        if out[i] in ("noisy", "artifact", "outlier"):
            continue
        flagged = scr.screen(b)
        breaths.append(b)
        if flagged is not None:
            out[flagged.i] = "outlier"
        out[i] = b.quality if b.quality != "ok" else (out[i] or "ok")
    return out
