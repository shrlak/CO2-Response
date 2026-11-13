"""A Medtronic Capnostream 35 as the CO2 source.

The Capnostream 35 samples gas through its Microstream line (50 mL/min) and measures
CO2 20 times a second. Over its RS-232 port (or its LAN / Wi-Fi link) it can send
what it measures in its "Capnostream", "SPDout" or "ASCII" protocol. Here it takes
the place of the board's own CO2 sensor: its CO2 waveform goes into the CO2 channel
of the Arduino's data lines, which still carry the flow (SFM3304-D), and everything
downstream (breaths, EtCO2, the response plot, alarms, recording) is unchanged.

Three parts:

- ``CapnoDecoder`` turns the monitor's ASCII lines into CO2 waveform samples (mmHg)
  and its own numbers (EtCO2, FiCO2, RR, SpO2, pulse). The byte layout of the ASCII
  protocol is not published, so the decoder is configurable: by default it finds
  ``KEY=value`` / ``KEY: value`` pairs and bare numbers by itself; ``fields`` gives
  the fields in order instead (see ``CapnoSettings``). ``co2response --capture PORT``
  records what a monitor sends, to set this up (docs/capnostream.md).
- ``CapnostreamSource`` reads the monitor (a serial port, or a network address such
  as ``socket://192.168.1.50:4001``), and ``SimulatedCapnostream`` stands in for one.
- ``CapnoMerge`` puts the two streams on one timeline: each Arduino data line is held
  until the Capnostream's CO2 for that moment has arrived, then sent on with the CO2
  value in place (in %, which is what the Uno R4 board's lines carry). The sample
  line's delay (about 2.7 s) is applied by the processing's CO2 delay, as for any
  sidestream analyser. With no CO2 from the monitor for a second, the CO2 is sent as
  9999, which the monitor shows as "--- " and a "not answering" alarm.

RESEARCH USE ONLY. This reads a certified monitor's data output; the Capnostream's
own alarms stay the primary alarms.
"""

from __future__ import annotations

import collections
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..processing.co2 import mmhg_to_pct
from ..protocol import DataLine, InfoLine, format_data_line, parse_line
from .base import Source, SourceItem, StatusEvent, ThreadedSource

NOT_ANSWERING = 9999.0
#: the Capnostream measures CO2 this often (Hz)
CAPNO_RATE_HZ = 20.0
#: no CO2 from the monitor for this long: the CO2 channel reads "not answering"
CAPNO_TIMEOUT_S = 1.0
#: an Arduino line waits at most this long for the CO2 of its moment
HOLD_MAX_S = 0.6

#: names the monitor may give each value (upper case), for KEY=value lines
ALIASES: dict[str, str] = {
    "CO2": "co2", "CO2W": "co2", "WAVE": "co2", "CO2WAVE": "co2", "CAPNO": "co2", "W": "co2", "PCO2": "co2",
    "ETCO2": "etco2", "ET": "etco2", "ETC02": "etco2",
    "FICO2": "fico2", "FI": "fico2", "FIC02": "fico2",
    "RR": "rr", "RESP": "rr", "RESPRATE": "rr", "BR": "rr", "AWRR": "rr",
    "SPO2": "spo2", "SAT": "spo2", "SP02": "spo2",
    "PR": "pr", "PULSE": "pr", "HR": "pr",
    "IPI": "ipi",
}  # fmt: skip
NUMBERS = ("etco2", "fico2", "rr", "spo2", "pr", "ipi")

_PAIR = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*[=:]\s*(-?\d+(?:\.\d+)?)")
_WORD_NUM = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s+(-?\d+(?:\.\d+)?)")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_SPLIT = re.compile(r"[,;\t ]+")


def to_mmhg(v: float, units: str, barometric_mm_hg: float = 760.0) -> float:
    """A CO2 value in the monitor's unit, in mmHg."""
    if units == "%":
        return v / 100 * barometric_mm_hg
    if units == "kPa":
        return v * 7.50062
    return v


@dataclass
class Decoded:
    #: CO2 waveform samples, mmHg, in the order they were measured
    co2: list[float] = field(default_factory=list)
    #: the monitor's own numbers found on the line (EtCO2 and FiCO2 in mmHg)
    numbers: dict[str, float] = field(default_factory=dict)


