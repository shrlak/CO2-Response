"""Serial protocol shared with the firmware. Spec: docs/protocol.md

Data lines look like ``D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>``; the checksum is the
XOR of every byte between ``D`` and ``*`` as two hex digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar, Union

from .jsfmt import js_round, to_fixed


@dataclass(frozen=True)
class DataLine:
    kind: ClassVar[str] = "data"
    seq: int
    t_ms: int
    volts: list[float]


@dataclass(frozen=True)
class InfoLine:
    kind: ClassVar[str] = "info"
    fields: dict[str, str]
    raw: str


@dataclass(frozen=True)
class ErrorLine:
    kind: ClassVar[str] = "error"
    code: str
    raw: str


@dataclass(frozen=True)
class AckLine:
    kind: ClassVar[str] = "ack"
    fields: list[str]
    raw: str


@dataclass(frozen=True)
class BadLine:
    kind: ClassVar[str] = "bad"
    reason: str  # 'checksum' or 'format'
    raw: str


@dataclass(frozen=True)
class EmptyLine:
    kind: ClassVar[str] = "empty"
    raw: str = field(default="")


ParsedLine = Union[DataLine, InfoLine, ErrorLine, AckLine, BadLine, EmptyLine]

_NUM = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_UINT = re.compile(r"[0-9]+")


def xor_checksum(payload: str) -> str:
    """XOR of every character in ``payload``, as 2 upper-case hex digits."""
    x = 0
    for ch in payload:
        x ^= ord(ch) & 0xFF
    return f"{x:02X}"


def format_data_line(seq: int, t_ms: float, volts) -> str:
    """Build a data line exactly as the firmware does (without the trailing newline)."""
    payload = f",{seq & 0xFFFF},{js_round(t_ms) & 0xFFFFFFFF},{','.join(to_fixed(v, 4) for v in volts)}"
    return f"D{payload}*{xor_checksum(payload)}"


def parse_line(text: str) -> ParsedLine:
    line = text.strip()
    if not line:
        return EmptyLine()
    head = line[0]

    if head == "D":
        star = line.rfind("*")
        if star < 0 or len(line) - star - 1 != 2:
            return BadLine("checksum", line)
        payload = line[1:star]
        given = line[star + 1 :].upper()
        if given != xor_checksum(payload):
            return BadLine("checksum", line)
        parts = payload.split(",")
        # parts[0] is '' (the payload starts with a comma)
        if parts[0] != "" or len(parts) < 4:
            return BadLine("format", line)
        seq_s, t_s, vs = parts[1], parts[2], parts[3:]
        if not _UINT.fullmatch(seq_s) or not _UINT.fullmatch(t_s) or not all(_NUM.fullmatch(v) for v in vs):
            return BadLine("format", line)
        seq = int(seq_s)
        if seq > 0xFFFF:
            return BadLine("format", line)
        return DataLine(seq, int(t_s), [float(v) for v in vs])

    if line.startswith("I,"):
        items = line[2:].split(",")
        fields = {items[i].upper(): items[i + 1] for i in range(0, len(items) - 1, 2)}
        return InfoLine(fields, line)
    if line.startswith("E,"):
        return ErrorLine(line[2:].split(",")[0], line)
    if line.startswith("A,"):
        return AckLine(line[2:].split(","), line)
    return BadLine("format", line)


class SeqTracker:
    """Counts dropped samples from gaps in the 16-bit rolling sequence number.

    The device clock going backwards, or a jump of more than half the range, is
    treated as a device reset, not a loss.
    """

    def __init__(self) -> None:
        self.received = 0
        self.dropped = 0
        self._last: int | None = None
        self._last_t: float | None = None

    def push(self, seq: int, t_ms: float | None = None) -> int:
        """Record one sample; returns the number of samples missing before it."""
        self.received += 1
        reset = t_ms is not None and self._last_t is not None and t_ms < self._last_t
        if t_ms is not None:
            self._last_t = t_ms
        if self._last is None or reset:
            self._last = seq
            return 0
        gap = (seq - self._last - 1 + 0x10000) & 0xFFFF
        self._last = seq
        if gap == 0xFFFF:
            return 0  # duplicate
        if gap > 0x8000:
            return 0  # reset or out of order
        self.dropped += gap
        return gap

    @property
    def drop_rate(self) -> float:
        """Fraction of expected samples that never arrived."""
        expected = self.received + self.dropped
        return self.dropped / expected if expected else 0.0

    def reset(self) -> None:
        self.received = 0
        self.dropped = 0
        self._last = None
        self._last_t = None


class LineSplitter:
    """Splits a stream of text chunks into complete lines."""

    def __init__(self) -> None:
        self._buf = ""

    def push(self, chunk: str) -> list[str]:
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")
        if len(self._buf) > 4096:
            self._buf = ""  # garbage with no newline
        return lines
