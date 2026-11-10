"""What the Calibration screen does, with no UI code.

The desktop window (``ui/settings/calibration.py``) and the browser page
(``web/settings_view.py``) both draw a ``CalDraft`` per channel: the calibration
being edited, the reference points captured for it, the syringe stroke, and every
rule about them. The wording both screens show is here too, so the two cannot
drift apart.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from .channels import DEFAULT_CHANNELS, clone_channels
from .jsfmt import precision
from .processing.calibration import (
    apply_calibration,
    fit_polynomial,
    pneumo_flow,
    syringe_k1,
    two_point_linear,
)
from .processing.co2 import pct_to_mmhg

NAMES = {"co2": "CO2 (capnograph)", "flow": "Flow sensor", "pneumo": "Pneumotachograph"}
#: what each channel reads when it is zeroed (room air is 0.04 % CO2)
ZERO_REF = {"co2": 0.04, "flow": 0.0, "pneumo": 0.0}
#: the two reference points offered first: (label A, value A, label B, value B)
SPAN_DEFAULT = {
    "co2": ("Room air", 0.04, "Calibration gas", 5.0),
    "flow": ("No flow", 0.0, "Reference flow", 30.0),
    "pneumo": ("No flow (ΔP = 0)", 0.0, "Reference pressure", 200.0),
}
TYPES = [("linear", "Linear"), ("polynomial", "Polynomial"), ("table", "Lookup table")]
#: a capture averages the live voltage for this long, s
CAPTURE_SEC = 2
CAPTURE_TIP = "Average the live voltage for 2 seconds"
CAPTURING = "Capturing 2 s…"

LEAD = (
    "Each channel turns the ADC voltage into a reading. Type the numbers in by hand, or capture live readings against references (room air and calibration gas, "
    "a reference flowmeter, a 3 L syringe). The preview updates as you edit; nothing changes on the monitor until you press Save."
)
NO_LIVE = "No live data. You can still enter calibrations by hand; capturing readings needs a connected device or the simulator."
FILE_CAL = "A replay is running with the calibration stored in its file. Changes saved here apply after the replay ends."
LINEAR_HINT = "value = gain × volts + offset"
COEFFS_LABEL = "Coefficients c0, c1, c2, …"
COEFFS_ERROR = "Enter numbers separated by commas, lowest order first."
TABLE_NOTE = "Values between points are interpolated; outside the table the end segments are extended."
ZERO_HINT = "Subtracted after the calibration. Set it by hand or with the button below."
K2_HINT = "flow = k1·ΔP + k2·ΔP·|ΔP|. Leave k2 at 0 for a linear (Fleisch-type) pneumotach."
FIT_INTRO = "Capture three or more readings across the range you will measure, then fit a line, a quadratic, or use the points as a lookup table. You can also type volts in by hand."
SYRINGE_STEPS = [
    "Zero the pneumotach with no flow (Enter values → Zero now), and save.",
    "Connect the calibration syringe with the plunger fully out.",
    "Press Start stroke, push the plunger all the way in with one smooth stroke (1–2 s), then press Stop.",
    "Check the measured volume. Apply the new k1, then push again to confirm it reads the syringe volume.",
]
NO_STROKE = "No pneumotach data arrived. Connect the device first."
DEFAULTS_LOADED = "Default values loaded. Save to apply."
RESET_DONE = "All channels reset to the defaults."

#: the buttons under the reference points: (degree, label, points needed)
FITS: dict[str, list[tuple[Any, str, int]]] = {
    "zerospan": [(1, "Compute line", 2)],
    "fit": [(1, "Fit line", 2), (2, "Fit quadratic", 3), ("table", "Use as lookup table", 2)],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digits_for(unit: str) -> int:
    return 3 if unit == "%" else 1 if unit == "Pa" else 2


def methods(cid: str) -> list[tuple[str, str]]:
    """The ways to calibrate a channel, as (id, tab label)."""
    out = [("manual", "Enter values"), ("zerospan", "Zero & span" if cid == "co2" else "Two-point"), ("fit", "Multi-point fit")]
    return out + [("syringe", "3 L syringe")] if cid == "pneumo" else out


def zero_button(cid: str) -> str:
    return "Zero now on room air (0.04 %)" if cid == "co2" else "Zero now (no flow)"


def zerospan_steps(cid: str) -> list[str]:
    if cid == "co2":
        steps = [
            "Flow room air through the sample line, wait for the reading to settle, then capture point A.",
            "Flow the calibration gas (e.g. 5 % CO2), wait for it to settle, then capture point B. Enter the gas's certified value.",
        ]
    else:
        ref = "flow (reference flowmeter)" if cid == "flow" else "pressure (manometer)"
        steps = ["With no flow through the sensor, capture point A.", f"Apply a known {ref} and capture point B. Enter the reference value."]
    return steps + ["Press Compute line, check the preview at the top of this card, then Save."]


def export_text(channels: dict) -> str:
    """The calibration file both front ends export."""
    return json.dumps({**channels, "exportedAt": utc_now()}, indent=2)


def export_name() -> str:
    return f"co2r-calibration-{datetime.now():%Y-%m-%d}.json"


class CalDraft:
    """One channel's calibration card: what is saved, the draft being edited, the
    reference points and the syringe stroke. Methods that tell the operator something
    return the message; those that refuse raise ``ValueError`` with the reason."""

    def __init__(self, cid: str, saved: dict) -> None:
        self.cid = cid
        self.saved_json = json.dumps(saved, sort_keys=True)
        self.draft: dict = copy.deepcopy(saved)
        a, ra, b, rb = SPAN_DEFAULT[cid]
        nan = math.nan
        #: the reference points of the two capture methods
        self.pts: dict[str, list[dict]] = {
            "zerospan": [{"label": a, "ref": ra, "volts": nan}, {"label": b, "ref": rb, "volts": nan}],
            "fit": [{"label": "Point 1", "ref": ra, "volts": nan}, {"label": "Point 2", "ref": rb / 2, "volts": nan}, {"label": "Point 3", "ref": rb, "volts": nan}],
        }
        #: what the last fit of each method said
        self.result = {"zerospan": "", "fit": ""}
        #: syringe stroke (pneumotach only)
        self.volume = 3.0
        self.stroke: tuple[float, float, float] | None = None  # ∫ΔP dt, ∫ΔP|ΔP| dt, seconds
        self._acc: dict | None = None
        self._off: Callable[[], None] | None = None

    # ---------- the draft

    @property
    def saved(self) -> dict:
        return json.loads(self.saved_json)

    @property
    def dirty(self) -> bool:
        return json.dumps(self.draft, sort_keys=True) != self.saved_json

    @property
    def unit(self) -> str:
        return self.draft.get("unit", "")

    def flow_model(self) -> dict:
        return self.draft.get("flowModel") or {"k1": 0.2, "k2": 0}

    def follow(self, saved: dict) -> bool:
        """Saved here, imported or reset: the draft starts again from what is saved.
        True when it changed."""
        sj = json.dumps(saved, sort_keys=True)
        if sj == self.saved_json:
            return False
        self.saved_json = sj
        self.draft = copy.deepcopy(saved)
        return True

    def update(self, **patch: Any) -> None:
        self.draft = {**self.draft, **copy.deepcopy(patch)}

    def discard(self) -> None:
        self.draft = self.saved

    def defaults(self) -> str:
        d = copy.deepcopy(DEFAULT_CHANNELS["channels"][self.cid])
        d["index"] = self.draft["index"]
        self.draft = d
        return DEFAULTS_LOADED

    def save(self, engine: Any) -> str:
        """Make the draft the channel's calibration. ``ValueError`` if it is not valid."""
        nxt = clone_channels(engine.channels)
        nxt["channels"][self.cid] = {**self.draft, "calibratedAt": utc_now()}
        engine.set_channels(nxt)
        self.follow(engine.channels["channels"][self.cid])
        return f"{NAMES[self.cid]} calibration saved."

    def meta(self, when_text: Callable[[str], str]) -> str:
        """The line under the card's title. ``when_text`` writes the saved time (an ISO
        string) the way the front end shows dates."""
        when = self.saved.get("calibratedAt")
        return f"CH{int(self.draft['index'])} · " + (f"saved {when_text(when)}" if when else "default (not yet calibrated)")

    def note(self) -> str:
        """The note of a calibration that has never been done (the defaults explain themselves)."""
        saved = self.saved
        return "" if saved.get("calibratedAt") else saved.get("note", "")

    def live(self, s: Any) -> list[tuple[str, str]]:
        """The three live readings at the top of the card, from a ``Snapshot``."""
        d, u = self.draft, self.unit
        dg = digits_for(u)
        i = int(d["index"])
        v = s.numbers.volts[i] if i < len(s.numbers.volts) else math.nan
        ok = math.isfinite(v)
        value = apply_calibration(d["calibration"], v) - d["zeroOffset"] if ok else math.nan
        if self.cid == "co2":
            p = s.settings.processing
            third = ("As mmHg", f"{_fmt(pct_to_mmhg(value, p.barometric_mm_hg, p.water_vapor), 1)} mmHg")
        elif self.cid == "pneumo":
            third = ("Flow", f"{_fmt(pneumo_flow(self.flow_model(), value), 1)} L/min")
        else:
            saved = self.saved
            sv = apply_calibration(saved["calibration"], v) - saved["zeroOffset"] if ok else math.nan
            third = ("Saved calibration", f"{_fmt(sv, dg)} {u}")
        return [("Raw", f"{v:.4f} V" if ok else "-- V"), ("Preview (unsaved)" if self.dirty else "Calibrated", f"{_fmt(value, dg)} {u}"), third]

    # ---------- typed in by hand

    def switch_type(self, t: str) -> bool:
        """Change the kind of calibration, keeping the curve where it can be kept."""
        cal = self.draft["calibration"]
        if t == cal["type"]:
            return False

        def y(v: float) -> float:
            return apply_calibration(cal, v)

        new: dict
        if t == "linear":
            new = {"type": "linear", "gain": precision(y(1) - y(0), 6), "offset": precision(y(0), 6)}
        elif t == "polynomial":
            new = {"type": "polynomial", "coeffs": [cal["offset"], cal["gain"]] if cal["type"] == "linear" else [precision(y(0), 6), precision(y(1) - y(0), 6)]}
        else:
            new = {"type": "table", "points": [[v, precision(y(v), 6)] for v in (0, 1.25, 2.5, 3.75, 5)]}
        self.update(calibration=new)
        return True

    def set_number(self, key: str, v: float) -> None:
        """Gain or offset of a linear calibration."""
        self.update(calibration={**self.draft["calibration"], key: v})

    def coeffs_text(self) -> str:
        return ", ".join(f"{c:g}" for c in self.draft["calibration"]["coeffs"])

    def set_coeffs(self, text: str) -> None:
        try:
            c = [float(x) for x in text.replace(",", " ").split()]
        except ValueError:
            c = []
        if not c or not all(math.isfinite(x) for x in c):
            raise ValueError(COEFFS_ERROR)
        self.update(calibration={"type": "polynomial", "coeffs": c})

    def _table(self, pts: list) -> None:
        self.update(calibration={"type": "table", "points": pts})

    def set_point(self, i: int, j: int, x: float) -> None:
        """Volts (``j`` 0) or value (``j`` 1) of lookup-table row ``i``. A row that has
        gone meanwhile (a capture finishes 2 s after it was started) is left alone."""
        pts = copy.deepcopy(self.draft["calibration"].get("points", []))
        if 0 <= i < len(pts):
            pts[i][j] = x
            self._table(pts)

    def capture_point(self, i: int, volts: float) -> None:
        self.set_point(i, 0, round(volts, 4))

    def can_remove_point(self) -> bool:
        return len(self.draft["calibration"].get("points", [])) > 2

    def remove_point(self, i: int) -> None:
        self._table([p for k, p in enumerate(self.draft["calibration"]["points"]) if k != i])

    def add_point(self) -> None:
        pts = copy.deepcopy(self.draft["calibration"]["points"])
        pts.append([pts[-1][0] + 0.5, pts[-1][1]])
        self._table(pts)

    def set_zero(self, v: float) -> None:
        self.update(zeroOffset=v)

    def zero_now(self, volts: float) -> str:
        """Zero on the captured voltage: the channel then reads its zero reference."""
        z = apply_calibration(self.draft["calibration"], volts) - ZERO_REF[self.cid]
        self.update(zeroOffset=precision(z, 6))
        return f"Zero offset set to {z:.{digits_for(self.unit)}f} {self.unit}. Save to apply."

    def set_flow(self, key: str, v: float) -> None:
        """k1 or k2 of the pneumotach's pressure-to-flow model."""
        self.update(flowModel={**self.flow_model(), key: v})

    # ---------- reference points (zero and span, multi-point fit)

    def point_name(self, mode: str, i: int) -> str:
        p = self.pts[mode][i]
        return f"{'AB'[i]} · {p['label']}" if mode == "zerospan" else p["label"]

    def ready(self, mode: str) -> list[dict]:
        """The points that have both a reference and a voltage."""
        return [p for p in self.pts[mode] if math.isfinite(p["volts"]) and math.isfinite(p["ref"])]

    def set_ref(self, mode: str, i: int, key: str, v: float | None) -> None:
        """A point's reference (``key`` "ref") or volts. A point that has gone meanwhile
        is left alone."""
        if 0 <= i < len(self.pts[mode]):
            self.pts[mode][i][key] = math.nan if v is None else v

    def capture_ref(self, mode: str, i: int, volts: float) -> float:
        v = round(volts, 4)
        self.set_ref(mode, i, "volts", v)
        return v

    def add_ref(self) -> None:
        pts = self.pts["fit"]
        pts.append({"label": f"Point {len(pts) + 1}", "ref": math.nan, "volts": math.nan})

    def can_remove_ref(self, mode: str) -> bool:
        return mode == "fit" and len(self.pts["fit"]) > 2

    def remove_ref(self, i: int) -> None:
        del self.pts["fit"][i]

    def fits(self, mode: str) -> list[tuple[Any, str, bool]]:
        """The fit buttons of a method: (degree, label, whether there are enough points)."""
        n = len(self.ready(mode))
        return [(deg, text, n >= need) for deg, text, need in FITS[mode]]

    def fit(self, mode: str, deg: Any) -> str:
        """Put a calibration through the ready points into the draft: ``deg`` 1 (a
        line), 2 (a quadratic) or "table". Returns what was done and how well it fits;
        ``ValueError`` if the points cannot be fitted."""
        ready = self.ready(mode)
        u = self.unit
        self.result[mode] = ""
        cal: dict
        if str(deg) == "table":
            cal, how = {"type": "table", "points": sorted([[p["volts"], p["ref"]] for p in ready])}, f"Lookup table from {len(ready)} points"
        elif int(deg) == 1 and len(ready) == 2:
            gain, offset = two_point_linear(ready[0]["volts"], ready[0]["ref"], ready[1]["volts"], ready[1]["ref"])
            cal, how = {"type": "linear", "gain": precision(gain, 7), "offset": precision(offset, 7)}, f"Line through 2 points: gain {gain:.5g} {u}/V, offset {offset:.5g} {u}"
        else:
            c = [precision(x, 7) for x in fit_polynomial([(p["volts"], p["ref"]) for p in ready], int(deg))]
            if int(deg) == 1:
                cal, how = {"type": "linear", "gain": c[1], "offset": c[0]}, f"Least-squares line through {len(ready)} points"
            else:
                cal, how = {"type": "polynomial", "coeffs": c}, f"Quadratic fit through {len(ready)} points"
        self.update(calibration=cal, zeroOffset=0)
        err = max(abs(apply_calibration(cal, p["volts"]) - p["ref"]) for p in ready)
        self.result[mode] = f"{how}. Largest error at the reference points: {err:.{digits_for(u)}f} {u}. Review the preview above, then Save."
        return self.result[mode]

    # ---------- the 3 L syringe (pneumotach)

    @property
    def stroke_running(self) -> bool:
        return self._off is not None

    def stroke_start(self, on_sample: Callable[[Callable[[Any], None]], Callable[[], None]]) -> None:
        """Start integrating the pressure. ``on_sample`` is the engine's: it calls back
        with every processed sample and returns the function that stops it."""
        self.stroke_detach()
        self.stroke = None
        idx = int(self.draft["index"])
        acc = self._acc = {"a1": 0.0, "a2": 0.0, "last_t": None, "last_p": 0.0, "t0": 0.0, "t_end": 0.0, "n": 0}

        def on(p: Any) -> None:
            d = self.draft
            v = p.volts[idx] if idx < len(p.volts) else math.nan
            if not math.isfinite(v):
                return
            pa = apply_calibration(d["calibration"], v) - d["zeroOffset"]
            if acc["last_t"] is not None:
                dt = p.t_sec - acc["last_t"]
                acc["a1"] += (pa + acc["last_p"]) / 2 * dt
                acc["a2"] += (pa * abs(pa) + acc["last_p"] * abs(acc["last_p"])) / 2 * dt
            else:
                acc["t0"] = p.t_sec
            acc["last_t"] = acc["t_end"] = p.t_sec
            acc["last_p"] = pa
            acc["n"] += 1

        self._off = on_sample(on)

    def stroke_stop(self) -> bool:
        """End the stroke. False when no pressure arrived during it."""
        self.stroke_detach()
        a = self._acc
        if not a or a["last_t"] is None:
            return False
        self.stroke = (a["a1"], a["a2"], a["t_end"] - a["t0"])
        return True

    def stroke_detach(self) -> None:
        if self._off:
            self._off()
        self._off = None

    def stroke_live(self) -> str:
        return f"Integrating… ∫ΔP dt = {self._acc['a1']:.1f} Pa·s" if self._off and self._acc else ""

    def set_volume(self, litres: float) -> None:
        self.volume = min(10.0, max(0.1, litres))

    def new_k1(self) -> tuple[float, str]:
        """The k1 that makes the stroke read the syringe's volume, and why not if there is none."""
        if not self.stroke:
            return math.nan, ""
        try:
            return syringe_k1(self.stroke[0], self.stroke[1], self.volume, self.flow_model()["k2"]), ""
        except ValueError as e:
            return math.nan, str(e)

    def can_apply_k1(self) -> bool:
        k1, _ = self.new_k1()
        return math.isfinite(k1) and k1 > 0

    def stroke_stats(self) -> list[tuple[str, str]]:
        if not self.stroke:
            return []
        a1, a2, secs = self.stroke
        fm = self.flow_model()
        measured = abs(fm["k1"] * a1 + fm["k2"] * a2) / 60
        k1, _ = self.new_k1()
        return [
            ("∫ΔP dt", f"{a1:.1f} Pa·s"),
            ("Stroke time", f"{secs:.2f} s"),
            ("Volume with current k1", f"{_fmt(measured, 3)} L ({_fmt((measured - self.volume) / self.volume * 100, 1)} %)"),
            ("New k1", f"{k1:.5g}" if math.isfinite(k1) else "--"),
        ]

    def apply_k1_text(self) -> str:
        k1, _ = self.new_k1()
        return f"Apply k1 = {k1:.5g}" if self.can_apply_k1() else ""

    def apply_k1(self) -> str:
        """Put the stroke's k1 into the draft. Returns the message, "" if there is none to apply."""
        k1, _ = self.new_k1()
        if not self.can_apply_k1():
            return ""
        self.set_flow("k1", precision(k1, 6))
        return f"k1 = {k1:.5g} applied to the draft. Save calibration to use it."


def _fmt(v: float, digits: int) -> str:
    if v is None or not math.isfinite(v):
        return "--"
    s = f"{v:.{digits}f}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s  # no "-0"
