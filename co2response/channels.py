"""Channel configuration: which value of a data line each sensor is and how it becomes units.

There are two boards, and so two bundled configurations:

- ``config/channels-digital.json``: the Uno R4 board with digital sensors (a Sensirion
  SFM3304-D flow sensor on I2C and a GSS SprintIR-6S-20 CO2 sensor on UART). Its data
  lines already carry engineering units (% CO2 and L/min), so the calibrations are 1:1
  trims, and the one flow sensor is both the "flow" and the "pneumo" channel.
- ``config/channels.json``: the earlier board, an ADS1115 reading three analog sensors
  (capnograph, flow sensor, pneumotach) and sending volts.

The configuration is a plain JSON document so it can be edited by hand. A calibration
is one of:

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
HARDWARE: tuple[str, ...] = ("digital", "analog")
DEFAULT_CHANNELS_PATH = Path(__file__).with_name("config") / "channels.json"
DIGITAL_CHANNELS_PATH = Path(__file__).with_name("config") / "channels-digital.json"
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


def load_default_channels(hardware: str = "analog") -> dict:
    """The defaults shipped with the app for a board: ``config/channels.json`` (analog
    sensors on an ADC) or ``config/channels-digital.json`` (the Uno R4 board)."""
    with open(DIGITAL_CHANNELS_PATH if hardware == "digital" else DEFAULT_CHANNELS_PATH, encoding="utf-8") as f:
        return validate_channels(json.load(f))


def is_digital(c: dict) -> bool:
    """Does this configuration read a board that sends engineering units, not volts?"""
    return str(c.get("adc", {}).get("type", "")).lower() == "digital"


def single_flow(c: dict) -> bool:
    """One flow sensor does both jobs (the flow waveform, and breaths and volumes), as on
    the Uno R4 board: the "flow" and "pneumo" channels read the same value."""
    ch = c["channels"]
    return int(ch["flow"]["index"]) == int(ch["pneumo"]["index"])


#: the analog board's defaults (the name is older than the second board)
DEFAULT_CHANNELS: dict = load_default_channels()
DEFAULT_DIGITAL_CHANNELS: dict = load_default_channels("digital")
