"""What the simulation panel controls: every simulator setting as a slider or a switch,
and ready-made situations that set them all at once.

Both front ends (the desktop window and the browser page) build their simulation
panel from these lists, so they always offer the same controls and situations.
The values reach the monitor the same way real data does: the simulator turns them
into waveforms, the waveforms into protocol lines, and the monitor measures its
numbers from those. So the monitor shows what it *measures*, which is what the
simulated sensors would show, and the panel shows both side by side.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..settings import SimSettings


@dataclass(frozen=True)
class Slider:
    key: str  # SimSettings attribute
    label: str
    unit: str
    lo: float
    hi: float
    step: float
    hint: str = ""
    #: shown as "1:2.0" instead of "2.0"
    prefix: str = ""


@dataclass(frozen=True)
class Switch:
    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True)
class Group:
    title: str
    items: tuple


GROUPS: tuple[Group, ...] = (
    Group(
        "Breathing",
        (
            Slider("rr", "Respiratory rate", "/min", 4, 60, 1, "Sets RR."),
            Slider("vt_ml", "Tidal volume", "mL", 50, 2000, 10, "Sets VT; MV = RR × VT."),
            Slider("ie_ratio", "I:E ratio", "", 1, 4, 0.1, "A shorter inspiration needs a higher peak inspiratory flow.", prefix="1:"),
            Slider("etco2", "End-tidal CO2", "mmHg", 0, 100, 1, "Sets EtCO2. Changes take a few breaths to settle, as in a patient."),
            Slider("fico2", "Inspired CO2", "mmHg", 0, 30, 1, "Sets FiCO2: the capnogram baseline between breaths."),
            Slider("obstruction_pct", "Airway obstruction", "%", 0, 100, 5, "Capnogram shape: 0 % square, 100 % slow-rising “shark fin”."),
        ),
    ),
    Group(
        "CO2 challenge (rebreathing)",
        (
            Switch("ramp", "CO2 rising", "EtCO2 climbs from its set value at the rate below."),
            Slider("ramp_mm_hg_per_min", "Rise rate", "mmHg/min", 1, 60, 1),
            Slider("ramp_max_mm_hg", "Rise stops at", "mmHg", 40, 100, 1),
            Slider("drive", "Breathing response", "×", 0, 5, 0.05, "How much RR and VT rise with CO2. 1 = normal, 0 = none (blunted)."),
        ),
    ),
    Group(
        "Events",
        (
            Switch("apnea", "Apnea", "Breathing stops; the apnea alarm follows."),
            Switch("co2_disconnected", "CO2 sensor unplugged", "Its input goes to the ADC rail; CO2 reads ---."),
            Switch("flow_disconnected", "Flow sensor unplugged"),
            Switch("pneumo_disconnected", "Pneumotach unplugged", "Breaths are then found on the capnogram; VT and MV read ---."),
            Switch("stop_data", "Device stops sending", "The data stream stalls; the no-data alarm follows."),
        ),
    ),
    Group(
        "Signal quality",
        (
            Slider("noise", "Sensor noise", "×", 0, 10, 0.5, "1 = a quiet bench setup."),
            Slider("spike_pct", "ADC spikes", "% of samples", 0, 5, 0.1, "Single-sample glitches; the spike filter removes them."),
            Slider("partial_pct", "Partial breaths", "% of breaths", 0, 50, 1, "No alveolar plateau; the breath screen leaves them out."),
            Slider("line_error_pct", "Serial line errors", "% of lines", 0, 10, 0.1, "One third lost, two thirds with a bad checksum."),
        ),
    ),
    Group(
        "Flow sensor calibration",
        (
            Slider("flow_gain_pct", "Flow sensor gain", "% of true", 50, 150, 1, "Changes the Flow and Peak insp numbers, not VT (the pneumotach)."),
            Slider("flow_offset_lpm", "Flow sensor zero offset", "L/min", -10, 10, 0.5),
        ),
    ),
)

#: at the top of the panel, above the situations: how fast simulated time runs. It is a
#: viewing choice, not part of a situation, so choosing a situation keeps it.
SPEED = Slider(
    "speed",
    "Speed",
    "×",
    1,
    10,
    1,
    "Runs the simulation faster than real time, like a replay at 2× to 10×: at 5× a minute of breathing passes in 12 s, "
    "so the numbers, trend and response plot get there five times sooner. Alarm delays stay in real seconds.",
)

#: under the panel's controls (both front ends)
MEASURED_NOTE = (
    "The monitor measures its numbers from the simulated waveforms, as it would from real sensors, so they can differ a little "
    "from the values set here. A new EtCO2 shows on the next breath; the displayed EtCO2 and RR average the last 3–4 breaths "
    "and MV the last minute, so those take a little longer (raise Speed to get there sooner)."
)

#: next to Speed: whether a whole simulated test is also saved as a session
RECORD_TESTS = Switch(
    "record_tests",
    "Record whole simulated tests",
    "Also saves a whole simulated test as a session (marked Simulator), so the review charts and the PDF report can be made from it too.",
)

SLIDERS: dict[str, Slider] = {SPEED.key: SPEED, **{it.key: it for g in GROUPS for it in g.items if isinstance(it, Slider)}}
SWITCHES: dict[str, Switch] = {RECORD_TESTS.key: RECORD_TESTS, **{it.key: it for g in GROUPS for it in g.items if isinstance(it, Switch)}}


def fmt_value(s: Slider, v: float) -> str:
    """A slider's value as shown next to it."""
    decimals = 0 if float(s.step).is_integer() else (2 if s.step < 0.1 else 1)
    text = f"{v:.{decimals}f}"
    return f"{s.prefix}{text}" + (f" {s.unit}" if s.unit else "")


