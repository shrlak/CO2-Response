"""The Arduino over USB serial (pyserial). Works on Windows, macOS and Linux."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..protocol import LineSplitter
from .base import StatusEvent, ThreadedSource

#: USB vendor ids of Arduino boards and the USB-serial chips clones use
USB_VENDORS = {0x2341: "Arduino", 0x2A03: "Arduino", 0x1A86: "CH340", 0x0403: "FTDI", 0x10C4: "CP210x", 0x239A: "Adafruit"}


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None

    @property
    def usb_id(self) -> str:
        """vid:pid:serial, which stays the same when the port name changes after a replug."""
        if self.vid is None:
            return ""
        return f"{self.vid:04x}:{(self.pid or 0):04x}:{self.serial_number or ''}"

    @property
    def likely_arduino(self) -> bool:
        return self.vid in USB_VENDORS or "arduino" in self.description.lower()

    @property
    def label(self) -> str:
        desc = self.description if self.description and self.description != "n/a" else "Serial port"
        if self.device in desc:
            return desc
        return f"{desc} ({self.device})"


def list_ports() -> list[PortInfo]:
    """Serial ports on this computer, likely Arduinos first."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    ports = [PortInfo(p.device, p.description or "", p.vid, p.pid, p.serial_number) for p in lp.comports()]
    return sorted(ports, key=lambda p: (not p.likely_arduino, p.device))


def _friendly(port: str, e: Exception) -> str:
    msg = str(e)
    low = msg.lower()
    if "permission" in low or "access is denied" in low or "busy" in low or "resource busy" in low:
        return f"{port} is in use or not allowed. Close the Arduino IDE Serial Monitor (only one program can use the port){'' if 'win' in port.lower() else ', and on Linux add yourself to the dialout group'}."
    if "no such file" in low or "could not find" in low or "filenotfound" in low or "cannot find" in low:
        return f"{port} is not there. Check the USB cable and pick the port again."
    return f"Could not open {port}: {msg}"


class SerialSource(ThreadedSource):
    kind = "serial"

    def __init__(self, port: str, baud_rate: int = 115200, label: str | None = None) -> None:
        super().__init__()
        self.port = port
        self.baud_rate = baud_rate
        self.label = label or f"Arduino ({port})"
        self._ser = None
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        try:
            import serial
        except ImportError as e:
            raise RuntimeError("pyserial is not installed (pip install pyserial).") from e
        try:
            # serial_for_url also takes URLs such as socket://host:port or rfc2217://
            self._ser = serial.serial_for_url(self.port, baudrate=self.baud_rate, timeout=0.05)
        except (serial.SerialException, OSError, ValueError) as e:
            raise RuntimeError(_friendly(self.port, e)) from e
        self._closing = False
        self._thread = threading.Thread(target=self._read_loop, name=f"serial {self.port}", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        split = LineSplitter()
        ser = self._ser
        try:
            while not self._closing:
                data = ser.read(max(1, ser.in_waiting))
                if data:
                    for line in split.push(data.decode("ascii", errors="replace")):
                        self._q.put(line)
        except Exception as e:  # unplugged, reset, or the OS closed the port
            if not self._closing:
                self._q.put(StatusEvent("error", f"Device disconnected (USB unplugged or reset): {e}"))

    def send(self, command: str) -> None:
        if self._ser is None:
            return
        try:
            with self._write_lock:
                self._ser.write((command.strip() + "\n").encode("ascii"))
        except Exception as e:
            if not self._closing:
                self._q.put(StatusEvent("error", f"Could not write to the device: {e}"))

    def stop(self) -> None:
        self._closing = True
        ser = self._ser
        if ser is not None:
            try:
                ser.cancel_read()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._ser = None
