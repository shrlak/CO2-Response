"""Number, time and JSON formatting that matches JavaScript exactly.

The first version of this app was written in TypeScript, and the session files and
firmware line format were defined by its formatter. These helpers reproduce that
output byte for byte, so files written by either version are interchangeable:

- ``to_fixed`` rounds exact ties away from zero like ``Number.toFixed`` (Python's
  ``format`` rounds them to even).
- ``js_round`` rounds halves up like ``Math.round`` (Python's ``round`` rounds to even).
- ``js_num`` / ``js_json`` print numbers the way JavaScript does (``760`` not ``760.0``).
- ``iso_ms`` prints a millisecond timestamp like ``Date.toISOString``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

NAN = float("nan")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def is_num(x: Any) -> bool:
    """True for a finite int or float (but not a bool), like ``Number.isFinite``."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def js_round(x: float) -> int:
    """``Math.round``: the nearest integer, halves rounded towards +infinity."""
    r = math.floor(x)
    return r + 1 if x - r >= 0.5 else r


def to_fixed(x: float, digits: int) -> str:
    """``Number.prototype.toFixed``."""
    x = float(x)
    if not math.isfinite(x):
        return "NaN" if math.isnan(x) else ("Infinity" if x > 0 else "-Infinity")
    if x == 0:
        x = 0.0  # (-0).toFixed(2) is "0.00"
    s = f"{x:.{digits}f}"
    scaled = abs(x) * 10.0**digits
    frac = scaled - math.floor(scaled)
    if abs(frac - 0.5) < 1e-6:
        # possibly an exact tie: JavaScript rounds it away from zero, Python to even
        s = str(Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    return s


def to_exponential(x: float, digits: int) -> str:
    """``Number.prototype.toExponential``, e.g. ``1.2e-3``."""
    mant, exp = f"{float(x):.{digits}e}".split("e")
    return f"{mant}e{int(exp):+d}"


def js_num(x: float | int) -> str:
    """``String(x)`` for a number: shortest round-trip digits, no trailing ``.0``."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    d = Decimal(repr(abs(x)))
    _, dig, exp = d.as_tuple()
    digits = "".join(map(str, dig)).rstrip("0") or "0"
    exp += len(dig) - len(digits)
    k = len(digits)
    n = k + exp  # value = 0.digits × 10^n
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mant}e{'+' if e > 0 else '-'}{abs(e)}"
    return sign + body


def js_json(obj: Any) -> str:
    """``JSON.stringify(obj)``: compact, numbers printed the JavaScript way."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return js_num(obj) if math.isfinite(obj) else "null"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, dict):
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{js_json(v)}" for k, v in obj.items()) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(js_json(v) for v in obj) + "]"
    raise TypeError(f"cannot serialize {type(obj).__name__}")


_iso_cache: list = [None, ""]


def iso_ms(ms: float) -> str:
    """``new Date(ms).toISOString()``, e.g. ``2026-11-05T05:00:00.010Z``."""
    ms = int(ms)
    sec, rem = divmod(ms, 1000)
    if _iso_cache[0] != sec:
        _iso_cache[0] = sec
        _iso_cache[1] = (_EPOCH + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{_iso_cache[1]}.{rem:03d}Z"


def parse_iso_ms(s: str) -> float:
    """``Date.parse`` for ISO 8601 timestamps; NaN when the text is not one."""
    s = (s or "").strip()
    if not s:
        return NAN
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return NAN
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d - _EPOCH) / timedelta(milliseconds=1)


def naive_sum(xs) -> float:
    """Left-to-right float sum (Python's ``sum`` compensates, which changes the last bits)."""
    s = 0.0
    for x in xs:
        s += x
    return s


def precision(x: float, digits: int) -> float:
    """``+x.toPrecision(digits)``: round to significant digits."""
    if not math.isfinite(x) or x == 0:
        return x
    return float(f"{x:.{digits}g}")
