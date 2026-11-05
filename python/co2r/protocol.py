"""
The CO2-Response line protocol (docs/protocol.md), byte-identical to the dashboard
(web/src/protocol/protocol.ts) and the firmware (firmware/CO2Response/line_format.h).
docs/protocol-fixtures.json holds shared test vectors that all three must reproduce.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence

_Q4 = Decimal("0.0001")
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def checksum(payload: str) -> str:
    """XOR of every byte of ``payload`` (the text between 'D' and '*'), as 2 hex digits."""
    x = 0
    for b in payload.encode("ascii"):
        x ^= b
    return f"{x:02X}"


def fixed4(v: float) -> str:
    """Format like JavaScript ``Number.prototype.toFixed(4)``: halves round away from zero.

    Python's own formatting rounds halves to even (0.03125 -> 0.0312), which would make
    lines differ from the dashboard's and the firmware's.
    """
    if v == 0:
        v = 0.0  # JS prints -0 as "0.0000"
    return str(Decimal(v).quantize(_Q4, rounding=ROUND_HALF_UP))


def format_data_line(seq: int, t_ms: float, volts: Sequence[float]) -> str:
    """``D,<seq>,<t_ms>,<v0>,...*<checksum>`` without the newline."""
    t = int(Decimal(t_ms).quantize(Decimal(1), rounding=ROUND_HALF_UP)) & 0xFFFFFFFF
    payload = f",{seq & 0xFFFF},{t}," + ",".join(fixed4(v) for v in volts)
    return f"D{payload}*{checksum(payload)}"


def info_line(fields: dict[str, Any]) -> str:
    """``I,KEY,value,...``; commas inside values are replaced so pairs stay parseable."""
    parts = []
    for k, v in fields.items():
        parts += [str(k).replace(",", " "), str(v).replace(",", " ").replace("\n", " ")]
    return "I," + ",".join(parts)


def parse_line(raw: str) -> dict[str, Any]:
    """Parse one line. Returns a dict with ``kind`` in data/info/error/ack/bad/empty."""
    line = raw.rstrip("\r").strip()
    if not line:
        return {"kind": "empty"}
    if line[0] == "D":
        star = line.rfind("*")
        if star < 0 or len(line) - star - 1 != 2:
            return {"kind": "bad", "reason": "checksum", "raw": line}
        payload = line[1:star]
        if line[star + 1 :].upper() != checksum(payload):
            return {"kind": "bad", "reason": "checksum", "raw": line}
        parts = payload.split(",")
        if parts[0] != "" or len(parts) < 4:
            return {"kind": "bad", "reason": "format", "raw": line}
        _, seq_s, t_s, *vs = parts
        if not seq_s.isdigit() or not t_s.isdigit() or not all(_NUM.match(v) for v in vs):
            return {"kind": "bad", "reason": "format", "raw": line}
        seq = int(seq_s)
        if seq > 0xFFFF:
            return {"kind": "bad", "reason": "format", "raw": line}
        return {"kind": "data", "seq": seq, "tMs": int(t_s), "volts": [float(v) for v in vs]}
    if line.startswith("I,"):
        items = line[2:].split(",")
        fields = {items[i].upper(): items[i + 1] for i in range(0, len(items) - 1, 2)}
        return {"kind": "info", "fields": fields, "raw": line}
    if line.startswith("E,"):
        return {"kind": "error", "code": line[2:].split(",")[0], "raw": line}
    if line.startswith("A,"):
        return {"kind": "ack", "fields": line[2:].split(","), "raw": line}
    return {"kind": "bad", "reason": "format", "raw": line}


class SeqTracker:
    """Counts dropped samples from gaps in the 16-bit sequence number (see the TS version)."""

    def __init__(self) -> None:
        self.received = 0
        self.dropped = 0
        self._last: int | None = None
        self._last_t: int | None = None

    def push(self, seq: int, t_ms: int | None = None) -> int:
        self.received += 1
        reset = t_ms is not None and self._last_t is not None and t_ms < self._last_t
        if t_ms is not None:
            self._last_t = t_ms
        if self._last is None or reset:
            self._last = seq
            return 0
        gap = (seq - self._last - 1) & 0xFFFF
        self._last = seq
        if gap == 0xFFFF or gap > 0x8000:
            return 0
        self.dropped += gap
        return gap
