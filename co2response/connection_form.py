"""What the Connection screen offers, with no UI code.

The desktop window (``ui/settings/connection.py``) and the browser page
(``web/settings_view.py``) reach the Arduino differently (the computer's serial
ports; Web Serial), so each draws its own USB card. Everything else on the screen
is the same, and is here: the choices, the limits, the wording and the checks.
"""

from __future__ import annotations

from typing import Any

from .sim.controls import SITUATION_BY_ID, SITUATIONS

BAUDS = [9600, 57600, 115200, 230400, 250000, 500000]
BAUD_HINT = "Must match BAUD_RATE in firmware/CO2Response/config.h."
RECONNECT_LABEL = "Reconnect when plugged back in"
RECONNECT_HINT = "If the USB cable comes out, the device-disconnected alarm sounds; plugging the same Arduino back in reconnects without a click, and recording continues."

#: sources that take a RATE command
RATE_KINDS = ("serial", "bridge", "simulator")
RATE_LABEL = "Sample rate per channel"
RATE_MIN, RATE_MAX, RATE_DEFAULT = 1, 200, 100
RATE_HINT = "Sends RATE to the device (1–200 Hz). 100 Hz is enough for breathing waveforms."

SIM_NOTE = "Generates realistic capnogram, flow and pneumotach signals through the same protocol, parser and processing as the Arduino. "
SIM_ADJUSTED = "Values changed by hand in the simulation panel."
#: the simulator's three quick values: (settings key, label, unit, lowest, highest, step)
SIM_FIELDS = [
    ("rr", "Respiratory rate", "/min", 4, 60, 1),
    ("etco2", "Baseline EtCO2", "mmHg", 10, 80, 1),
    ("vt_ml", "Tidal volume", "mL", 100, 1500, 10),
]

REPLAY_SPEEDS = [(1, "1×"), (2, "2×"), (5, "5×"), (10, "10×")]
FILE_CAL_LABEL = "Use the calibration saved in the file"
FILE_CAL_HINT = "On: replays exactly as recorded. Off: re-processes the raw volts with the current calibration."
BRIDGE_LABEL = "Server address"


def status_text(s: Any) -> str:
    """The connection in a few words, from a ``Snapshot``."""
    return {"connected": f"connected to {s.source_label}", "connecting": "connecting…", "error": f"lost ({s.status_detail})"}.get(s.status, "not connected")


def device_info(s: Any) -> list[tuple[str, str]]:
    """What the connected device said about itself."""
    return [(str(k), str(v)) for k, v in (s.info or {"INFO": "waiting for reply"}).items()]


def takes_rate(s: Any) -> bool:
    return s.source_kind in RATE_KINDS


def clamp_rate(hz: float | None) -> int:
    return round(min(RATE_MAX, max(RATE_MIN, hz if hz else RATE_DEFAULT)))


def rate_commands(hz: float | None) -> list[str]:
    """Set the sample rate, then ask the device what it is now doing."""
    return [f"RATE {clamp_rate(hz)}", "INFO"]


def situation_options() -> list[tuple[str, str]]:
    return [(x.id, f"{x.group}: {x.label}") for x in SITUATIONS]


def situation_hint(sim: Any) -> str:
    """What the chosen situation is, or that its values have since been changed."""
    sit = SITUATION_BY_ID.get(sim.preset)
    return sit.description if sit and not sim.adjusted else SIM_ADJUSTED


def sim_button(s: Any) -> str:
    return "Restart simulator" if s.source_kind == "simulator" else "Start simulator"


def clamp_sim(key: str, v: float) -> float:
    """A simulator quick value, kept inside its limits."""
    for k, _, _, lo, hi, _ in SIM_FIELDS:
        if k == key:
            return min(hi, max(lo, v))
    return v


def replayable(library: Any) -> list:
    """The saved sessions that can be replayed (not the one being recorded)."""
    return [r for r in library.list() if r.status != "recording"]


def for_replay(session: Any) -> Any:
    """A parsed session, if there is anything in it to replay."""
    if not session.n:
        raise ValueError("it has no samples")
    return session
