"""Breath detection on pneumotach flow, falling back to the capnogram when there is no flow signal."""

from __future__ import annotations

import math
from dataclasses import dataclass

NAN = float("nan")


@dataclass
class BreathOptions:
    #: flow must pass ± this (L/min) to count as inspiration/expiration; 0.05 L/s = 3 L/min
    deadband_lpm: float = 3
    #: breaths shorter than this (s) are treated as noise
    min_breath_sec: float = 1
    #: a flow dropout shorter than this (s) is bridged with the last valid value. The
    #: usual cause is the sensor clipping at the end of its range on a hard breath;
    #: without the bridge every clipped breath would be lost.
    hold_sec: float = 0.5


@dataclass
class Breath:
    #: start of this breath's inspiration (device clock, s)
    t_start: float
    #: start of the next inspiration, when the breath was finalized (s)
    t_end: float
    period_sec: float
    #: peak CO2 in the expiratory phase, mmHg (NaN if no CO2 signal)
    etco2: float
    #: minimum CO2 during inspiration, mmHg
    fico2: float
    #: inspired volume, mL (NaN in CO2-only mode)
    vt_ml: float
    #: peak inspiratory flow, L/min (NaN in CO2-only mode)
    peak_insp_lpm: float
    #: which signal found the breath: 'flow' or 'co2'
    detected_by: str
    #: fraction of the expiration with CO2 at ≥ 90 % of EtCO2 (an alveolar plateau)
    plateau: float = NAN
    #: median expiratory CO2 above half its peak, mmHg: the plateau level, which noise
    #: barely moves (unlike the peak, EtCO2)
    level: float = NAN
    #: noise during the breath (σ of the unfiltered signal): CO2 mmHg, flow L/min
    noise_co2: float = NAN
    noise_flow: float = NAN
    #: 'ok', 'noisy', 'artifact' or 'outlier' (see denoise.py); only 'ok' is interpreted
    quality: str = "ok"
    reason: str = ""
    #: the breath's number in the recording, if one was running
    rec_n: int | None = None
    #: expired volume, mL (NaN in CO2-only mode). It should match ``vt_ml``; a
    #: difference means a leak or a drifting flow zero
    vte_ml: float = NAN
    #: how long after expiration began the CO2 rose past half of the last EtCO2, s.
    #: Dead-space washout takes 0.1-0.3 s; more is the CO2 sensor's delay
    co2_lag_sec: float = NAN
    #: seconds of the inspiration during which the flow signal was out of range and
    #: bridged: the inspired volume under-reads (see ``BreathOptions.hold_sec``)
    clipped_sec: float = 0.0

    @property
    def mv_lpm(self) -> float:
        """This breath's own minute ventilation, L/min: VT x 60 / its period."""
        if not (math.isfinite(self.vt_ml) and self.period_sec > 0):
            return NAN
        return self.vt_ml / 1000 * 60 / self.period_sec


