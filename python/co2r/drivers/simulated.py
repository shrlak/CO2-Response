"""A simulated DAQ: breathing waveforms in volts, paced in real time. No hardware needed."""
from __future__ import annotations

import math
import random
import time
from typing import Sequence

from .base import DaqDriver, DriverInfo


class SimulatedDriver(DaqDriver):
    """15 breaths/min, EtCO2 38 mmHg, VT 500 mL, encoded with the default channels.json
    scaling (CO2 2 %/V; flow 40 L/min/V around 2.5 V; pneumotach 200 Pa/V, k1 0.2)."""

    name = "simulated"
    max_rate_hz = 2000.0

    def __init__(self, rr: float = 15.0, etco2: float = 38.0, vt_ml: float = 500.0, ramp_mmhg_per_min: float = 0.0, noise: float = 1.0, seed: int | None = None) -> None:
        self.rr = rr
        self.etco2 = etco2
        self.vt_ml = vt_ml
        self.ramp = ramp_mmhg_per_min
        self.noise = noise
        self._rand = random.Random(seed)
        self._rate = 100.0
        self._n = 0
        self._t0 = 0.0
        self._open = False

    def open(self, rate_hz: float) -> DriverInfo:
        self._rate = float(rate_hz)
        self._n = 0
        self._t0 = time.monotonic()
        self._open = True
        return DriverInfo(name="Simulated DAQ", channels=3, rate_hz=self._rate, full_scale_v=5.0)

    def sample(self, n: int) -> list[float]:
        """Volts for sample number ``n`` (pure; used by read() and by tests)."""
        t = n / self._rate
        period = 60.0 / self.rr
        ti = period / 3
        x = t % period
        et = min(80.0, self.etco2 + self.ramp * t / 60.0)
        vt_l = self.vt_ml / 1000 * (1 + 0.04 * max(0.0, et - self.etco2))
        qpk = vt_l * 60 * math.pi / (2 * ti)
        tau = (period - ti) / 5
        qe = vt_l * 60 / (tau * tau / (tau + 0.06))
        if x < ti:
            flow = qpk * math.sin(math.pi * x / ti)
            co2 = et * math.exp(-x / 0.05)
        else:
            e = x - ti
            flow = -qe * math.exp(-e / tau) * (1 - math.exp(-e / 0.06))
            co2 = (et - 3 + 3 * e / (period - ti)) / (1 + math.exp(-(e - 0.28) / 0.04))
        r = self._rand.uniform
        k = self.noise
        pct = max(0.0, co2 + r(-0.25, 0.25) * k) / 760 * 100
        return [
            min(5.0, max(0.0, pct / 2.0)),
            min(5.0, max(0.0, (flow + r(-0.6, 0.6) * k + 100) / 40)),
            min(5.0, max(0.0, ((flow + r(-0.4, 0.4) * k) / 0.2 + 500) / 200)),
        ]

    def read(self, timeout: float = 1.0) -> list[Sequence[float]]:
        if not self._open:
            raise RuntimeError("driver is not open")
        deadline = time.monotonic() + timeout
        while True:
            due = int((time.monotonic() - self._t0) * self._rate)
            if due > self._n or time.monotonic() >= deadline:
                break
            time.sleep(min(0.01, 0.5 / self._rate))
        out = [self.sample(i) for i in range(self._n, due)]
        self._n = max(self._n, due)
        return out

    def close(self) -> None:
        self._open = False
