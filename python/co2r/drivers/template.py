"""
TEMPLATE for a new data-acquisition board. Copy this file, rename the class, fill in
the three methods, and register it in co2r/drivers/__init__.py. Then:

    co2r-daq check --driver mydaq        # does it read sensible volts at the right rate?
    co2r-daq serve --driver mydaq        # feed the dashboard (Settings > Connection > Python)

Rules the rest of the system relies on:
- read() returns voltages, one list per sample, channel order CO2, flow, pneumotach.
- Samples are hardware-timed and evenly spaced at the rate passed to open().
- Raise DaqError when the device is missing or fails (the server retries every 2 s).
- Raise DaqOverrun(lost) when the device reports lost samples, so the dashboard
  counts them as dropped.
"""
from __future__ import annotations

from typing import Sequence

from .base import DaqDriver, DaqError, DriverInfo


class TemplateDriver(DaqDriver):
    name = "template"
    max_rate_hz = 1000.0

    def __init__(self, device: str = "") -> None:
        self.device = device
        self._dev = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        # e.g. try: import vendor_library; except ImportError: return False, "pip install vendor_library"
        return False, "template only: copy it to write a driver"

    def open(self, rate_hz: float) -> DriverInfo:
        # 1. connect to the device (raise DaqError with a helpful message if it is missing)
        # 2. configure 3 single-ended voltage inputs with a range that covers 0-5 V
        # 3. start continuous, hardware-timed acquisition at rate_hz
        raise DaqError("TemplateDriver is a template. Copy it and fill in open/read/close.")

    def read(self, timeout: float = 1.0) -> list[Sequence[float]]:
        # return every sample that arrived since the last call, e.g. [[v0, v1, v2], ...]
        return []

    def close(self) -> None:
        self._dev = None
