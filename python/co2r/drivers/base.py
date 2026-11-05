"""
The interface every data-acquisition driver implements.

A driver only has to deliver hardware-timed voltages. Everything else (the line
protocol, sequence numbers, commands from the dashboard, reconnecting) is done by
co2r.sources.SampledSource, so adding a new DAQ is one small file. Start from
co2r/drivers/template.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class DriverInfo:
    """What a driver reports once it is open."""

    #: shown in the dashboard header and hardware check, e.g. "NI USB-6001 (Dev1)"
    name: str
    #: number of channels in every sample (CH0 = CO2, CH1 = flow, CH2 = pneumotach)
    channels: int
    #: samples per second per channel actually delivered
    rate_hz: float
    #: largest input voltage the device measures in its configured range, if known
    full_scale_v: float | None = None
    extra: dict = field(default_factory=dict)


class DaqError(Exception):
    """The device could not be opened or stopped working. SampledSource retries."""


class DaqOverrun(Exception):
    """Samples were lost (e.g. the device buffer overflowed). ``lost`` is how many, if known."""

    def __init__(self, lost: int = 0, message: str = "samples lost") -> None:
        super().__init__(message)
        self.lost = lost


class DaqDriver(ABC):
    #: short name used on the command line (--driver NAME)
    name = "base"
    #: highest rate the driver allows, samples/s per channel
    max_rate_hz = 1000.0

    @abstractmethod
    def open(self, rate_hz: float) -> DriverInfo:
        """Start acquiring at ``rate_hz``. Raise DaqError if the device is not available."""

    @abstractmethod
    def read(self, timeout: float = 1.0) -> list[Sequence[float]]:
        """Block until at least one sample is available (or ``timeout``) and return all
        samples read so far, oldest first. Each sample is one voltage per channel.
        Samples must be evenly spaced at the open rate (hardware-timed)."""

    @abstractmethod
    def close(self) -> None:
        """Stop and release the device. Must be safe to call twice."""

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """Whether the driver's library is installed: (ok, message)."""
        return True, "built in"


def average_groups(samples: list[Sequence[float]], n: int) -> list[list[float]]:
    """Average consecutive groups of ``n`` samples (oversampling: lower noise at a lower
    output rate). A trailing partial group is dropped; callers keep the remainder."""
    if n <= 1:
        return [list(s) for s in samples]
    out = []
    for i in range(0, len(samples) - n + 1, n):
        group = samples[i : i + n]
        out.append([sum(col) / n for col in zip(*group)])
    return out
