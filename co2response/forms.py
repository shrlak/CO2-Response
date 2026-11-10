"""The settings forms, described once.

The desktop window (``ui/spec_form.py``) and the browser page (``web/spec_form.py``)
both build their alarm, signal-processing and test forms from the lists here, so a
setting is added, worded and limited in one place and the two front ends cannot drift
apart. Nothing here touches a user interface.

Each ``Field`` edits one attribute of a settings dataclass (``AlarmSettings``,
``ProcessingOptions`` or ``TestSettings``):

- ``number``    a number between ``lo`` and ``hi``
- ``toggle``    a switch
- ``choice``    one of a few values, shown as segmented buttons
- ``optional``  a number that can be switched off (stored as None): a switch row, and
                under it the number row (``value_label``) while it is on
- ``blank``     a number that may be left empty (stored as None)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Field:
    #: the attribute it edits
    key: str
    label: str
    kind: str = "number"
    unit: str = ""
    lo: float = -1e9
    hi: float = 1e9
    step: float = 1
    hint: str = ""
    #: stored as a whole number
    integer: bool = False
    #: shown value = stored value × scale (deadband: stored L/min, shown L/s)
    scale: float = 1
    #: decimals kept of the shown value
    digits: int = 3
    #: 'optional': the value it starts with when switched on, and the number row's label
    default: float = 0
    value_label: str = ""
    #: 'choice': (value, text) pairs, and the hint for each value
    options: tuple = ()
    hints: dict = field(default_factory=dict)
    #: the row is shown only while this is true for the settings object
    when: Callable[[Any], bool] | None = None
    #: short name for element ids and widget attributes (defaults to ``key``)
    id: str = ""
    #: extra text after the hint, worked out from a live snapshot (e.g. a measured value)
    live: Callable[[Any], str] | None = None

    @property
    def name(self) -> str:
        return self.id or self.key

    def shown(self, obj: Any) -> bool:
        return self.when is None or bool(self.when(obj))

    def to_display(self, stored: float | None) -> float | None:
        if stored is None or not math.isfinite(stored):
            return None
        return round(stored * self.scale, self.digits)

    def to_stored(self, shown: float) -> float:
        """Clamp a typed value and convert it to what is stored."""
        v = min(self.hi, max(self.lo, shown)) / self.scale
        return round(v) if self.integer else v

    def hint_for(self, obj: Any) -> str:
        if self.kind == "choice":
            return self.hints.get(getattr(obj, self.key), self.hint)
        return self.hint


def _lag_note(snapshot: Any) -> str:
    """What the monitor measures right now, to help choose the CO2 delay."""
    lag = getattr(getattr(snapshot, "numbers", None), "co2_lag_sec", math.nan)
    if lag is None or not math.isfinite(lag):
        return ""
    more = " Longer than that is the sensor's delay: add it here." if lag > 0.45 else ""
    return f" Measured now: CO2 rises {lag:.2f} s after expiration begins; 0.1–0.3 s is normal dead-space washout.{more}"


ALARM_FIELDS: tuple[Field, ...] = (
    Field(
        "source",
        "Alarm on",
        "choice",
        options=(("etco2", "EtCO2 per breath"), ("live", "Live CO2")),
        hints={
            "etco2": "Compared once per breath, after each expiration.",
            "live": "Live CO2 alarms when the waveform itself stays past a limit, e.g. raised inspired CO2 during rebreathing.",
        },
    ),
    Field(
        "high_mm_hg",
        "Warning limit (high CO2)",
        unit="mmHg",
        lo=5,
        hi=150,
        hint="Drawn as the dashed line on the capnogram and trend. A medium-priority alarm while a stop limit is set; high priority on its own.",
        id="high",
    ),
    Field(
        "stop_mm_hg",
        "Stop limit",
        "optional",
        unit="mmHg",
        lo=5,
        hi=150,
        default=65,
        value_label="Stop the test at",
        hint="A high-priority STOP TEST alarm as soon as CO2 stays at or above this for half a second, or one breath ends above it. It does not wait for a second breath, and it sounds even if the warning was silenced.",
        id="stop",
    ),
    Field(
        "low_mm_hg",
        "Low CO2 limit",
        "optional",
        unit="mmHg",
        lo=0,
        hi=100,
        default=25,
        value_label="Low limit",
        hint="Optional. Helps catch apnea, a leak or a disconnected sample line.",
        id="low",
    ),
    Field(
        "auto_valve",
        "Switch the valve from the app",
        "toggle",
        hint="With a valve driver on the board's valve output: to the bag at Start rebreathing, and back to room air at End test, at the stop alarm, "
        "on apnea and at an emergency stop. The board also returns the valve to room air by itself if the app stops. Off: the operator turns a manual valve.",
        id="autovalve",
        live=lambda s: " The connected device has no valve output." if getattr(s, "status", "") == "connected" and getattr(s, "valve", None) is None else "",
    ),
    Field(
        "delay_breaths",
        "Trigger delay",
        unit="breaths",
        lo=1,
        hi=10,
        integer=True,
        hint="Consecutive breaths past the warning or low limit before alarming. Stops a single breath from alarming.",
        when=lambda a: a.source == "etco2",
        id="delayb",
    ),
    Field("delay_sec", "Trigger delay", unit="s", lo=0, hi=60, step=0.5, when=lambda a: a.source == "live", id="delays"),
    Field("hysteresis_mm_hg", "Hysteresis", unit="mmHg", lo=0, hi=10, step=0.5, hint="A high alarm clears only below limit − hysteresis.", id="hyst"),
    Field("apnea_sec", "Apnea alarm after", unit="s", lo=0, hi=120, hint="High-priority alarm when no breath is detected for this long. 0 turns it off.", id="apnea"),
    Field(
        "rebreathing_max_sec",
        "Rebreathing time limit",
        unit="s",
        lo=0,
        hi=1800,
        step=10,
        hint="Medium alarm when this long has passed since the Rebreathing marker and the test has not been ended. 0 turns it off.",
        id="testmax",
    ),
    Field(
        "leak_pct",
        "Volume mismatch notice",
        unit="%",
        lo=0,
        hi=100,
        hint="A notice when inspired and expired volume differ by more than this over 5 breaths: a leak at the mouthpiece or mask, or a pneumotach zero that has drifted. 0 turns it off.",
        id="leak",
    ),
    Field(
        "calibration_max_age_h",
        "Calibration reminder after",
        unit="h",
        lo=0,
        hi=8760,
        hint="A notice while a real device is connected and a channel was calibrated longer ago than this, or never. 0 turns it off.",
        id="calage",
    ),
    Field("silence_sec", "Silence duration", unit="s", lo=10, hi=120, hint="Audio comes back on by itself after this. Maximum 120 s.", id="silence"),
)

PROCESSING_FIELDS: tuple[Field, ...] = (
    Field(
        "barometric_mm_hg",
        "Barometric pressure",
        unit="mmHg",
        lo=500,
        hi=800,
        hint="Used to convert % CO2 to mmHg. Pittsburgh is typically about 735 mmHg; enter the lab's reading for accuracy.",
        id="pb",
    ),
    Field("water_vapor", "Water-vapor correction (BTPS)", "toggle", hint="Subtracts 47 mmHg before converting % to mmHg.", id="wv"),
    Field("filter_on", "Low-pass filter", "toggle", hint="2nd-order Butterworth on every channel.", id="filt"),
    Field("filter_hz", "Filter cutoff", unit="Hz", lo=1, hi=40, step=0.5, when=lambda p: p.filter_on, id="filthz"),
    Field(
        "deadband_lpm",
        "Breath detection deadband",
        unit="L/s",
        lo=0.005,
        hi=0.5,
        step=0.005,
        scale=1 / 60,
        hint="Flow must pass ± this to count as a new inspiration. Raise it if noise is counted as breaths.",
        id="deadband",
    ),
    Field("min_breath_sec", "Minimum breath length", unit="s", lo=0.3, hi=5, step=0.1, id="minbreath"),
    Field(
        "despike",
        "Remove spikes (Hampel filter)",
        "toggle",
        hint="Replaces single glitchy samples with the median of their neighbours before anything is calculated. Keeps real fast changes such as the capnogram upstroke. Adds 30 ms of delay.",
    ),
    Field(
        "reject_bad_breaths",
        "Leave noisy breaths out",
        "toggle",
        hint="Each breath is checked before it is used: too noisy (signal-to-noise below 8), a partial breath with no alveolar plateau, an impossible value, flow beyond the sensor's range, or an isolated EtCO2 jump. Those breaths are left out of the numbers, the EtCO2 trend, the response plot and the EtCO2 alarms, but stay in the recording, marked. A run of them raises a technical alarm.",
        id="reject",
    ),
    Field(
        "co2_delay_ms",
        "CO2 signal delay",
        unit="ms",
        lo=0,
        hi=5000,
        step=10,
        hint="How late the CO2 signal is behind the flow (the sample line of a sidestream analyser). The flow is held back by this much so each breath's CO2 lines up with it. 0 for a mainstream sensor.",
        id="co2delay",
        live=_lag_note,
    ),
    Field(
        "volume_btps",
        "Report volumes at body conditions (BTPS)",
        "toggle",
        hint="Multiplies pneumotach flow, VT and MV by the factor from room temperature, saturated (as measured) to 37 °C, saturated: about ×1.09 at 22 °C. Off: volumes are as measured. Agree the convention with the clinicians before comparing results.",
        id="btps",
    ),
    Field("ambient_temp_c", "Room temperature", unit="°C", lo=0, hi=40, step=0.5, when=lambda p: p.volume_btps, id="roomtemp"),
    Field(
        "rebreathing_gas_factor",
        "Bag gas correction",
        unit="×",
        lo=0.5,
        hi=1.5,
        step=0.01,
        hint="Pneumotach flow is multiplied by this from the Rebreathing marker to the end of the test. A pneumotach calibrated in air over-reads in an oxygen-rich bag: about 0.90 in pure O2, 1 in air. Leave it at 1 if the syringe calibration was done with the bag gas.",
        id="gasfactor",
    ),
)

TEST_FIELDS: tuple[Field, ...] = (
    Field(
        "response_skip_sec",
        "Leave out the start of rebreathing",
        unit="s",
        lo=0,
        hi=300,
        step=5,
        hint="Breaths in the first seconds after the Rebreathing marker (the coached deep breaths, and the bag, lungs and blood coming to equilibrium) are drawn faintly and not fitted.",
        id="skip",
    ),
    Field("height_cm", "Subject height", "blank", unit="cm", lo=30, hi=250, hint="Height and weight go in the file header so the slope can be normalized for body size. Leave empty if not needed.", id="height"),
    Field("weight_kg", "Subject weight", "blank", unit="kg", lo=1, hi=400, id="weight"),
    Field("age_years", "Subject age", "blank", unit="years", lo=0, hi=120, hint="Age in years only. Never a date of birth, a name or an MRN.", id="age"),
)


SPO2_FIELDS: tuple[Field, ...] = (
    Field(
        "enabled",
        "SpO2 channel connected",
        "toggle",
        hint="A pulse oximeter's analog output on the spare ADC input (A3), as a safety channel: a tile on the monitor, a low-SpO2 alarm, and a column in the recording. "
        "The firmware must be built with SPO2_CHANNEL 1 so the board sends the fourth value.",
        id="spo2on",
    ),
    Field("gain_pct_per_v", "SpO2 scaling", unit="% per V", lo=-1000, hi=1000, step=1, hint="SpO2 % = scaling × volts + offset. From the oximeter's manual (often 1 V = 100 %).", when=lambda o: o.enabled, id="spo2gain"),
    Field("offset_pct", "SpO2 offset", unit="%", lo=-500, hi=500, step=1, when=lambda o: o.enabled, id="spo2offset"),
    Field(
        "low_pct",
        "Low SpO2 alarm below",
        unit="%",
        lo=0,
        hi=100,
        hint="High-priority alarm when SpO2 stays below this. 0 turns it off. With the valve switched from the app, it also returns the valve to room air.",
        when=lambda o: o.enabled,
        id="spo2low",
    ),
    Field("low_delay_sec", "Low SpO2 alarm after", unit="s", lo=0, hi=120, when=lambda o: o.enabled, id="spo2delay"),
)


def patch_for(f: Field, obj: Any, shown: float | None = None, on: bool | None = None, choice: Any = None) -> dict:
    """The settings change for an edit of field ``f``: a typed number (``shown``), a
    switch (``on``) or a choice. Empty when nothing would change."""
    cur = getattr(obj, f.key)
    if f.kind == "toggle":
        new: Any = bool(on)
    elif f.kind == "choice":
        new = choice
    elif f.kind == "optional" and on is not None:
        new = (cur if cur is not None else f.default) if on else None
    elif shown is None:
        new = None if f.kind == "blank" else cur
    else:
        new = f.to_stored(shown)
    return {} if new == cur else {f.key: new}
