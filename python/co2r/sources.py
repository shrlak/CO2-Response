"""
Line sources: something that produces protocol lines and accepts the dashboard's
commands (INFO, START, STOP, RATE n). Each runs in its own thread and hands lines to
``emit`` (thread-safe on the server side).

- SerialRelaySource: the Arduino. Lines are passed through untouched.
- SampledSource: any DaqDriver. Turns hardware-timed volts into protocol lines and
  answers commands the way the firmware does.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from . import __version__
from .drivers.base import DaqDriver, DaqError, DaqOverrun, DriverInfo
from .protocol import format_data_line, info_line

Emit = Callable[[str], None]
Log = Callable[[str], None]


class Source:
    def __init__(self) -> None:
        self.emit: Emit = lambda line: None
        self.log: Log = lambda msg: None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, emit: Emit, log: Log = lambda m: None) -> None:
        self.emit = emit
        self.log = log
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name=type(self).__name__, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def command(self, cmd: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class SerialRelaySource(Source):
    """Relays the Arduino firmware's lines and forwards commands to it."""

    def __init__(self, port: str, baud: int = 115200) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self._ser = None
        self._lock = threading.Lock()

    def run(self) -> None:
        import serial  # pyserial

        while not self._stop.is_set():
            try:
                self._ser = serial.Serial(self.port, self.baud, timeout=0.2)
            except Exception as e:
                self.log(f"cannot open {self.port}: {e}; retrying")
                self.emit("E,BRIDGE_SERIAL_LOST")
                self._stop.wait(2.0)
                continue
            self.log(f"reading {self.port} at {self.baud} baud")
            buf = b""
            try:
                while not self._stop.is_set():
                    chunk = self._ser.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        self.emit(raw.rstrip(b"\r").decode("ascii", "replace"))
            except Exception as e:
                self.log(f"serial error: {e}; reconnecting")
                self.emit("E,BRIDGE_SERIAL_LOST")
            finally:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self._stop.wait(1.0)

    def command(self, cmd: str) -> None:
        with self._lock:
            if self._ser is not None:
                self._ser.write((cmd.strip() + "\n").encode("ascii", "ignore"))


class SampledSource(Source):
    """Wraps a DaqDriver. Behaves like the firmware: seq advances once per sample slot
    (lost samples leave a gap), t_ms comes from the sample clock, and the dashboard's
    INFO/START/STOP/RATE commands work the same."""

    def __init__(self, driver: DaqDriver, rate_hz: float = 100.0, autostart: bool = True) -> None:
        super().__init__()
        self.driver = driver
        self.rate_hz = float(rate_hz)
        self.streaming = autostart
        self.info: DriverInfo | None = None
        self.seq = 0
        self._t_base_ms = 0.0
        self._n = 0
        self._pending_rate: float | None = None
        self._lock = threading.Lock()

    # ---- the firmware-like INFO line
    def info_fields(self) -> dict:
        i = self.info
        f = {"FW": f"co2r-py-{__version__}", "ADC": i.name if i else self.driver.name, "CH": i.channels if i else 0, "RATE": _num(i.rate_hz if i else self.rate_hz), "DRIVER": self.driver.name}
        if i and i.full_scale_v is not None:
            f["FSR"] = _num(i.full_scale_v)
        if i:
            f.update(i.extra)
        return f

    def _open(self) -> bool:
        try:
            self.info = self.driver.open(self.rate_hz)
        except DaqError as e:
            msg = str(e).replace(",", ";").replace("\n", " ")
            self.log(f"DAQ not available: {msg}; retrying in 2 s")
            self.emit(f"E,ADC_NOT_FOUND,{msg}")
            return False
        self.rate_hz = self.info.rate_hz
        self.emit(info_line(self.info_fields()))
        self.log(f"acquiring from {self.info.name}: {self.info.channels} channels at {self.info.rate_hz:g} Hz")
        return True

    def run(self) -> None:
        while not self._stop.is_set():
            if not self._open():
                self._stop.wait(2.0)
                continue
            try:
                while not self._stop.is_set():
                    with self._lock:
                        new_rate, self._pending_rate = self._pending_rate, None
                    if new_rate is not None:
                        self._change_rate(new_rate)
                    try:
                        samples = self.driver.read(timeout=0.5)
                    except DaqOverrun as e:
                        lost = max(1, e.lost)
                        self._advance(lost)
                        self.emit("E,OVERRUN")
                        self.log(str(e))
                        continue
                    for s in samples:
                        if self.streaming:
                            self.emit(format_data_line(self.seq, self._t_ms(), s))
                        self._advance(1)
            except DaqError as e:
                self.log(f"DAQ error: {e}; reopening")
                self.emit(f"E,ADC_NOT_FOUND,{str(e).replace(',', ';')}")
            finally:
                try:
                    self.driver.close()
                except Exception:
                    pass
            self._stop.wait(1.0)

    def _t_ms(self) -> float:
        return self._t_base_ms + self._n * 1000.0 / self.rate_hz

    def _advance(self, n: int) -> None:
        self.seq = (self.seq + n) & 0xFFFF
        self._n += n

    def _change_rate(self, hz: float) -> None:
        self._t_base_ms = self._t_ms()
        self._n = 0
        self.driver.close()
        self.rate_hz = hz
        self.info = self.driver.open(hz)
        self.rate_hz = self.info.rate_hz
        self.emit(f"A,RATE,{_num(self.rate_hz)}")

    def command(self, cmd: str) -> None:
        parts = cmd.strip().upper().split()
        if not parts:
            return
        name = parts[0]
        if name == "INFO":
            self.emit(info_line(self.info_fields()))
        elif name == "START":
            self.streaming = True
            self.emit("A,START")
        elif name == "STOP":
            self.streaming = False
            self.emit("A,STOP")
        elif name == "RATE":
            try:
                hz = float(parts[1])
            except (IndexError, ValueError):
                hz = -1
            if not (1 <= hz <= self.driver.max_rate_hz):
                self.emit("E,BAD_RATE")
                return
            with self._lock:
                self._pending_rate = hz
        else:
            self.emit("E,UNKNOWN_CMD")


def _num(v: float) -> str:
    return f"{v:g}"