class BreathDetector:
    """A breath runs from one inspiration start to the next and is reported when the
    next inspiration begins."""

    def __init__(self, opts: BreathOptions | None = None) -> None:
        self.opts = opts or BreathOptions()
        self._mode: str | None = None
        self._phase = "unknown"
        self._start: float | None = None
        self._last_t: float | None = None
        self._last_flow = 0.0
        self._vt_l = 0.0
        self._peak = 0.0
        self._fi = NAN
        self._et = NAN
        self._last_et = NAN
        self._exp_co2: list[float] = []
        self._vte_l = 0.0
        self._t_exp: float | None = None
        self._lag = NAN
        self._lag_open = False
        self._flow_t: float | None = None
        self._clip_insp = 0.0
        self._clip_exp = False

    def reset(self) -> None:
        self._mode = None
        self._phase = "unknown"
        self._start = None
        self._last_t = None

    def _begin_breath(self, t: float) -> Breath | None:
        out = None
        if self._start is not None and self._mode is not None:
            flow_mode = self._mode == "flow"
            exp = self._exp_co2
            plateau = level = NAN
            if exp and math.isfinite(self._et):
                plateau = sum(1 for c in exp if c >= 0.9 * self._et) / len(exp)
                top = sorted(c for c in exp if c >= 0.5 * self._et)
                level = top[len(top) // 2] if top else NAN
            out = Breath(
                t_start=self._start,
                t_end=t,
                period_sec=t - self._start,
                etco2=self._et,
                fico2=self._fi,
                vt_ml=self._vt_l * 1000 if flow_mode else NAN,
                peak_insp_lpm=self._peak if flow_mode else NAN,
                detected_by=self._mode,
                plateau=plateau,
                level=level,
                # an expiration that went out of range has no trustworthy volume
                vte_ml=self._vte_l * 1000 if flow_mode and not self._clip_exp else NAN,
                co2_lag_sec=self._lag if flow_mode else NAN,
                clipped_sec=self._clip_insp,
            )
            if math.isfinite(self._et):
                self._last_et = self._et
        self._start = t
        self._phase = "insp"
        self._vt_l = 0.0
        self._peak = 0.0
        self._fi = NAN
        self._et = NAN
        self._exp_co2 = []
        self._vte_l = 0.0
        self._t_exp = None
        self._lag = NAN
        self._lag_open = False
        self._clip_insp = 0.0
        self._clip_exp = False
        return out

    def _co2_rise_level(self) -> float:
        """The CO2 level that marks the capnogram's upstroke: half of the last EtCO2."""
        ref = self._last_et if math.isfinite(self._last_et) else 20
        return max(4, 0.5 * ref)

    def push(self, t: float, flow: float, co2: float) -> Breath | None:
        """Feed one sample.

        t: device time (s); flow: pneumotach flow (L/min, NaN when invalid);
        co2: CO2 (mmHg, NaN when invalid). Returns the breath that just finished, if any.
        """
        held = False
        if math.isfinite(flow):
            self._flow_t = t
        elif self._mode == "flow" and self._flow_t is not None and 0 <= t - self._flow_t <= self.opts.hold_sec:
            flow = self._last_flow  # a short dropout: carry on from the last valid value
            held = True
        mode = "flow" if math.isfinite(flow) else "co2" if math.isfinite(co2) else None
        if mode != self._mode:
            self.reset()
            self._mode = mode
        if mode is None:
            return None

        dt = 0.0 if self._last_t is None else max(0.0, min(0.5, t - self._last_t))
        self._last_t = t
        if held:
            if self._phase == "insp":
                self._clip_insp += dt
            else:
                self._clip_exp = True
        long_enough = self._start is None or t - self._start >= self.opts.min_breath_sec
        breath = None

        if mode == "flow":
            db = self.opts.deadband_lpm
            if flow > db and self._phase != "insp" and long_enough:
                breath = self._begin_breath(t)
            elif flow < -db and self._phase == "insp":
                self._phase = "exp"
                self._t_exp = t
                self._lag_open = False
            if self._phase == "insp":
                self._vt_l += ((max(0.0, flow) + max(0.0, self._last_flow)) / 2 / 60) * dt
                self._peak = max(self._peak, flow)
            if self._start is not None:
                # expired volume: every sample of the breath with flow outward
                self._vte_l += ((max(0.0, -flow) + max(0.0, -self._last_flow)) / 2 / 60) * dt
            if self._phase == "exp" and math.isnan(self._lag) and math.isfinite(co2):
                # timed from the start of expiration to the capnogram's next upstroke.
                # A late CO2 signal may still show the previous plateau at first; the
                # upstroke is the rise that follows a low reading.
                if co2 <= self._co2_rise_level():
                    self._lag_open = True
                elif self._lag_open and self._t_exp is not None:
                    self._lag = t - self._t_exp
                    self._lag_open = False
            self._last_flow = flow
        else:
            ref = self._last_et if math.isfinite(self._last_et) else 20
            hi = max(4, 0.5 * ref)
            lo = max(2, 0.3 * ref)
            if co2 > hi and self._phase != "exp":
                self._phase = "exp"
            elif co2 < lo and self._phase == "exp" and long_enough:
                breath = self._begin_breath(t)

        if math.isfinite(co2):
            if self._phase == "exp":
                self._et = max(self._et, co2) if math.isfinite(self._et) else co2
                if len(self._exp_co2) < 20000:
                    self._exp_co2.append(co2)
            if self._phase == "insp":
                self._fi = min(self._fi, co2) if math.isfinite(self._fi) else co2
        return breath
