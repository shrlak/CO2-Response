"""Synthetic breathing: half-sine inspiratory flow, exponential expiratory flow, and a
capnogram with a fast upstroke and a slowly rising alveolar plateau whose end value is
exactly the breath's end-tidal CO2.

Every value the monitor shows can be set here (see ``co2response/sim/controls.py`` for
the simulation panel that drives them): end-tidal and inspired CO2, rate, tidal volume,
I:E ratio (and so peak inspiratory flow), the capnogram's shape, a CO2 challenge, apnea,
unplugged sensors, a stalled data stream, signal noise and a miscalibrated flow sensor.
The defaults reproduce the original model exactly, so recorded sample data and tests
stay byte-for-byte reproducible."""

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
    #: inspired CO2 (the baseline between breaths), mmHg
    fico2: float = 0
    #: airway obstruction, 0–100 %: 0 = square capnogram, 100 = "shark fin" (bronchospasm, COPD)
    obstruction_pct: float = 0
    #: how strongly breathing responds to raised CO2 during a CO2 challenge (1 = normal, 0 = none)
    drive: float = 1
    #: sensor noise multiplier (1 = a quiet bench setup)
    noise: float = 1
    #: % of samples with a single-sample ADC spike on one channel
    spike_pct: float = 0
    #: % of breaths that are partial (no alveolar plateau)
    partial_pct: float = 0
    #: % of serial lines lost (one third) or corrupted (two thirds)
    line_error_pct: float = 0
    #: the flow sensor reads gain × true flow + offset (a miscalibrated or drifting sensor)
    flow_gain: float = 1
    flow_offset_lpm: float = 0
    #: how much of the way to a new end-tidal CO2 each breath goes: 0.5 halves the gap
    #: every breath (the original, gradual approach over about five breaths); 1 reaches
    #: it on the next breath, or on the breath being drawn if that is still inspiring
    settle: float = 0.5
    #: events, on top of the scenario
    ramp: bool = False
    apnea: bool = False
    co2_disconnected: bool = False
    flow_disconnected: bool = False
    pneumo_disconnected: bool = False
    stop_data: bool = False

    # the legacy scenarios switch the same behaviours on
    @property
    def ramp_on(self) -> bool:
        return self.ramp or self.scenario == "co2Ramp"

    @property
    def apnea_on(self) -> bool:
        return self.apnea or self.scenario == "apnea"

    @property
    def co2_disconnected_on(self) -> bool:
        return self.co2_disconnected or self.scenario == "co2Disconnect"

    @property
    def stop_data_on(self) -> bool:
        return self.stop_data or self.scenario == "noData"


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
        prev = self.params
        scenario_changed = "scenario" in patch and patch["scenario"] != prev.scenario
        self.params = dataclasses.replace(prev, **patch)
        if scenario_changed or "etco2" in patch or self.params.ramp_on != prev.ramp_on:
            self._target = self.params.etco2
            # the CO2 of an inspiration does not depend on this breath's EtCO2, so a breath
            # that is still inspiring can take the new value without a step in the trace
            if self.params.settle >= 1 and not self.params.apnea_on and self._t - self._breath_start < self._ti:
                self._et = self._target
        if self.params.apnea_on and not prev.apnea_on:
            self._apnea_co2 = max(self._apnea_co2, self.params.fico2)

    @property
    def target_etco2(self) -> float:
        """Current target end-tidal CO2 (rises during the ramp scenario)."""
        return self._target

    def _reset_breathing(self) -> None:
        self._et = self.params.etco2
        self._prev_end = self._et
        self._target = self.params.etco2
        self._plan_breath()

    def expected(self) -> dict[str, float]:
        """What the monitor should read for the breath now being drawn: the values the
        simulation panel compares with the monitor's own measurements."""
        p = self.params
        if p.apnea_on:
            return {"etco2": math.nan, "fico2": p.fico2, "rr": 0.0, "vt": math.nan, "mv": 0.0, "peak": math.nan}
        rr = 60 / self._period
        vt_ml = self._qpk * 2 * self._ti / (60 * math.pi) * 1000
        # peak expiratory flow: the maximum of exp(-e/tau) * (1 - exp(-e/c)), c = 0.06 s
        c = 0.06
        e_pk = c * math.log((c + self._tau) / c)
        q_exp = self._qe * math.exp(-e_pk / self._tau) * (1 - math.exp(-e_pk / c))
        return {
            "etco2": self._et,
            "fico2": p.fico2,
            "rr": rr,
            "vt": vt_ml,
            "mv": rr * vt_ml / 1000,
            "peak": p.flow_gain * self._qpk + p.flow_offset_lpm,
            # true flows, for checking the sensors' range
            "insp_flow": self._qpk,
            "exp_flow": q_exp,
        }

    def _plan_breath(self) -> None:
        p = self.params
        drive = max(0, self._et - p.etco2 - 2) * p.drive
        rr = p.rr + 0.2 * drive
        self._period = 60 / rr
        self._ti = self._period * p.insp_fraction
        vt_l = (p.vt_ml / 1000) * (1 + 0.04 * max(0, self._et - p.etco2) * p.drive)
        self._qpk = (vt_l * 60 * math.pi) / (2 * self._ti)
        te = self._period - self._ti
        self._tau = te / 5
        c = 0.06
        self._qe = (vt_l * 60) / ((self._tau * self._tau) / (self._tau + c))
        # "artifacts": about one breath in seven is a partial breath that never reaches
        # an alveolar plateau, so its CO2 peak under-reads (a classic capnography artifact)
        partial = max(0.15 if p.scenario == "artifacts" else 0, p.partial_pct / 100)
        self._shallow = partial > 0 and self._rand() < partial

    def _noise(self, scale: float) -> float:
        return (self._rand() - 0.5) * 2 * scale

    def step(self, dt: float) -> PhysSample:
        """Advance by dt seconds and return the sensor values at the new time."""
        p = self.params
        self._t += dt
        if p.ramp_on:
            self._target = min(p.ramp_max_mm_hg, self._target + (p.ramp_mm_hg_per_min / 60) * dt)

        noisy = (5 if p.scenario == "noisy" else 1) * p.noise
        fi = p.fico2
        if p.apnea_on:
            # no gas moves past the sensor: it relaxes to the inspired level
            self._apnea_co2 = fi + (self._apnea_co2 - fi) * math.exp(-dt / 3)
            flow = 0.0
            co2 = self._apnea_co2
            self._breath_start = self._t
        else:
            x = self._t - self._breath_start
            if x >= self._period:
                self._breath_start += self._period
                self._prev_end = self._et
                self._et += (self._target - self._et) * min(1.0, max(0.0, p.settle))
                self._plan_breath()
                x = self._t - self._breath_start
            if x < self._ti:
                flow = self._qpk * math.sin((math.pi * x) / self._ti)
                co2 = fi + (self._prev_end - fi) * math.exp(-x / 0.05)
            else:
                e = x - self._ti
                te = self._period - self._ti
                flow = -self._qe * math.exp(-e / self._tau) * (1 - math.exp(-e / 0.06))
                up = 1 / (1 + math.exp(-(e - 0.28) / 0.04))
                co2 = fi + up * (self._et - 3 + 3 * (e / te) - fi)
                k = min(1, max(0, p.obstruction_pct / 100))
                if k > 0:
                    # obstruction: a slow, rounded rise with no flat plateau that still
                    # ends at the breath's EtCO2 ("shark fin")
                    tau = max(0.05, 0.5 * k * te)
                    fin = fi + (self._et - fi) * (1 - math.exp(-e / tau)) / (1 - math.exp(-te / tau))
                    co2 = (1 - k) * co2 + k * fin
            if self._shallow:
                flow *= 0.35
                co2 = co2 if x < self._ti else fi + (0.6 * self._et - fi) * ((x - self._ti) / (self._period - self._ti)) ** 2
            self._apnea_co2 = co2

        flow_lpm = p.flow_gain * flow + p.flow_offset_lpm + self._noise(0.6 * noisy)
        pneumo_pa = (flow + self._noise(0.4 * noisy)) / p.k_pneumo
        co2_mm = max(0, co2 + self._noise(0.25 * noisy))
        co2_pct = None if p.co2_disconnected_on else (co2_mm / p.barometric_mm_hg) * 100
        return PhysSample(co2_pct, flow_lpm, pneumo_pa)
