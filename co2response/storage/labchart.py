"""Import a LabChart (ADInstruments PowerLab) text export as a session.

CHOP records the rebreathing test with PowerLab and exports it from LabChart as text
(File > Export > Text, with the header and time column). Such a file looks like this,
tab-separated:

    Interval=	0.01 s
    ExcelDateTime=	4.6331e+004	11/05/26 01:02:03.000
    TimeFormat=	StartOfBlock
    ChannelTitle=	Flow	CO2	SAT
    Range=	10.000 V	10.000 V	10.000 V
    UnitName=	mL/s	mmHg	%
    0	12.5	0.31	98	#* Baseline
    0.01	14.1	0.30	98

``labchart_to_session`` turns it into a session file (format v4) by running the
monitor's own processing over it, so breaths, the noise screen, the response and the
report are worked out exactly as for a recording made here. The already-calibrated
flow and CO2 are written as "volts" through a made-up linear calibration that is
stored in the file's header, which keeps the file self-describing and replayable.

Flow may be in mL/s, L/s or L/min, with either sign for inspiration (it is taken from
the CO2: CO2 is high while breathing out). CO2 may be in mmHg, % or kPa. An SpO2 /
SAT channel in % is kept as the SpO2 column. Comments become event markers.

**Not checked against a real CHOP export** (none was available): it follows
LabChart's documented text layout. If a file is refused, the message says which
channels it found.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..channels import DEFAULT_CHANNELS, clone_channels
from ..jsfmt import parse_iso_ms
from ..processing.metrics import minute_ventilation, respiratory_rate
from ..processing.pipeline import Pipeline
from ..settings import ProcessingOptions, Spo2Settings
from .session_file import BreathCells, SessionMeta, clean_text, format_header, format_row

#: the import's own barometric pressure: only used to pass mmHg through the % column and back
IMPORT_PB = 760.0
#: sample rates above this are averaged down (the monitor's processing is tuned for about 100 Hz)
MAX_RATE_HZ = 200.0
KPA_TO_MMHG = 7.50062

_HEADER = re.compile(r"^([A-Za-z]+)=\t?(.*)$")


@dataclass
class LabChartData:
    interval_s: float
    titles: list[str]
    units: list[str]
    #: seconds from the start, always increasing (blocks are joined end to end)
    t: np.ndarray
    channels: list[np.ndarray]
    comments: list[tuple[float, str]] = field(default_factory=list)
    #: when the recording started, ms since 1970 (NaN if the file does not say)
    started_ms: float = math.nan


def looks_like_labchart(text: str) -> bool:
    """True for the start of a LabChart text export (its header lines)."""
    head = text[:4000]
    return "Interval=" in head and ("ChannelTitle=" in head or "UnitName=" in head)


def _num(s: str) -> float:
    s = s.strip()
    if not s:
        return math.nan
    try:
        return float(s)
    except ValueError:
        try:
            return float(s.replace(",", "."))  # a decimal comma
        except ValueError:
            return math.nan


def _start_ms(value: str) -> float:
    """``ExcelDateTime=`` gives days since 1899-12-30 (then the same as text)."""
    days = _num(value.split("\t")[0])
    return (days - 25569.0) * 86400_000 if math.isfinite(days) and days > 20000 else math.nan


def parse_labchart(text: str) -> LabChartData:
    interval = math.nan
    titles: list[str] = []
    units: list[str] = []
    started = math.nan
    ts: list[float] = []
    rows: list[list[float]] = []
    comments: list[tuple[float, str]] = []
    offset = 0.0
    last = -math.inf
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        m = _HEADER.match(line)
        if m and not line[0].isdigit():
            key, value = m.group(1), m.group(2)
            if key == "Interval":
                interval = _num(value.split()[0]) if value.split() else math.nan
                if "ms" in value:
                    interval /= 1000
            elif key == "ChannelTitle":
                titles = [x.strip() for x in value.split("\t")]
            elif key == "UnitName":
                units = [x.strip() for x in value.split("\t")]
            elif key == "ExcelDateTime" and not math.isfinite(started):
                started = _start_ms(value)
            continue
        cells = line.split("\t")
        t = _num(cells[0])
        if not math.isfinite(t):
            continue
        comment = ""
        values = cells[1:]
        for i, c in enumerate(values):
            if c.lstrip().startswith("#"):  # "#* text" (all channels) or "#2 text" (one channel)
                comment = re.sub(r"^#\S*\s*", "", "\t".join(values[i:]).strip())
                values = values[:i]
                break
        if t + offset <= last:  # a new block: its clock starts again
            offset = last + (interval if math.isfinite(interval) else 0.01) - t
        t += offset
        last = t
        ts.append(t)
        rows.append([_num(c) for c in values])
        if comment:
            comments.append((t, comment))
    if not rows:
        raise ValueError("This LabChart export has no data rows.")
    n = max(len(r) for r in rows)
    data = np.full((len(rows), n), np.nan)
    for i, r in enumerate(rows):
        data[i, : len(r)] = r
    t_arr = np.asarray(ts, float)
    if not math.isfinite(interval) or interval <= 0:
        interval = float(np.median(np.diff(t_arr))) if len(t_arr) > 1 else 0.01
    t_arr = t_arr - t_arr[0]
    comments = [(c - ts[0], text) for c, text in comments]
    titles = (titles + [f"Channel {i + 1}" for i in range(len(titles), n)])[:n]
    units = (units + [""] * n)[:n]
    return LabChartData(interval, titles, units, t_arr, [data[:, i] for i in range(n)], comments, started)


def pick_channels(d: LabChartData) -> dict[str, int]:
    """Which column is the flow, the CO2 and (if there is one) the SpO2."""

    flow_units = ("ml/s", "l/s", "l/min", "lpm", "ml/sec", "l/sec")
    out: dict[str, int] = {}
    in_flow_units = [i for i, u in enumerate(d.units) if u.lower().replace(" ", "") in flow_units]
    named = [i for i in in_flow_units if any(w in d.titles[i].lower() for w in ("flow", "pneumo"))]
    if named or len(in_flow_units) == 1:
        out["flow"] = (named or in_flow_units)[0]
    # transcutaneous CO2 ("TcCO2", "tcpCO2") is not the airway CO2
    co2 = [
        i
        for i, (t, u) in enumerate(zip(d.titles, d.units))
        if "co2" in t.lower().replace("₂", "2") and not t.lower().startswith(("tc", "ptc", "transc")) and u.lower() in ("mmhg", "%", "kpa", "torr")
    ]
    if co2:
        out["co2"] = co2[0]
    sat = [i for i, (t, u) in enumerate(zip(d.titles, d.units)) if any(w in t.lower() for w in ("sat", "spo2", "sao2")) and u.strip() in ("%", "")]
    if sat:
        out["spo2"] = sat[0]
    missing = [k for k in ("flow", "co2") if k not in out]
    if missing:
        found = ", ".join(f"{t} ({u or 'no unit'})" for t, u in zip(d.titles, d.units)) or "none"
        need = " and ".join({"flow": "a flow channel (mL/s, L/s or L/min)", "co2": "an airway CO2 channel (mmHg, % or kPa)"}[k] for k in missing)
        raise ValueError(f"This LabChart export needs {need}. Channels found: {found}.")
    return out


def _to_lpm(v: np.ndarray, unit: str) -> np.ndarray:
    u = unit.lower().replace(" ", "")
    return v * (0.06 if u.startswith("ml") else 60.0 if u in ("l/s", "l/sec") else 1.0)


def _to_mmhg(v: np.ndarray, unit: str) -> np.ndarray:
    u = unit.lower().strip()
    return v * (IMPORT_PB / 100 if u == "%" else KPA_TO_MMHG if u == "kpa" else 1.0)


def _average(x: np.ndarray, k: int) -> np.ndarray:
    n = len(x) // k * k
    return np.nanmean(x[:n].reshape(-1, k), axis=1) if k > 1 else x


def labchart_to_session(text: str, name: str = "LabChart export", app_version: str = "") -> str:
    """A LabChart text export as the text of a session file (format v4)."""
    d = parse_labchart(text)
    pick = pick_channels(d)
    flow = _to_lpm(d.channels[pick["flow"]], d.units[pick["flow"]])
    co2 = _to_mmhg(d.channels[pick["co2"]], d.units[pick["co2"]])
    sat = d.channels[pick["spo2"]] if "spo2" in pick else None
    t = d.t
    k = max(1, round((1 / d.interval_s) / 100)) if 1 / d.interval_s > MAX_RATE_HZ else 1
    if k > 1:
        n = len(t) // k * k
        t, flow, co2 = t[:n:k], _average(flow, k), _average(co2, k)
        sat = _average(sat, k) if sat is not None else None
    rate = 1 / (d.interval_s * k)

    # inspiration must be positive: CO2 is high while breathing out
    ok = np.isfinite(flow) & np.isfinite(co2)
    if ok.any():
        high = co2 > (np.nanmin(co2[ok]) + np.nanmax(co2[ok])) / 2
        if (high & ok).any() and float(np.mean(flow[high & ok])) > 0:
            flow = -flow

    # a made-up linear calibration that carries the calibrated values through the "volts"
    span = max(100.0, float(np.nanmax(np.abs(flow[np.isfinite(flow)]))) * 1.25) if np.isfinite(flow).any() else 100.0
    ch = clone_channels(DEFAULT_CHANNELS)
    k1 = 0.2
    pa_per_v = span / k1 / 2.25  # ±span L/min over 0.25–4.75 V
    lpm_per_v = span / 2.25
    c = ch["channels"]
    c["co2"].update(calibration={"type": "linear", "gain": 4.0, "offset": 0.0}, zeroOffset=0, calibratedAt=None, note="LabChart import: CO2 was already in mmHg")
    c["flow"].update(calibration={"type": "linear", "gain": lpm_per_v, "offset": -2.5 * lpm_per_v}, zeroOffset=0, calibratedAt=None, note="LabChart import: the same flow signal as the pneumotach")
    c["pneumo"].update(calibration={"type": "linear", "gain": pa_per_v, "offset": -2.5 * pa_per_v}, zeroOffset=0, calibratedAt=None, flowModel={"k1": k1, "k2": 0}, note="LabChart import: flow was already calibrated")
    oximeter = Spo2Settings(enabled=sat is not None, gain_pct_per_v=100, offset_pct=0)
    opts = ProcessingOptions(barometric_mm_hg=IMPORT_PB, sample_rate_hz=rate)

    v_co2 = np.clip(np.nan_to_num(co2 / IMPORT_PB * 100 / 4.0, nan=0.0), 0, 5)
    v_flow = np.clip(np.nan_to_num(2.5 + flow / lpm_per_v, nan=2.5), 0.01, 4.99)
    v_sat = np.clip(np.nan_to_num(sat / 100, nan=0.0), 0, 5) if sat is not None else None

    started = d.started_ms if math.isfinite(d.started_ms) else parse_iso_ms("2000-01-01T00:00:00.000Z")
    units = ", ".join(f"{d.titles[i]} [{d.units[i]}]" for i in sorted(pick.values()))
    meta = SessionMeta(
        study_code=re.sub(r"\.[A-Za-z0-9]+$", "", name)[:40],
        started_ms=started,
        app_version=app_version or "unknown",
        source=clean_text(f"LabChart import ({units}; {1 / d.interval_s:g} Hz{f' averaged to {rate:g} Hz' if k > 1 else ''})"),
        firmware="LabChart",
        rate_hz=round(rate, 3),
        channels=ch,
        processing=opts,
        spo2=oximeter if sat is not None else None,
    )
    out = [format_header(meta)]
    pipe = Pipeline(ch, opts, oximeter)
    marks = sorted(d.comments)
    mi = 0
    breaths: list = []
    good: list = []
    by_id: dict[int, int] = {}
    pending: list[str] = ["IMPORTED LabChart text export"]
    t_ms = np.round(t * 1000).tolist()
    n_breath = 0

    def emit(pr) -> None:
        nonlocal mi, n_breath
        ts = pr.t_ms / 1000
        while mi < len(marks) and marks[mi][0] <= ts:
            pending.append(f"MARK {marks[mi][1]}")
            mi += 1
        cells = None
        if pr.outlier is not None and id(pr.outlier) in by_id:
            if pr.outlier in good:
                good.remove(pr.outlier)
            pending.append(f"BREATH_REJECTED {by_id[id(pr.outlier)]} outlier: {pr.outlier.reason}")
        b = pr.breath
        if b is not None:
            n_breath += 1
            by_id[id(b)] = n_breath
            breaths.append(b)
            if b.quality == "ok":
                good.append(b)
            cells = BreathCells(n_breath, b.etco2, b.fico2, respiratory_rate(good), b.vt_ml, minute_ventilation(good, b.t_end), b.peak_insp_lpm, b.quality, b.period_sec, b.vte_ml)
        out.append(format_row(ts, started + pr.t_ms, pr.t_ms, pr.seq, pr.volts, pr.co2_mmhg, pr.co2_pct, pr.flow_lpm, pr.pneumo_pa, pr.pneumo_lpm, cells, list(pending), pr.spo2))
        pending.clear()

    for i in range(len(t_ms)):
        volts = [float(v_co2[i]), float(v_flow[i]), float(v_flow[i])]
        if v_sat is not None:
            volts.append(float(v_sat[i]))
        pr = pipe.process(t_ms[i], volts, i & 0xFFFF)
        if pr is not None:
            emit(pr)
    for pr in pipe.flush():
        emit(pr)
    end = t_ms[-1] / 1000 if t_ms else 0.0
    late = [f"MARK {text}" for _, text in marks[mi:]] + pending
    if late:
        out.append(format_row(end, started + end * 1000, events=late))
    return "".join(out)
