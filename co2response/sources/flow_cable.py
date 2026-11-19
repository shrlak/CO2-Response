"""The flow sensor through Sensirion's evaluation cable, straight into the computer.

The SFM3300-250-D (or any SFM3x00-D/AW) clips into Sensirion's evaluation cable
(SEK-SFM3xxx-AW/D, generation 1 or 2). A microcontroller in the clip-on cap reads the
sensor over I2C and sends its flow over RS-232, which an FTDI chip in the USB plug
turns into a serial port on the computer (Sensirion application note GF_AN_SFM-22). The
protocol is Nicolay's "flow meter connector with pressure sensor" protocol
(SP_290322000_cp):

- 115200 baud, 8 data bits, no parity, 1 stop bit; the master asks, the cable answers.
- A frame is ``address, function, n, data[n], crc`` (address 1 by default), with data
  low byte first and an 8-bit CRC (polynomial 0x31, initial value 0, the SFM3000's).
- Function 5 is a test (answer ``55 AA``), 10 the sensor's product identifier, 15 its
  serial number, 16 the flow in milli-standard-litres a minute (the cable applies the
  sensor's own offset and scale, so any SFM3x00 reads correctly), and 30 starts a
  continuous stream: ``flow (int32, mslm), [pressure (int16)], 0xFF, 0x03`` per packet.

Three parts, as for the Capnostream (capnostream.py):

- ``FlowCableSource`` opens the cable, checks it answers, and streams the flow (or polls
  function 16 if the stream does not start), as ``FlowSample`` items.
- ``FlowMerge`` puts that flow into the flow value of the Arduino's data lines: each line
  waits (at most ``HOLD_MAX_S``) for the flow of its moment, and gets the mean of the
  cable's samples since the previous line, the same anti-aliasing average the firmware
  used to take. With no flow from the cable for ``FLOW_TIMEOUT_S`` the flow is sent as
  9999, which the monitor shows as "---" and a "not answering" alarm.
- ``bench_check`` reads the cable on its own, for a first test (``co2response --flow-cable PORT``).

The cable is plugged straight into the computer, not through the USB isolator: run the
laptop on its battery (mains unplugged) while a person is connected.
"""

from __future__ import annotations

import collections
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from ..protocol import DataLine, InfoLine, format_data_line, parse_line
from .base import SourceItem, StatusEvent, ThreadedSource

NOT_ANSWERING = 9999.0
BAUD = 115200
DEFAULT_ADDRESS = 1
#: no flow from the cable for this long: the flow reads "not answering"
FLOW_TIMEOUT_S = 0.5
#: an Arduino line waits at most this long for the flow of its moment
HOLD_MAX_S = 0.3

FN_TEST = 5
FN_PRODUCT = 10
FN_SERIAL = 15
FN_FLOW = 16
FN_STREAM = 30

#: Sensirion product identifiers (bits 27:8 of function 10's answer)
PRODUCTS = {
    0x18ABA: "SFM3200-AW",
    0x18ABC: "SFM3300-AW",
    0x18CA9: "SFM3400-AW",
    0x18ABD: "SFM3300-D",
    0x18CEB: "SFM3400-D",
    0x40501: "SFM3304-D",
}

#: a frame's exception codes (the answer's function code has bit 7 set)
EXCEPTIONS = {
    1: "unknown function",
    2: "no firmware",
    3: "still starting up",
    4: "busy",
    5: "wrong data length",
    15: "sensor shut down (needs a hardware reset)",
}


def crc8(data: bytes | bytearray | Iterable[int]) -> int:
    """The protocol's CRC-8: polynomial x^8 + x^5 + x^4 + 1 (0x31), initial value 0."""
    crc = 0
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def request(function: int, data: bytes = b"", address: int = DEFAULT_ADDRESS) -> bytes:
    """A master frame: address, function, n, data, crc."""
    body = bytes([address & 0xFF, function & 0x7F, len(data)]) + data
    return body + bytes([crc8(body)])


class FrameError(Exception):
    pass


def parse_answer(frame: bytes, function: int) -> bytes:
    """The data of a cable's answer to ``function``; raises FrameError for a bad or
    exception answer."""
    if len(frame) < 4:
        raise FrameError("answer too short")
    n = frame[2]
    if len(frame) != n + 4:
        raise FrameError("answer has the wrong length")
    if crc8(frame[:-1]) != frame[-1]:
        raise FrameError("answer has a bad checksum")
    if frame[1] == (function | 0x80):
        code = frame[3] if n else 0
        raise FrameError(f"cable refused function {function}: {EXCEPTIONS.get(code, f'code {code}')}")
    if frame[1] != function:
        raise FrameError(f"answer to function {frame[1]}, not {function}")
    return bytes(frame[3:-1])


