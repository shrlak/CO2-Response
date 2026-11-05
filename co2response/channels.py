"""Channel configuration: which ADC input each sensor is on and how volts become units.

The configuration is a plain JSON document (``co2response/config/channels.json``) so it
can be edited by hand. A calibration is one of:

- ``{"type": "linear", "gain": g, "offset": o}``       value = g·V + o
- ``{"type": "polynomial", "coeffs": [c0, c1, …]}``    value = c0 + c1·V + c2·V² + …
- ``{"type": "table", "points": [[V, value], …]}``     piecewise-linear lookup
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .jsfmt import is_num

CHANNEL_IDS: tuple[str, ...] = ("co2", "flow", "pneumo")
RAIL_CHECKS = ("both", "high", "low", "none")
DEFAULT_CHANNELS_PATH = Path(__file__).with_name("config") / "channels.json"
DEFAULT_FLOW_MODEL = {"k1": 0.2, "k2": 0}


def clone_channels(c: dict) -> dict:
    return copy.deepcopy(c)


def validate_channels(c) -> dict:
    """Check a channels config and fill in optional fields. Raises ValueError with a readable message."""
    if not isinstance(c, dict) or not isinstance(c.get("channels"), dict) or not isinstance(c.get("adc"), dict):
        raise ValueError('Missing "adc" or "channels".')
    for cid in CHANNEL_IDS:
        ch = c["channels"].get(cid)
        if not isinstance(ch, dict):
            raise ValueError(f'Missing channel "{cid}".')
        cal = ch.get("calibration")
        if not isinstance(cal, dict):
            raise ValueError(f'Channel "{cid}" has no calibration.')
        kind = cal.get("type")
        if kind == "linear":
            if not is_num(cal.get("gain")) or not is_num(cal.get("offset")):
                raise ValueError(f'Channel "{cid}": gain and offset must be numbers.')
        elif kind == "polynomial":
            coeffs = cal.get("coeffs")
            if not isinstance(coeffs, list) or not coeffs or not all(is_num(x) for x in coeffs):
                raise ValueError(f'Channel "{cid}": coefficients must be numbers.')
        elif kind == "table":
            pts = cal.get("points")
            if not isinstance(pts, list) or len(pts) < 2:
                raise ValueError(f'Channel "{cid}": a table needs at least 2 points.')
            if not all(isinstance(p, list) and len(p) == 2 and all(is_num(v) for v in p) for p in pts):
                raise ValueError(f'Channel "{cid}": table points must be [volts, value] pairs.')
        else:
            raise ValueError(f'Channel "{cid}": unknown calibration type.')
        if not is_num(ch.get("index")):
            raise ValueError(f'Channel "{cid}": index must be a number.')
        if not is_num(ch.get("zeroOffset")):
            ch["zeroOffset"] = 0
        if ch.get("railCheck") not in RAIL_CHECKS:
            ch["railCheck"] = "high" if cid == "co2" else "both"
        ch.setdefault("label", cid)
        ch.setdefault("unit", "")
        ch.setdefault("calibratedAt", None)
    if not isinstance(c["channels"]["pneumo"].get("flowModel"), dict):
        c["channels"]["pneumo"]["flowModel"] = dict(DEFAULT_FLOW_MODEL)
    return c


def load_default_channels() -> dict:
    """The defaults shipped with the app (``co2response/config/channels.json``)."""
    with open(DEFAULT_CHANNELS_PATH, encoding="utf-8") as f:
        return validate_channels(json.load(f))


DEFAULT_CHANNELS: dict = load_default_channels()
