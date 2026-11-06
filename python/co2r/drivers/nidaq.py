"""
National Instruments DAQ through NI-DAQmx (USB-6001, USB-6009, USB-6211, cDAQ, ...).

Install NI-DAQmx (the driver) from ni.com, then ``pip install "co2r[nidaq]"``.
Find the device name (e.g. Dev1) in NI MAX, or run ``co2r-daq list``.

NOT YET TESTED ON HARDWARE: the code follows the nidaqmx Python API and is tested
against a stand-in for it (python/tests/test_drivers.py). Try it with
``co2r-daq check --driver nidaq`` before relying on it.
"""
from __future__ import annotations

from typing import Sequence

from .base import DaqDriver, DaqError, DaqOverrun, DriverInfo, average_groups

# NI-DAQmx error for "samples are no longer available" (the buffer overflowed)
_OVERWRITE_ERRORS = {-200279, -200277}


class NiDaqmxDriver(DaqDriver):
    name = "nidaq"
    max_rate_hz = 10000.0

    def __init__(
        self,
        device: str = "Dev1",
        channels: Sequence[str] = ("ai0", "ai1", "ai2"),
        min_v: float = 0.0,
        max_v: float = 5.0,
        terminal: str = "RSE",
        oversample: int = 10,
    ) -> None:
        self.device = device
        self.channels = list(channels)
        self.min_v = min_v
        self.max_v = max_v
        self.terminal = terminal.upper()
        self.oversample = max(1, int(oversample))
        self._task = None
        self._carry: list[list[float]] = []
        self._chunk = 1
        self._rate = 100.0

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import nidaqmx  # noqa: F401
        except Exception as e:  # ImportError or a missing NI runtime
            return False, f'needs NI-DAQmx and `pip install "co2r[nidaq]"` ({e.__class__.__name__})'
        return True, "nidaqmx installed"

    def _terminal_config(self, constants):
        tc = constants.TerminalConfiguration
        names = {
            "RSE": ["RSE"],
            "NRSE": ["NRSE"],
            "DIFF": ["DIFF", "DIFFERENTIAL"],
            "PSEUDO_DIFF": ["PSEUDO_DIFF", "PSEUDODIFFERENTIAL"],
            "DEFAULT": ["DEFAULT"],
        }.get(self.terminal, [self.terminal])
        for n in names:
            if hasattr(tc, n):
                return getattr(tc, n)
        raise DaqError(f"Unknown terminal configuration {self.terminal}. Use RSE, NRSE, DIFF or PSEUDO_DIFF.")

    def open(self, rate_hz: float) -> DriverInfo:
        try:
            import nidaqmx
            from nidaqmx import constants
        except Exception as e:
            raise DaqError(f"nidaqmx is not available: {e}") from e
        self.close()
        hw_rate = float(rate_hz) * self.oversample
        try:
            task = nidaqmx.Task()
            for ch in self.channels:
                task.ai_channels.add_ai_voltage_chan(
                    f"{self.device}/{ch}",
                    terminal_config=self._terminal_config(constants),
                    min_val=self.min_v,
                    max_val=self.max_v,
                )
            # 10 s of buffer so a busy computer does not lose samples
            task.timing.cfg_samp_clk_timing(rate=hw_rate, sample_mode=constants.AcquisitionType.CONTINUOUS, samps_per_chan=int(hw_rate * 10))
            task.start()
        except DaqError:
            raise
        except Exception as e:
            try:
                task.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            raise DaqError(f"NI-DAQmx could not start {self.device}: {e}") from e
        self._task = task
        self._carry = []
        self._rate = float(rate_hz)
        # read in ~50 ms chunks, whole oversample groups
        self._chunk = max(self.oversample, int(hw_rate * 0.05) // self.oversample * self.oversample)
        return DriverInfo(
            name=f"NI {self.device}",
            channels=len(self.channels),
            rate_hz=self._rate,
            full_scale_v=max(abs(self.min_v), abs(self.max_v)),
            extra={"OVERSAMPLE": self.oversample, "TERMINAL": self.terminal},
        )

    def read(self, timeout: float = 1.0) -> list[Sequence[float]]:
        if self._task is None:
            raise DaqError("NI task is not open")
        try:
            data = self._task.read(number_of_samples_per_channel=self._chunk, timeout=timeout)
        except Exception as e:
            code = getattr(e, "error_code", None)
            if code in _OVERWRITE_ERRORS:
                self._carry = []
                raise DaqOverrun(0, f"NI buffer overflow: {e}") from e
            if "timeout" in str(e).lower() or code == -200284:
                return []
            raise DaqError(f"NI read failed: {e}") from e
        # one channel -> flat list; several -> list per channel
        if data and not isinstance(data[0], (list, tuple)):
            data = [data]
        samples = [list(s) for s in zip(*data)]
        samples = self._carry + samples
        usable = len(samples) // self.oversample * self.oversample
        self._carry = samples[usable:]
        return average_groups(samples[:usable], self.oversample)

    def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            try:
                task.stop()
            finally:
                task.close()


def list_devices() -> list[str]:
    try:
        import nidaqmx.system

        return [f"{d.name} ({d.product_type})" for d in nidaqmx.system.System.local().devices]
    except Exception:
        return []