def product_name(data: bytes) -> str:
    """The sensor's name from function 10's 4-byte answer."""
    if len(data) < 4:
        return "unknown sensor"
    v = int.from_bytes(data[:4], "little")
    pid = (v >> 8) & 0xFFFFF
    return PRODUCTS.get(pid, f"sensor 0x{pid:05X}")


def flow_slm(data: bytes) -> float:
    """Flow in standard L/min from 4 bytes of signed milli-standard-litres a minute."""
    v = int.from_bytes(data[:4], "little", signed=True)
    return math.nan if v == 0x7FFFFFFF else v / 1000.0


@dataclass(frozen=True)
class FlowSample:
    """One flow reading from the cable: computer time (s) and standard L/min."""

    t: float
    slm: float


@dataclass(frozen=True)
class CableInfo:
    """What the cable said about itself when it started."""

    sensor: str
    serial: str
    mode: str  # 'stream' or 'poll'


class StreamDecoder:
    """Splits the cable's continuous stream into flow values.

    A packet is ``flow (4 bytes) [pressure (2 bytes)] FF 03``: 6 bytes without a pressure
    sensor in the cap, 8 with one (the evaluation cable has one). The packet length is
    found from three trailers in a row at the same spacing; after that each packet must
    end in FF 03, or the decoder searches again."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.length: int | None = None
        self.packets = 0
        self.resyncs = 0

    @staticmethod
    def _plausible(pkt: bytes | bytearray, n: int) -> bool:
        if pkt[n - 2] != 0xFF or pkt[n - 1] != 0x03:
            return False
        # flow is within +-2,000,000 mslm, so its top byte is 00 or FF
        return pkt[3] in (0x00, 0xFF)

    def push(self, data: bytes) -> list[float]:
        self.buf += data
        out: list[float] = []
        while True:
            if self.length is None:
                if not self._sync():
                    if len(self.buf) > 64:
                        del self.buf[: len(self.buf) - 32]
                    return out
            n = self.length
            assert n is not None
            if len(self.buf) < n:
                return out
            pkt = self.buf[:n]
            if not self._plausible(pkt, n):
                self.length = None
                self.resyncs += 1
                del self.buf[:1]
                continue
            del self.buf[:n]
            self.packets += 1
            out.append(flow_slm(bytes(pkt[:4])))

    def _sync(self) -> bool:
        b = self.buf
        for start in range(0, max(0, len(b) - 17)):
            for n in (8, 6):
                if start + 3 * n > len(b):
                    continue
                if all(self._plausible(b[start + k * n : start + (k + 1) * n], n) for k in range(3)):
                    del b[:start]
                    self.length = n
                    return True
        return False


class FlowCableSource(ThreadedSource):
    """Sensirion's evaluation cable on a serial port: ``FlowSample`` items, a
    ``CableInfo`` once it has started, and a ``StatusEvent`` if it goes away."""

    kind = "flowcable"

    def __init__(self, port: str, address: int = DEFAULT_ADDRESS, opener: Callable[[str], object] | None = None) -> None:
        super().__init__()
        self.port = port
        self.address = address
        self.label = f"Sensirion flow cable ({port})"
        self._opener = opener
        self._ser = None
        self._thread: threading.Thread | None = None
        self.info: CableInfo | None = None

    # ---------- talking to the cable

    def _open(self):
        if self._opener is not None:
            return self._opener(self.port)
        try:
            import serial
        except ImportError as e:
            raise RuntimeError("pyserial is not installed (pip install pyserial).") from e
        try:
            return serial.serial_for_url(self.port, baudrate=BAUD, timeout=0.05)
        except (serial.SerialException, OSError, ValueError) as e:
            raise RuntimeError(f"Could not open the flow cable on {self.port}: {e}") from e

    def _ask(self, function: int, data: bytes = b"", timeout: float = 0.3) -> bytes:
        ser = self._ser
        assert ser is not None
        ser.write(request(function, data, self.address))
        frame = bytearray()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame += ser.read(max(1, ser.in_waiting))
            if len(frame) >= 3 and len(frame) >= frame[2] + 4:
                break
        if not frame:
            raise FrameError("no answer")
        return parse_answer(bytes(frame[: frame[2] + 4] if len(frame) >= 3 else frame), function)

    def _quiet(self) -> None:
        """Stop a stream left running by an earlier session and empty the input."""
        ser = self._ser
        assert ser is not None
        ser.write(b"\x00")  # any byte stops a stream
        time.sleep(0.05)
        ser.reset_input_buffer()

    def start(self) -> None:
        self._ser = self._open()
        try:
            self._quiet()
            try:
                self._ask(FN_TEST)
            except FrameError:
                self._quiet()
                self._ask(FN_TEST)  # a stream may have swallowed the first try
            sensor = serial_no = "?"
            try:
                sensor = product_name(self._ask(FN_PRODUCT))
                serial_no = str(int.from_bytes(self._ask(FN_SERIAL)[:8], "little"))
            except FrameError:
                pass
        except FrameError as e:
            self._close()
            raise RuntimeError(f"The flow cable on {self.port} does not answer ({e}). Check that the sensor is clipped in and this is the cable's port.") from e
        except Exception as e:
            self._close()
            raise RuntimeError(f"Could not talk to the flow cable on {self.port}: {e}") from e
        self._closing = False
        self._sensor, self._serial = sensor, serial_no
        self._thread = threading.Thread(target=self._read_loop, name=f"flow cable {self.port}", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        ser = self._ser
        assert ser is not None
        try:
            mode = self._stream(ser)
            if mode == "poll":
                self._poll(ser)
        except Exception as e:  # unplugged
            if not self._closing:
                self._q.put(StatusEvent("error", f"Flow cable disconnected: {e}"))

    def _stream(self, ser) -> str:
        """Stream until closed; returns 'poll' if no packets come within a second."""
        ser.write(request(FN_STREAM, b"", self.address))
        dec = StreamDecoder()
        started = time.monotonic()
        dt = 0.001  # the packet spacing, learnt as it goes
        last_t: float | None = None
        announced = False
        while not self._closing:
            data = ser.read(max(1, ser.in_waiting))
            now = time.monotonic()
            if not data:
                if not announced and now - started > 1.0:
                    return "poll"
                continue
            flows = dec.push(data)
            if not flows:
                if not announced and now - started > 1.0:
                    self._quiet()
                    return "poll"
                continue
            if not announced:
                announced = True
                self.info = CableInfo(self._sensor, self._serial, "stream")
                self._q.put(self.info)  # type: ignore[arg-type]
            if last_t is not None and len(flows):
                dt += 0.05 * ((now - last_t) / len(flows) - dt)
                dt = min(max(dt, 0.0002), 0.05)
            last_t = now
            k = len(flows)
            for i, f in enumerate(flows):  # spread a burst evenly back from its arrival
                self._q.put(FlowSample(now - (k - 1 - i) * dt, f))  # type: ignore[arg-type]
        return "stream"

    def _poll(self, ser) -> None:
        self.info = CableInfo(self._sensor, self._serial, "poll")
        self._q.put(self.info)  # type: ignore[arg-type]
        while not self._closing:
            try:
                data = self._ask(FN_FLOW, timeout=0.1)
            except FrameError:
                continue
            self._q.put(FlowSample(time.monotonic(), flow_slm(data)))  # type: ignore[arg-type]
            time.sleep(0.002)

    def _close(self) -> None:
        ser = self._ser
        self._ser = None
        if ser is not None:
            try:
                ser.write(b"\x00")  # leave the cable quiet
            except Exception:
                pass
            try:
                ser.close()
            except Exception:
                pass

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
        self._close()


@dataclass
class _Held:
    t: float  # on the computer's clock
    line: str


class FlowMerge:
    """Puts the cable's flow into the Arduino's data lines (see the module docstring).

    ``clock()`` is the computer's clock in seconds (``time.monotonic``); ``sign`` is +1
    when inspiration reads positive with the sensor as mounted, -1 if not."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sign: int = 1) -> None:
        self.clock = clock
        self.sign = -1 if sign < 0 else 1
        self.samples: collections.deque[tuple[float, float]] = collections.deque(maxlen=4000)
        self._held: collections.deque[_Held] = collections.deque()
        self._offset: float | None = None
        self._offset_at = 0.0
        self._prev_t: float | None = None
        self.samples_in = 0
        self.info: CableInfo | None = None

    def cable_items(self, items: Iterable[object]) -> list[StatusEvent]:
        """Take the cable's items; returns its status events (a disconnect)."""
        events: list[StatusEvent] = []
        for item in items:
            if isinstance(item, FlowSample):
                if math.isfinite(item.slm):
                    self.samples.append((item.t, self.sign * item.slm))
                    self.samples_in += 1
            elif isinstance(item, CableInfo):
                self.info = item
            elif isinstance(item, StatusEvent):
                events.append(item)
        return events

    def flow_between(self, t0: float | None, t1: float) -> float:
        """The mean flow of the samples in (t0, t1], or the nearest one; NaN with none recent."""
        s = self.samples
        if not s or self.clock() - s[-1][0] > FLOW_TIMEOUT_S or t1 - s[-1][0] > FLOW_TIMEOUT_S:
            return math.nan
        lo = t1 - 0.05 if t0 is None or t1 - t0 > 0.05 else t0
        total, n = 0.0, 0
        nearest, gap = math.nan, math.inf
        for ts, v in reversed(s):
            if ts <= lo:
                if abs(ts - t1) < gap:
                    nearest, gap = v, abs(ts - t1)
                break
            if ts <= t1:
                total += v
                n += 1
            elif abs(ts - t1) < gap:
                nearest, gap = v, abs(ts - t1)
        if n:
            return total / n
        return nearest if gap <= 0.1 else math.nan

    def board_items(self, items: Iterable[SourceItem]) -> list[SourceItem]:
        """The board's lines and events; data lines come back once their flow is known."""
        out: list[SourceItem] = []
        now = self.clock()
        for item in items:
            if not isinstance(item, str):
                out.append(item)
                continue
            p = parse_line(item)
            if isinstance(p, InfoLine) and "FW" in p.fields:
                out.append(self._info_line(item))
                continue
            if not isinstance(p, DataLine) or len(p.volts) < 2:
                out.append(item)
                continue
            dev = p.t_ms / 1000
            off = now - dev
            if self._offset is None or off < self._offset or now - self._offset_at > 30:
                self._offset, self._offset_at = off, now
            self._held.append(_Held(dev + self._offset, item))
        out.extend(self.release())
        return out

    def release(self) -> list[str]:
        out: list[str] = []
        now = self.clock()
        last = self.samples[-1][0] if self.samples else -math.inf
        while self._held:
            h = self._held[0]
            if h.t > last and now - h.t < HOLD_MAX_S:
                break
            self._held.popleft()
            out.append(self._with_flow(h))
        return out

    def _with_flow(self, h: _Held) -> str:
        p = parse_line(h.line)
        if not isinstance(p, DataLine) or len(p.volts) < 2:
            return h.line
        f = self.flow_between(self._prev_t, h.t)
        self._prev_t = h.t
        values = list(p.volts)
        values[1] = f if math.isfinite(f) else NOT_ANSWERING
        return format_data_line(p.seq, p.t_ms, values)

    def _info_line(self, line: str) -> str:
        """The board's info line, saying the flow comes from the cable."""
        name = "SFM_CABLE"
        parts = line.split(",")
        for i in range(1, len(parts) - 1, 2):
            if parts[i].upper() == "FLOW":
                parts[i + 1] = name
                return ",".join(parts)
        return line + f",FLOW,{name}"


def bench_check(port: str, seconds: float = 10, log=print, opener: Callable[[str], object] | None = None) -> int:
    """Read the cable on its own and print the flow twice a second. Returns 0 if flow came."""
    src = FlowCableSource(port, opener=opener)
    try:
        src.start()
    except RuntimeError as e:
        log(str(e))
        return 1
    n = 0
    try:
        end = time.monotonic() + seconds
        window: list[float] = []
        next_print = time.monotonic() + 0.5
        while time.monotonic() < end:
            for item in src.poll():
                if isinstance(item, CableInfo):
                    log(f"Cable answers: {item.sensor}, serial {item.serial}, {item.mode} mode")
                elif isinstance(item, FlowSample):
                    n += 1
                    window.append(item.slm)
                elif isinstance(item, StatusEvent):
                    log(item.detail)
                    return 1
            if time.monotonic() >= next_print:
                next_print += 0.5
                if window:
                    log(f"flow {sum(window) / len(window):8.2f} L/min   ({len(window)} readings)")
                    window = []
            time.sleep(0.02)
    finally:
        src.stop()
    log(f"{n} flow readings in {seconds:g} s" if n else "No flow readings came from the cable.")
    return 0 if n else 1
