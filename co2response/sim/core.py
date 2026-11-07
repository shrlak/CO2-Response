"""Emulates the firmware: produces protocol lines sample by sample, including the
corrupt and missing lines the "noisy" scenario injects."""

from __future__ import annotations

import math
from typing import Callable, NamedTuple

from ..protocol import format_data_line
from .physiology import PhysioModel, mulberry32


class Encoders(NamedTuple):
    """Physical value → volts for each channel (the inverse of the channel calibration)."""

    co2: Callable[[float], float]
    flow: Callable[[float], float]
    pneumo: Callable[[float], float]


FIRMWARE_INFO = "I,FW,sim-1.0.0,ADC,SIMULATED,CH,3,RATE,"


class SimulatorCore:
    def __init__(self, encoders: Encoders, rail_low_v: float = 0, rail_high_v: float = 5, **params) -> None:
        self.encoders = encoders
        self.model = PhysioModel(**params)
        self.rate_hz = 100
        self.streaming = True
        self._seq = 0
        self._t_ms = 0.0
        self._rand = mulberry32(params.get("seed", 1) + 99)
        self._rail_low_v = rail_low_v
        self._rail_high_v = rail_high_v

    def _clamp(self, v: float) -> float:
        return min(self._rail_high_v, max(self._rail_low_v, v))

    def next_sample(self) -> list[str]:
        """The line(s) for one sample period. May be empty (dropped or stopped)."""
        dt = 1 / self.rate_hz
        s = self.model.step(dt)
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        self._t_ms += 1000 * dt
        p = self.model.params
        if not self.streaming or p.stop_data_on:
            return []

        # an unplugged sensor: the recommended pull-up drives its input to the high rail
        volts = [
            self._rail_high_v if s.co2_pct is None else self._clamp(self.encoders.co2(s.co2_pct)),
            self._rail_high_v if p.flow_disconnected else self._clamp(self.encoders.flow(s.flow_lpm)),
            self._rail_high_v if p.pneumo_disconnected else self._clamp(self.encoders.pneumo(s.pneumo_pa)),
        ]
        spikes = max(0.01 if p.scenario == "artifacts" else 0, p.spike_pct / 100)
        if spikes > 0 and self._rand() < spikes:
            # a glitchy ADC conversion: one sample on one channel jumps by 1–2 V
            ch = min(2, int(self._rand() * 3))
            volts[ch] = self._clamp(volts[ch] + (1 + self._rand()) * (1 if self._rand() < 0.5 else -1))
        line = format_data_line(seq, self._t_ms, volts)

        # lost and corrupted lines: the "noisy" scenario drops 0.5 % and corrupts 1 %
        drop, bad = (0.005, 0.015) if p.scenario == "noisy" else (0.0, 0.0)
        if p.line_error_pct / 100 > bad:
            bad = p.line_error_pct / 100
            drop = bad / 3
        if bad > 0:
            r = self._rand()
            if r < drop:
                return []  # dropped sample → sequence gap
            if r < bad:
                line = line[:-2] + ("01" if line.endswith("00") else "00")  # bad checksum
        return [line]

    def command(self, cmd: str) -> list[str]:
        """Reply to a host command the way the firmware does."""
        parts = cmd.strip().upper().split()
        name = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if name == "INFO":
            return [FIRMWARE_INFO + str(self.rate_hz)]
        if name == "START":
            self.streaming = True
            return ["A,START"]
        if name == "STOP":
            self.streaming = False
            return ["A,STOP"]
        if name == "RATE":
            try:
                hz = float(arg)
            except ValueError:
                hz = math.nan
            if not math.isfinite(hz) or not hz.is_integer() or hz < 1 or hz > 200:
                return ["E,BAD_RATE"]
            self.rate_hz = int(hz)
            return [f"A,RATE,{self.rate_hz}"]
        return ["E,UNKNOWN_CMD"]