class CapnoDecoder:
    """The monitor's ASCII lines → CO2 samples and numbers. See the module docstring."""

    def __init__(self, fields: str = "", units: str = "mmHg", barometric_mm_hg: float = 760.0) -> None:
        self.fields = [f.strip().lower() for f in fields.split(",") if f.strip()] if fields else []
        self.units = units
        self.barometric = barometric_mm_hg
        #: lines that gave nothing (for the capture summary)
        self.unknown = 0

    def _co2(self, v: float) -> float:
        return to_mmhg(v, self.units, self.barometric)

    def decode(self, line: str) -> Decoded:
        out = Decoded()
        text = line.strip().strip("\x02\x03")
        if not text:
            return out
        if self.fields:
            self._positional(text, out)
        else:
            self._auto(text, out)
        if not out.co2 and not out.numbers:
            self.unknown += 1
        return out

    def _positional(self, text: str, out: Decoded) -> None:
        parts = [p for p in _SPLIT.split(text) if p]
        for i, name in enumerate(self.fields):
            if name == "co2*":
                out.co2.extend(self._co2(float(p)) for p in parts[i:] if _NUM.match(p))
                return
            if i >= len(parts) or not _NUM.match(parts[i]):
                continue
            v = float(parts[i])
            if name == "co2":
                out.co2.append(self._co2(v))
            elif name in ("etco2", "fico2"):
                out.numbers[name] = self._co2(v)
            elif name in NUMBERS:
                out.numbers[name] = v

    def _auto(self, text: str, out: Decoded) -> None:
        if _NUM.match(text):  # a bare number: a waveform sample
            out.co2.append(self._co2(float(text)))
            return
        pairs = _PAIR.findall(text) or _WORD_NUM.findall(text)
        for key, val in pairs:
            name = ALIASES.get(key.upper())
            if name is None:
                continue
            v = float(val)
            if name == "co2":
                out.co2.append(self._co2(v))
            elif name in ("etco2", "fico2"):
                out.numbers[name] = self._co2(v)
            else:
                out.numbers[name] = v


def split_lines(buf: str) -> tuple[list[str], str]:
    """Complete lines (ended by CR, LF or both) and what is left over."""
    parts = re.split(r"\r\n|\r|\n", buf)
    return parts[:-1], parts[-1]