def slider_index(s: Slider, v: float) -> int:
    """Integer slider position (for widgets that only take whole numbers)."""
    return round((min(s.hi, max(s.lo, v)) - s.lo) / s.step)


def slider_value(s: Slider, i: int) -> float:
    v = s.lo + i * s.step
    return round(v, 6) if not float(s.step).is_integer() else float(round(v))


# ------------------------------------------------------------------ situations


@dataclass(frozen=True)
class Situation:
    id: str
    label: str
    group: str
    description: str
    #: the settings that differ from a normal adult at rest
    values: dict
    #: an event marker placed when the situation is chosen (e.g. "Rebreathing")
    marker: str | None = None
    #: a whole test that runs by itself: baseline, then the CO2 rise with these values,
    #: then the end of the test and recovery (see ``MonitorEngine.apply_situation``)
    test: bool = False


#: a whole simulated test, in simulated seconds: the baseline before the valve is
#: "turned", how long the "operator" takes to end the test once the stop alarm sounds
#: (or the CO2 stops rising), and the recovery that is still simulated afterwards
TEST_BASELINE_SEC = 30.0
TEST_REACTION_SEC = 3.0
TEST_RECOVERY_SEC = 20.0
WHOLE_TEST = "Whole test (runs by itself)"
_WHOLE = (
    " Runs the whole test by itself: 30 s of baseline, then the valve is “turned” and CO2 rises until the stop alarm, then the end of the test and "
    "20 s of recovery. Every graph fills in from it: the waveforms, the EtCO2, MV and RR trends and the response plot with its baseline and fitted line. "
    "Set Speed to 10× to see it all in about 20 s."
)

