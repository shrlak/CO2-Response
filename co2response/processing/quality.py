"""Flags a channel that has sat at an ADC rail (sensor unplugged, saturated or shorted)."""

from __future__ import annotations


def at_rail(v: float, low_v: float, high_v: float, tol_v: float, check: str = "both") -> bool:
    """True as soon as the sample is at a rail (before any hold time).

    ``check`` says which rails mean a fault: a capnograph legitimately reads 0 V at
    0 % CO2, so the CO2 channel checks only the high rail.
    """
    low = check in ("both", "low") and v <= low_v + tol_v
    high = check in ("both", "high") and v >= high_v - tol_v
    return low or high


class RailMonitor:
    def __init__(self, low_v: float, high_v: float, tol_v: float, hold_sec: float = 2, check: str = "both") -> None:
        self.low_v, self.high_v, self.tol_v = low_v, high_v, tol_v
        self.hold_sec = hold_sec
        self.check = check
        self._since: float | None = None

    def push(self, t_sec: float, v: float) -> bool:
        """True while the channel is at a rail and has been for hold_sec."""
        if not at_rail(v, self.low_v, self.high_v, self.tol_v, self.check):
            self._since = None
            return False
        if self._since is None:
            self._since = t_sec
        return t_sec - self._since >= self.hold_sec