class CapnostreamSource(ThreadedSource):
    """The monitor's data port: a serial port, or a network address (socket://host:port)."""

    kind = "capnostream"

    def __init__(self, port: str, baud: int = 9600) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.label = f"Capnostream 35 ({port})"
        self._ser = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            import serial
        except ImportError as e:
            raise RuntimeError("pyserial is not installed (pip install pyserial).") from e
        try:
            self._ser = serial.serial_for_url(self.port, baudrate=self.baud, timeout=0.05)
        except (serial.SerialException, OSError, ValueError) as e:
            raise RuntimeError(f"Could not open the Capnostream on {self.port}: {e}") from e
        self._closing = False
        self._thread = threading.Thread(target=self._read_loop, name=f"capnostream {self.port}", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        buf = ""
        ser = self._ser
        assert ser is not None  # opened by start()
        try:
            while not self._closing:
                data = ser.read(max(1, ser.in_waiting))
                if data:
                    lines, buf = split_lines(buf + data.decode("latin-1"))
                    for line in lines:
                        self._q.put(line)
                    if len(buf) > 4096:
                        buf = ""
        except Exception as e:  # unplugged, or the network dropped
            if not self._closing:
                self._q.put(StatusEvent("error", f"Capnostream disconnected: {e}"))

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


class SimulatedCapnostream(Source):
    """A stand-in Capnostream for the simulator: the simulated subject's CO2 as the monitor
    would send it, 20 times a second and ``delay_s`` after it left the airway, with its
    own numbers once a second. It sends ``CO2=<mmHg>`` and ``ETCO2=… RR=… SPO2=… PR=…``
    lines, which the decoder reads by itself (the real monitor's layout may differ)."""

    kind = "capnostream"

    def __init__(self, core, delay_s: float = 2.7) -> None:
        self.core = core
        self.delay_s = delay_s
        self.label = "Capnostream 35 (simulated)"
        self.unplugged = False
        self._last_numbers = -math.inf

    def start(self) -> None:
        self.core.capno_feed = collections.deque()

    def poll(self) -> list[SourceItem]:
        feed = self.core.capno_feed
        now = self.core.device_s
        out: list[SourceItem] = []
        while feed and feed[0][0] + self.delay_s <= now:
            t, mmhg, et, rr = feed.popleft()
            if self.unplugged:
                continue
            out.append(f"CO2={mmhg:.1f}")
            if t - self._last_numbers >= 1.0:
                self._last_numbers = t
                spo2 = self.core.model.params.spo2
                out.append(f"ETCO2={et:.0f} RR={rr:.0f} SPO2={spo2:.0f} PR=72")
        return out

    def stop(self) -> None:
        self.core.capno_feed = None


@dataclass
class _Held:
    t: float  # on the common clock
    line: str


class CapnoMerge:
    """Puts the Capnostream's CO2 into the Arduino's data lines (see the module docstring).

    ``clock()`` is the common timeline in seconds: the computer's clock for real
    devices, the simulated device's clock for the simulator. ``co2_units_pct`` turns
    mmHg into the % the board's lines carry (using the barometric pressure)."""

    def __init__(self, decoder: CapnoDecoder, clock: Callable[[], float], mmhg_to_line: Callable[[float], float]) -> None:
        self.decoder = decoder
        self.clock = clock
        self.mmhg_to_line = mmhg_to_line
        #: (common time, mmHg) of the monitor's recent CO2 samples
        self.samples: collections.deque[tuple[float, float]] = collections.deque(maxlen=400)
        self._held: collections.deque[_Held] = collections.deque()
        #: the Arduino's clock to the common clock: the smallest (arrival - device time) seen
        self._offset: float | None = None
        self._offset_at = 0.0
        self._next_t: float | None = None
        #: the monitor's own numbers, and when they arrived (common clock)
        self.numbers: dict[str, float] = {}
        self.numbers_at = -math.inf
        self.lines_in = 0
        self.samples_in = 0
        #: True once the board's info line has said it sends units (the Uno R4 board)
        self.board_ok = True

    # ---------- the monitor

    def capno_line(self, line: str) -> None:
        self.lines_in += 1
        now = self.clock()
        d = self.decoder.decode(line)
        for v in d.co2:
            # the samples come 20 a second, often in bursts: space them evenly and
            # resynchronize to the arrival time when they drift
            t = now if self._next_t is None or abs(self._next_t - now) > 0.25 else self._next_t
            if math.isfinite(v):
                self.samples.append((t, v))
                self.samples_in += 1
            self._next_t = t + 1 / CAPNO_RATE_HZ
        if d.numbers:
            self.numbers.update(d.numbers)
            self.numbers_at = now

    def co2_at(self, t: float) -> float:
        """The monitor's CO2 at common time ``t`` (mmHg), or NaN when it has none."""
        s = self.samples
        if not s or t - s[-1][0] > CAPNO_TIMEOUT_S or self.clock() - s[-1][0] > CAPNO_TIMEOUT_S:
            return math.nan
        if t >= s[-1][0]:
            return s[-1][1]
        prev = None
        for ts, v in reversed(s):
            if ts <= t:
                if prev is None:
                    return v
                (t1, v1) = prev
                return v + (v1 - v) * (t - ts) / ((t1 - ts) or 1)
            prev = (ts, v)
        return s[0][1] if t >= s[0][0] - 1 / CAPNO_RATE_HZ else math.nan

    @property
    def fresh_numbers(self) -> dict[str, float]:
        """The monitor's own numbers from the last 5 s (empty when it has gone quiet)."""
        return dict(self.numbers) if self.clock() - self.numbers_at <= 5 else {}

    # ---------- the board

    def board_items(self, items: Iterable[SourceItem]) -> list[SourceItem]:
        """The board's lines and events; data lines come back once their CO2 is known
        (now or on a later call)."""
        out: list[SourceItem] = []
        now = self.clock()
        for item in items:
            if not isinstance(item, str):
                out.append(item)
                continue
            p = parse_line(item)
            if isinstance(p, InfoLine) and "FW" in p.fields:
                self.board_ok = "UNITS" in p.fields
                out.append(self._info_line(item))
                continue
            if not isinstance(p, DataLine) or not self.board_ok:
                out.append(item)
                continue
            dev = p.t_ms / 1000
            off = now - dev
            # the smallest delay seen is the truest; let it creep up slowly (clock drift)
            if self._offset is None or off < self._offset or now - self._offset_at > 30:
                self._offset, self._offset_at = off, now
            self._held.append(_Held(dev + self._offset, item))
        out.extend(self.release())
        return out

    def release(self) -> list[str]:
        """The held data lines whose CO2 has arrived (or that have waited long enough)."""
        out: list[str] = []
        now = self.clock()
        last = self.samples[-1][0] if self.samples else -math.inf
        while self._held:
            h = self._held[0]
            if h.t > last and now - h.t < HOLD_MAX_S:
                break
            self._held.popleft()
            out.append(self._with_co2(h))
        return out

    def _with_co2(self, h: _Held) -> str:
        p = parse_line(h.line)
        if not isinstance(p, DataLine) or not p.volts:
            return h.line
        mmhg = self.co2_at(h.t)
        values = list(p.volts)
        values[0] = NOT_ANSWERING if not math.isfinite(mmhg) else self.mmhg_to_line(max(0.0, mmhg))
        return format_data_line(p.seq, p.t_ms, values)

    @staticmethod
    def _info_line(line: str) -> str:
        """The board's info line, saying where the CO2 comes from."""
        parts = line.split(",")
        for i in range(1, len(parts) - 1, 2):
            if parts[i].upper() == "CO2":
                parts[i + 1] = "CAPNOSTREAM35"
                return ",".join(parts)
        return line + ",CO2,CAPNOSTREAM35"


def mmhg_to_line_pct(barometric_mm_hg: float, water_vapor: bool) -> Callable[[float], float]:
    """mmHg → the % the Uno R4 board's lines carry, so the processing gets the same mmHg back."""
    return lambda mmhg: mmhg_to_pct(mmhg, barometric_mm_hg, water_vapor)


# ------------------------------------------------------------------ capture


BAUDS_TO_TRY = (9600, 19200, 38400, 57600, 115200)


def _printable_share(data: bytes) -> float:
    if not data:
        return 0.0
    ok = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return ok / len(data)


def capture(port: str, seconds: float = 60, baud: str | int = "auto", out=None, decoder: CapnoDecoder | None = None, opener=None, log=print) -> dict:
    """Record what a Capnostream sends, to set up the decoder (``co2response --capture``).

    With ``baud="auto"`` each common rate is tried for 3 s and the one giving the most
    readable text is kept. Writes the raw lines to ``out`` (a text file) and returns a
    summary: the rate, the lines, how many CO2 samples and which numbers the decoder
    found in them."""
    if opener is None:
        import serial

        def opener(b: int):
            return serial.serial_for_url(port, baudrate=b, timeout=0.1)

    def read_for(ser, secs: float) -> bytes:
        end = time.monotonic() + secs
        data = b""
        while time.monotonic() < end:
            data += ser.read(max(1, getattr(ser, "in_waiting", 0) or 1))
        return data

    chosen = int(baud) if baud != "auto" else None
    if chosen is None:
        best = (-1.0, BAUDS_TO_TRY[0])
        for b in BAUDS_TO_TRY:
            ser = opener(b)
            try:
                data = read_for(ser, 3)
            finally:
                ser.close()
            share = _printable_share(data)
            log(f"  {b:>6} baud: {len(data):>5} bytes, {share * 100:.0f} % readable")
            if len(data) > 20 and share > best[0]:
                best = (share, b)
        chosen = best[1]
    log(f"Recording {seconds:g} s at {chosen} baud from {port} …")
    ser = opener(chosen)
    try:
        raw = read_for(ser, seconds)
    finally:
        ser.close()
    lines, rest = split_lines(raw.decode("latin-1"))
    if rest:
        lines.append(rest)
    dec = decoder or CapnoDecoder()
    co2 = 0
    found: set[str] = set()
    for line in lines:
        d = dec.decode(line)
        co2 += len(d.co2)
        found.update(d.numbers)
    if out is not None:
        out.write(f"# Capnostream capture: {port}, {chosen} baud, {seconds:g} s, {len(raw)} bytes, {len(lines)} lines\n")
        for line in lines:
            out.write(line.encode("unicode_escape").decode("ascii") + "\n")
    return {"baud": chosen, "bytes": len(raw), "lines": len(lines), "co2_samples": co2, "co2_rate_hz": co2 / seconds if seconds else 0.0, "numbers": sorted(found), "unknown_lines": dec.unknown}
