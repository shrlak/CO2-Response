"""Synthetic breathing: half-sine inspiratory flow, exponential expiratory flow, and a
capnogram with a fast upstroke and a slowly rising alveolar plateau whose end value is
exactly the breath's end-tidal CO2."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Callable

SCENARIOS: list[tuple[str, str]] = [
    ("normal", "Normal breathing"),
    ("co2Ramp", "CO2 ramp (rebreathing)"),
    ("apnea", "Apnea"),
    ("co2Disconnect", "CO2 sensor disconnected"),
    ("noData", "Device stops sending"),
    ("noisy", "Noisy signal + line errors"),
    ("artifacts", "Spikes + partial breaths"),
]


def mulberry32(seed: int) -> Callable[[], float]:
    """Small seeded PRNG (mulberry32) so simulated data is reproducible in tests."""
    a = seed & 0xFFFFFFFF
    m = 0xFFFFFFFF

    def rand() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & m
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & m
        t = (t ^ (t + (((t ^ (t >> 7)) * (t | 61)) & m))) & m
        return ((t ^ (t >> 14)) & m) / 4294967296

    return rand


@dataclass
class SimParams:
    #: breaths/min
    rr: float = 15
    #: baseline end-tidal CO2, mmHg
    etco2: float = 38
    #: baseline tidal volume, mL
    vt_ml: float = 500
    #: inspiratory fraction of the breath (1:2 I:E = 1/3)
    insp_fraction: float = 1 / 3
    #: CO2 ramp speed in mmHg/min and the level it stops at
    ramp_mm_hg_per_min: float = 15
    ramp_max_mm_hg: float = 62
    #: pneumotach constant used to turn flow into ΔP, L/min per Pa
    k_pneumo: float = 0.2
    #: barometric pressure the "true" gas is at, mmHg (converts mmHg → %)
    barometric_mm_hg: float = 760
    scenario: str = "normal"
    seed: int = 1


@dataclass
class PhysSample:
    #: CO2 in % at the sensor, or None when the sensor is disconnected
    co2_pct: float | None
    #: flow-sensor reading, L/min (positive = inspiration)
    flow_lpm: float
    #: pneumotach differential pressure, Pa
    pneumo_pa: float


class PhysioModel:
    def __init__(self, **params) -> None:
        self.params = SimParams(**params)
        self._rand = mulberry32(self.params.seed)
        self._t = 0.0
        self._breath_start = 0.0
        self._period = 4.0
        self._ti = 4 / 3
        self._et = 38.0
        self._prev_end = 38.0
        self._target = 38.0
        self._qpk = 0.0
        self._qe = 0.0
        self._tau = 0.5
        self._apnea_co2 = 0.0
        self._shallow = False
        self._reset_breathing()

    def set_params(self, **patch) -> None:
        scenario_changed = "scenario" in patch and patch["scenario"] != self.params.scenario
        self.params = dataclasses.replace(self.params, **patch)
        if scenario_changed or "etco2" in patch:
            self._target = self.params.etco2

    @property
    def target_etco2(self) -> float:
        """Current target end-tidal CO2 (rises during the ramp scenario)."""
        return self._target

    def _reset_breathing(self) -> None:
        self._et = self.params.etco2
        self._prev_end = self._et
        self._target = self.params.etco2
        self._plan_breath()

    def _plan_breath(self) -> None:
        p = self.params
        drive = max(0, self._et - p.etco2 - 2)
        rr = p.rr + 0.2 * drive
        self._period = 60 / rr
        self._ti = self._period * p.insp_fraction
        vt_l = (p.vt_ml / 1000) * (1 + 0.04 * max(0, self._et - p.etco2))
        self._qpk = (vt_l * 60 * math.pi) / (2 * self._ti)
        te = self._period - self._ti
        self._tau = te / 5
        c = 0.06
        self._qe = (vt_l * 60) / ((self._tau * self._tau) / (self._tau + c))
        # "artifacts": about one breath in seven is a partial breath that never reaches
        # an alveolar plateau, so its CO2 peak under-reads (a classic capnography artifact)
        self._shallow = p.scenario == "artifacts" and self._rand() < 0.15

    def _noise(self, scale: float) -> float:
        return (self._rand() - 0.5) * 2 * scale

    def step(self, dt: float) -> PhysSample:
        """Advance by dt seconds and return the sensor values at the new time."""
        p = self.params
        self._t += dt
        if p.scenario == "co2Ramp":
            self._target = min(p.ramp_max_mm_hg, self._target + (p.ramp_mm_hg_per_min / 60) * dt)

        noisy = 5 if p.scenario == "noisy" else 1
        if p.scenario == "apnea":
            self._apnea_co2 *= math.exp(-dt / 3)
            flow = 0.0
            co2 = self._apnea_co2
            self._breath_start = self._t
        else:
            x = self._t - self._breath_start
            if x >= self._period:
                self._breath_start += self._period
                self._prev_end = self._et
                self._et += (self._target - self._et) * 0.5
                self._plan_breath()
                x = self._t - self._breath_start
            if x < self._ti:
                flow = self._qpk * math.sin((math.pi * x) / self._ti)
                co2 = self._prev_end * math.exp(-x / 0.05)
            else:
                e = x - self._ti
                te = self._period - self._ti
                flow = -self._qe * math.exp(-e / self._tau) * (1 - math.exp(-e / 0.06))
                up = 1 / (1 + math.exp(-(e - 0.28) / 0.04))
                co2 = up * (self._et - 3 + 3 * (e / te))
            if self._shallow:
                flow *= 0.35
                co2 = co2 if x < self._ti else 0.6 * self._et * ((x - self._ti) / (self._period - self._ti)) ** 2
            self._apnea_co2 = co2

        flow_lpm = flow + self._noise(0.6 * noisy)
        pneumo_pa = (flow + self._noise(0.4 * noisy)) / p.k_pneumo
        co2_mm = max(0, co2 + self._noise(0.25 * noisy))
        co2_pct = None if p.scenario == "co2Disconnect" else (co2_mm / p.barometric_mm_hg) * 100
        return PhysSample(co2_pct, flow_lpm, pneumo_pa)
