"""Which board firmware this version of the app was written for.

Each board says its firmware version in its info line (``I,FW,2.3.0,BOARD,UNO_R4,...``).
When it is not the version this app expects, the monitor says so: older firmware
is missing what the app relies on (the Uno R4's watchdog and sensor set-up resend
came in 2.3.0), and newer firmware means the app itself is out of date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import APP_NAME, __version__

#: by board: the firmware version in that board's config.h (FW_VERSION), and its folder
EXPECTED: dict[str, tuple[str, str]] = {
    "UNO_R4": ("2.3.0", "firmware/CO2ResponseR4"),
    "ADC": ("1.2.0", "firmware/CO2Response"),
}
BOARD_NAME = {"UNO_R4": "Uno R4 board", "ADC": "ADC board"}

_VERSION = re.compile(r"^\d+(\.\d+)*$")


def parse_version(v: str) -> tuple[int, ...] | None:
    """``"2.3.0"`` -> ``(2, 3, 0)``; None for anything else (``sim-2.0.0``, ``co2r-py-0.3.0``)."""
    v = v.strip()
    return tuple(int(x) for x in v.split(".")) if _VERSION.match(v) else None


def board(info: dict[str, str] | None) -> str | None:
    """Which of the project's boards sent this info line, if it is one of them."""
    info = info or {}
    if str(info.get("BOARD", "")).upper() == "UNO_R4":
        return "UNO_R4"
    if "ADC" in info and parse_version(str(info.get("FW", ""))) is not None:
        return "ADC"
    return None


@dataclass(frozen=True)
class FirmwareCheck:
    board: str
    found: str
    expected: str
    folder: str
    state: str  # 'ok' | 'older' | 'newer'

    @property
    def message(self) -> str:
        name = BOARD_NAME[self.board]
        if self.state == "older":
            return f"BOARD FIRMWARE {self.found} IS OLDER THAN THIS APP EXPECTS ({self.expected}): upload {self.folder} to the {name} again"
        if self.state == "newer":
            return f"Board firmware {self.found} is newer than this app ({__version__}) knows: update the app"
        return f"{name} firmware {self.found}"


def check(info: dict[str, str] | None) -> FirmwareCheck | None:
    """How the connected board's firmware compares with the one this app expects.

    None when the device is not one of the project's Arduino boards (the simulator,
    a Python DAQ driver) or does not give a plain version number."""
    b = board(info)
    if b is None:
        return None
    found = str((info or {}).get("FW", ""))
    have = parse_version(found)
    if have is None:
        return None
    expected, folder = EXPECTED[b]
    want = parse_version(expected)
    assert want is not None
    state = "older" if have < want else "newer" if have > want else "ok"
    return FirmwareCheck(b, found, expected, folder, state)


def version_line(info: dict[str, str] | None) -> str:
    """``CO2-Response 0.9.0 · firmware 2.3.0``: the app's version, and the board's once it has said."""
    fw = (info or {}).get("FW")
    return f"{APP_NAME} {__version__}" + (f" · firmware {fw}" if fw else "")


def short_version(info: dict[str, str] | None) -> str:
    """``v0.9.0 · FW 2.3.0``, for the header bar."""
    fw = (info or {}).get("FW")
    return f"v{__version__}" + (f" · FW {fw}" if fw else "")
