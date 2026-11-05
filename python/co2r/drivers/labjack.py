"""
LabJack T7 / T4 / T8 through LJM stream mode.

Install LJM (the driver) from labjack.com, then ``pip install "co2r[labjack]"``.

NOT YET TESTED ON HARDWARE: the code follows the labjack-ljm Python API and is tested
against a stand-in for it (python/tests/test_drivers.py). Try it with
``co2r-daq check --driver labjack`` before relying on it.
"""
from __future__ import annotations

from typing import Sequence

from .base import DaqDriver, DaqError, DaqOverrun, DriverInfo, average_groups

_SKIPPED = -9999.0  # LJM marks samples lost to a device buffer overflow with this value


class LabJackDriver(DaqDriver):
    name = "labjack"
    max_rate_hz = 10000.0

    def __init__(
        self,
        device_type: str = "ANY",
        connection: str = "ANY",
        identifier: str = "ANY",
        channels: Sequence[str] = ("AIN0", "AIN1", "AIN2"),
        range_v: float = 10.0,
        oversample: int = 10,
    ) -> None:
        self.device_type = device_type
        self.connection = connection
        self.identifier = identifier
        self.channels = [c.upper() for c in channels]
        self.range_v = range_v
        self.oversample = max(1, int(oversample))
        self._ljm = None
        self._handle = None
        self._carry: list[list[float]] = []

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            from labjack import ljm  # noqa: F401
        except Exception as e:
            return False, f'needs LabJack LJM and `pip install "co2r[labjack]"` ({e.__class__.__name__})'
        return True, "labjack-ljm installed"

    def open(self, rate_hz: float) -> DriverInfo:
        try:
            from labjack import ljm
        except Exception as e:
            raise DaqError(f"labjack-ljm is not available: {e}") from e
        self.close()
        self._ljm = ljm
        try:
            handle = ljm.openS(self.device_type, self.connection, self.identifier)
        except Exception as e:
            raise DaqError(f"No LabJack found ({self.device_type}/{self.connection}/{self.identifier}): {e}") from e
        try:
            info = ljm.getHandleInfo(handle)
            # best-effort stream settings; not every model has every register
            for name, value in [("STREAM_TRIGGER_INDEX", 0), ("STREAM_CLOCK_SOURCE", 0), ("STREAM_SETTLING_US", 0), ("STREAM_RESOLUTION_INDEX", 0)]:
                try:
                    ljm.eWriteName(handle, name, value)
                except Exception:
                    pass
            for ch in self.channels:
                for name, value in [(f"{ch}_RANGE", self.range_v), (f"{ch}_NEGATIVE_CH", 199)]:  # 199 = single-ended
                    try:
                        ljm.eWriteName(handle, name, value)
                    except Exception:
                        pass
            addresses = ljm.namesToAddresses(len(self.channels), self.channels)[0]
            hw_rate = float(rate_hz) * self.oversample
            scans_per_read = max(self.oversample, int(hw_rate * 0.05) // self.oversample * self.oversample)
            actual = ljm.eStreamStart(handle, scans_per_read, len(addresses), addresses, hw_rate)
        except Exception as e:
            try:
                ljm.close(handle)
            except Exception:
                pass
            raise DaqError(f"LabJack stream could not start: {e}") from e
        self._handle = handle
        self._carry = []
        model = {4: "T4", 7: "T7", 8: "T8"}.get(info[0] if isinstance(info, (list, tuple)) else 0, "LabJack")
        return DriverInfo(
            name=f"LabJack {model}",
            channels=len(self.channels),
            rate_hz=float(actual) / self.oversample,
            full_scale_v=self.range_v,
            extra={"OVERSAMPLE": self.oversample},
        )

    def read(self, timeout: float = 1.0) -> list[Sequence[float]]:
        if self._handle is None or self._ljm is None:
            raise DaqError("LabJack stream is not open")
        try:
            data, _device_backlog, _ljm_backlog = self._ljm.eStreamRead(self._handle)[:3]
        except Exception as e:
            raise DaqError(f"LabJack read failed: {e}") from e
        n = len(self.channels)
        scans = [list(data[i : i + n]) for i in range(0, len(data) - n + 1, n)]
        lost = sum(1 for s in scans if any(v == _SKIPPED for v in s))
        if lost:
            self._carry = []
            good = [s for s in scans if not any(v == _SKIPPED for v in s)]
            raise DaqOverrun(lost // self.oversample or 1, f"LabJack skipped {lost} scans ({len(good)} kept)")
        samples = self._carry + scans
        usable = len(samples) // self.oversample * self.oversample
        self._carry = samples[usable:]
        return average_groups(samples[:usable], self.oversample)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None and self._ljm is not None:
            try:
                self._ljm.eStreamStop(handle)
            except Exception:
                pass
            try:
                self._ljm.close(handle)
            except Exception:
                pass


def list_devices() -> list[str]:
    try:
        from labjack import ljm

        n, types, conns, serials, _ips = ljm.listAll(ljm.constants.dtANY, ljm.constants.ctANY)
        return [f"LabJack T{t} serial {s}" for t, s in zip(types[:n], serials[:n])]
    except Exception:
        return []
