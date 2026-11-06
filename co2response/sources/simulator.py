"""Runs the firmware emulation against the clock, like a device streaming in real time."""

from __future__ import annotations

import time
from typing import Callable

from ..sim.core import Encoders, SimulatorCore
from .base import Source, SourceItem


class SimulatorSource(Source):
    kind = "simulator"
    label = "Simulator"

    def __init__(self, encoders: Encoders, rail_low_v: float = 0, rail_high_v: float = 5, clock: Callable[[], float] = time.monotonic, **params) -> None:
        self.core = SimulatorCore(encoders, rail_low_v, rail_high_v, **params)
        self._clock = clock
        self._out: list[SourceItem] = []
        self._due = 0.0
        self._last = 0.0

    def start(self) -> None:
        self._out = list(self.core.command("INFO"))
        self._last = self._clock()
        self._due = 0.0

    def poll(self) -> list[SourceItem]:
        now = self._clock()
        rate = self.core.rate_hz
        # catch up after a stall (e.g. a modal dialog), but never more than 2 s at once
        self._due = min(self._due + (now - self._last) * rate, 2 * rate)
        self._last = now
        out, self._out = self._out, []
        while self._due >= 1:
            self._due -= 1
            out.extend(self.core.next_sample())
        return out

    def send(self, command: str) -> None:
        self._out.extend(self.core.command(command))

    def stop(self) -> None:
        self._out = []
