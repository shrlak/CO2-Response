"""Runs the firmware emulation against the clock, like a device streaming in real time."""

from __future__ import annotations

import time
from typing import Callable

from ..sim.core import Encoders, SimulatorCore
from .base import Source, SourceItem

#: the fastest the simulation runs (the engine processes about 25 000 samples a second)
MAX_SPEED = 50.0


class SimulatorSource(Source):
    kind = "simulator"

    def __init__(
        self, encoders: Encoders, rail_low_v: float = 0, rail_high_v: float = 5, clock: Callable[[], float] = time.monotonic, speed: float = 1, digital: bool = False, **params
    ) -> None:
        self.core = SimulatorCore(encoders, rail_low_v, rail_high_v, digital=digital, **params)
        self._clock = clock
        #: simulated seconds per real second. The device clock in the lines runs this much
        #: faster, as in a replay at 2×–10×, so everything measured on it (RR, VT, MV, the
        #: breaths themselves) stays right while the numbers and graphs get there sooner.
        self.speed = speed
        self._out: list[SourceItem] = []
        self._due = 0.0
        self._last = 0.0

    @property
    def run_speed(self) -> float:
        """The speed it runs at: a stored value outside the panel's 1–50× runs at the nearest end."""
        return min(MAX_SPEED, max(1.0, self.speed))

    @property
    def label(self) -> str:  # type: ignore[override]
        return "Simulator" if self.run_speed == 1 else f"Simulator ({self.run_speed:g}× speed)"

    def start(self) -> None:
        self._out = list(self.core.command("INFO"))
        self._last = self._clock()
        self._due = 0.0

    def poll(self) -> list[SourceItem]:
        now = self._clock()
        rate = self.core.rate_hz * self.run_speed
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