SITUATIONS: tuple[Situation, ...] = (
    # whole tests: the simulation presses the test keys itself
    Situation(
        "test_normal",
        "Whole rebreathing test",
        WHOLE_TEST,
        "A normal response: breathing rises steadily as CO2 climbs 12 mmHg/min to the 65 mmHg stop limit." + _WHOLE,
        {"ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 67, "drive": 0.8},
        test=True,
    ),
    Situation(
        "test_blunted",
        "Whole test: blunted response",
        WHOLE_TEST,
        "Breathing hardly responds to the CO2 (opioids, sleep, CCHS): an almost flat response line." + _WHOLE,
        {"ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 67, "drive": 0.1, "rr": 12},
        test=True,
    ),
    Situation(
        "test_brisk",
        "Whole test: brisk response",
        WHOLE_TEST,
        "A strong response: a steep line. The CO2 rise stops at 52 mmHg, before the limits, and the test ends there." + _WHOLE,
        {"ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 52, "drive": 2, "vt_ml": 400},
        test=True,
    ),
    # breathing patterns
    Situation("normal", "Normal at rest", "Breathing patterns", "Adult at rest: RR 15 /min, VT 500 mL, EtCO2 38 mmHg.", {}),
    Situation("hyperventilation", "Hyperventilation", "Breathing patterns", "Fast, deep breathing washes CO2 out (anxiety, pain): EtCO2 drops to about 20 mmHg.", {"rr": 28, "vt_ml": 650, "etco2": 20}),
    Situation(
        "hypoventilation",
        "Hypoventilation",
        "Breathing patterns",
        "Slow, shallow breathing lets CO2 build up (sedation, opioids): EtCO2 climbs to 63 mmHg, past the 60 mmHg warning limit.",
        {"rr": 6, "vt_ml": 320, "etco2": 63},
    ),
    Situation("tachypnea", "Rapid shallow", "Breathing patterns", "RR 40 /min with small breaths: MV stays about normal while EtCO2 creeps up.", {"rr": 40, "vt_ml": 220, "etco2": 46}),
    Situation("bradypnea", "Slow deep", "Breathing patterns", "RR 6 /min with large breaths.", {"rr": 6, "vt_ml": 950, "etco2": 40}),
    Situation("exercise", "Light exercise", "Breathing patterns", "MV about 16 L/min: high flows on both flow traces, near the top of their range.", {"rr": 20, "vt_ml": 800, "etco2": 42}),
    Situation("child", "Child (~6 years)", "Breathing patterns", "RR 24 /min, VT 200 mL.", {"rr": 24, "vt_ml": 200, "etco2": 38}),
    # clinical
    Situation(
        "bronchospasm",
        "Bronchospasm / COPD",
        "Clinical",
        "Obstructed airways: a slow-rising “shark fin” capnogram with no flat plateau and a long expiration.",
        {"obstruction_pct": 95, "rr": 20, "ie_ratio": 4, "etco2": 50, "vt_ml": 420},
    ),
    Situation("inspired_co2", "Raised inspired CO2", "Clinical", "Rebreathing exhausted absorber or dead space: the baseline stays well above zero (FiCO2 12 mmHg).", {"fico2": 12, "etco2": 50}),
    Situation("low_output", "Low cardiac output", "Clinical", "Less CO2 reaches the lungs: EtCO2 falls to 12 mmHg though ventilation is normal.", {"etco2": 12, "rr": 14, "vt_ml": 520}),
    Situation("apnea", "Apnea", "Clinical", "Breathing stops. The apnea alarm sounds after 20 s.", {"apnea": True}),
    # CO2 challenge
    Situation(
        "rebreathing",
        "Rebreathing test",
        "CO2 challenge",
        "EtCO2 climbs 12 mmHg/min and breathing rises with it. Places a Rebreathing marker, so the ventilatory-response plot fits the line. "
        "Set Speed to 5× or 10× to run the whole test in well under a minute.",
        {"ramp": True, "ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 58},
        marker="Rebreathing",
    ),
    Situation(
        "blunted",
        "Blunted response",
        "CO2 challenge",
        "The same rise in CO2, but breathing hardly responds (opioids, sleep, CCHS): an almost flat response slope.",
        {"ramp": True, "ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 58, "drive": 0.1, "rr": 12},
        marker="Rebreathing",
    ),
    Situation(
        "brisk",
        "Brisk response",
        "CO2 challenge",
        "CO2 rises to 52 mmHg with a strong breathing response: a steep slope.",
        {"ramp": True, "ramp_mm_hg_per_min": 12, "ramp_max_mm_hg": 52, "drive": 2, "vt_ml": 400},
        marker="Rebreathing",
    ),
    # equipment
    Situation("co2_disconnect", "CO2 sensor unplugged", "Equipment problems", "The capnograph input sits at the ADC rail: a technical alarm, and CO2 reads ---.", {"co2_disconnected": True}),
    Situation("flow_disconnect", "Flow sensor unplugged", "Equipment problems", "The flow sensor input sits at the ADC rail.", {"flow_disconnected": True}),
    Situation("pneumo_disconnect", "Pneumotach unplugged", "Equipment problems", "Breaths are found on the capnogram instead; VT and MV read ---.", {"pneumo_disconnected": True}),
    Situation("no_data", "Device stops sending", "Equipment problems", "The data stream stalls; the no-data alarm sounds after 3 s.", {"stop_data": True}),
    Situation("noisy", "Noisy signal", "Equipment problems", "Five times the sensor noise, plus lost and corrupted serial lines.", {"noise": 5, "line_error_pct": 1.5}),
    Situation("artifacts", "Spikes + partial breaths", "Equipment problems", "ADC spikes and partial breaths, to show the noise removal at work.", {"spike_pct": 1, "partial_pct": 15}),
    Situation(
        "flow_drift",
        "Flow sensor drift",
        "Equipment problems",
        "The flow sensor reads 65 % of the true flow plus 8 L/min, so it clearly disagrees with the pneumotach.",
        {"flow_gain_pct": 65, "flow_offset_lpm": 8},
    ),
)

SITUATION_BY_ID: dict[str, Situation] = {s.id: s for s in SITUATIONS}
SITUATION_GROUPS: tuple[str, ...] = tuple(dict.fromkeys(s.group for s in SITUATIONS))


def situation_settings(sid: str) -> SimSettings:
    """A situation's complete settings: its values on top of a normal adult at rest."""
    sit = SITUATION_BY_ID[sid]
    return dataclasses.replace(SimSettings(), **sit.values, preset=sid, adjusted=False)


# ------------------------------------------------------------------ settings → model

#: the old single-choice scenarios turn on the same switches as the panel
SCENARIO_SWITCH = {"co2Ramp": "ramp", "apnea": "apnea", "co2Disconnect": "co2_disconnected", "noData": "stop_data"}


def model_params(values: dict) -> dict:
    """SimSettings fields (all or some) → PhysioModel parameters."""
    out = {}
    for k, v in values.items():
        if k in ("preset", "adjusted", "speed", "record_tests"):  # these belong to the source and the engine, not the model
            continue
        if k == "ie_ratio":
            out["insp_fraction"] = 1 / (1 + v)
        elif k == "flow_gain_pct":
            out["flow_gain"] = v / 100
        else:
            out[k] = v
    return out


def switch_on(sim: SimSettings, key: str) -> bool:
    """Whether a switch is on, by itself or through the old scenario setting."""
    return bool(getattr(sim, key)) or SCENARIO_SWITCH.get(sim.scenario) == key


def switch_patch(sim: SimSettings, key: str, on: bool) -> dict:
    """The settings change for flipping a switch (turning off a scenario that holds it on)."""
    patch: dict = {key: on}
    if not on and SCENARIO_SWITCH.get(sim.scenario) == key:
        patch["scenario"] = "normal"
    return patch


def scenario_note(sim: SimSettings) -> str:
    """A note when an old scenario adds to what the panel shows."""
    if sim.scenario == "noisy":
        return "The “Noisy signal” scenario adds extra noise and line errors. Choose a situation to reset it."
    if sim.scenario == "artifacts":
        return "The “Spikes + partial breaths” scenario adds spikes and partial breaths. Choose a situation to reset it."
    return ""


def situation_label(sim: SimSettings) -> str:
    sit = SITUATION_BY_ID.get(sim.preset)
    name = sit.label if sit else "Custom"
    return f"{name} (adjusted)" if sim.adjusted else name


#: the numbers the panel reads back from the monitor: (key, label, unit, decimals)
READBACK: tuple[tuple[str, str, str, int], ...] = (
    ("etco2", "EtCO2", "mmHg", 0),
    ("fico2", "FiCO2", "mmHg", 0),
    ("rr", "RR", "/min", 0),
    ("vt", "VT", "mL", 0),
    ("mv", "MV", "L/min", 1),
    ("peak", "Peak insp", "L/min", 0),
)


def monitor_values(numbers) -> dict[str, float]:
    """The monitor's own numbers for the READBACK keys."""
    return {"etco2": numbers.etco2, "fico2": numbers.fico2, "rr": numbers.rr, "vt": numbers.vt, "mv": numbers.mv, "peak": numbers.peak_flow}
