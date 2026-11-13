"""Add a Capnostream 35's exported data to an Arduino recording (the "after the study" path).

The live path (``sources/capnostream.py``) reads the monitor's data port while the study
runs. This is the other way round, for when the monitor's data cannot be streamed or
was not connected: record the flow with the Arduino as usual, export the monitor's data
to a text/CSV file afterwards, and put the two together here. The result is an ordinary
session file (format v4): breaths, EtCO2, VT, MV, the response plot and the report are
worked out by this app's own processing, exactly as for a live recording.

Two kinds of export are understood, found by how fast the CO2 column is sampled:

- **waveform** (5 Hz or faster, normally 20 Hz): the monitor's CO2 values go into the
  CO2 channel, as in the live path. The two recordings are lined up by comparing the
  CO2 with the flow: CO2 is high while the flow is expiratory.
- **trend** (an EtCO2 value about once a second, or once a breath): there is no CO2
  waveform to put in. Each breath, found from the flow, takes the monitor's EtCO2 for
  that breath. FiCO2 is not available. The lining up uses the monitor's respiratory
  rate against the rate worked out from the flow, or a shift you give.

The export's layout is not published (Medtronic's data transfer document comes with the
monitor), so the reader is tolerant: it finds the header row by the column names
(``Time``, ``CO2``, ``EtCO2``, ``RR`` ...), takes units from ``(mmHg)`` in a name or from
``units``, and skips any lines above the header (they may hold patient details and are
never copied). A headerless file needs ``fields``, as for the live decoder.

**Not tried on a real Capnostream export** (none was available): the tests use exports
built from the sample session in several plausible layouts.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..processing.co2 import mmhg_to_pct
from ..processing.metrics import minute_ventilation, respiratory_rate
from ..processing.pipeline import Pipeline
from ..settings import ProcessingOptions, Spo2Settings, from_dict
from ..sources.capnostream import ALIASES, CAPNO_RATE_HZ, to_mmhg
from .session_file import BreathCells, ParsedSession, SessionMeta, clean_text, format_header, format_row, session_markers

NAN = float("nan")
#: a CO2 column sampled at least this fast (Hz) is a waveform; slower is a trend
WAVEFORM_MIN_HZ = 5.0
#: the monitor's own sample-line delay (s), as in the live path
DEFAULT_DELAY_S = 2.7
#: how far (s) either side of "both started together" the lining up looks
DEFAULT_SEARCH_S = 300.0
#: a trend EtCO2 shows up on the monitor some time after the line delay (its own
#: averaging, and the export's resolution): half a breath, within these limits (s)
TREND_MARGIN_S = (0.5, 2.0)
#: below this correlation the two recordings are not taken to belong together
MIN_R = 0.15

EXPORT_ALIASES: dict[str, str] = {
    **ALIASES,
    "CO2WAVEFORM": "co2", "CAPNOGRAM": "co2", "WAVEFORM": "co2", "CO2MMHG": "co2", "CO2VALUE": "co2",
    "ETCO2MMHG": "etco2", "ENDTIDALCO2": "etco2", "ETCO2VALUE": "etco2",
    "INSPCO2": "fico2", "FICO2MMHG": "fico2",
    "RESPIRATORYRATE": "rr", "RESPRATE": "rr", "RRBPM": "rr", "BREATHRATE": "rr",
    "PULSERATE": "pr", "HEARTRATE": "pr", "PRBPM": "pr",
    "OXYGENSATURATION": "spo2",
}  # fmt: skip
TIME_NAMES = frozenset({"TIME", "TIMESTAMP", "DATETIME", "DATE", "ELAPSED", "ELAPSEDTIME", "SECONDS", "SEC", "T", "CLOCK", "TIMEMS", "TIMESEC"})
MISSING = frozenset({"", "-", "--", "---", "----", "***", "NA", "N/A", "NAN", "NULL", "INVALID"})


# ------------------------------------------------------------------ reading the export


@dataclass
class CapnoExport:
    #: "waveform" or "trend"
    kind: str
    #: seconds from the export's first row
    t: np.ndarray
    #: the CO2 waveform (mmHg), or the EtCO2 trend (mmHg)
    co2: np.ndarray
    #: the monitor's respiratory rate (time, breaths/min) if the export has one
    rr_t: np.ndarray = field(default_factory=lambda: np.zeros(0))
    rr: np.ndarray = field(default_factory=lambda: np.zeros(0))
    rate_hz: float = NAN
    rows: int = 0
    #: what was found, for messages: e.g. {"co2": "CO2 (mmHg)", "time": "Time"}
    columns: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _num(s: str) -> float:
    s = s.strip().strip('"')
    if s.upper() in MISSING:
        return NAN
    try:
        return float(s)
    except ValueError:
        try:
            return float(s.replace(",", "."))  # a decimal comma
        except ValueError:
            return NAN


_UNIT = re.compile(r"[\(\[]\s*([^)\]]*?)\s*[\)\]]")


def _name_and_unit(cell: str) -> tuple[str, str]:
    """``"EtCO2 (mmHg)"`` -> (``"ETCO2"``, ``"mmhg"``)."""
    cell = cell.strip().strip('"')
    m = _UNIT.search(cell)
    unit = m.group(1).lower().replace(" ", "") if m else ""
    name = re.sub(r"[^A-Z0-9]", "", _UNIT.sub("", cell).upper())
    return name, unit


def _unit_of(unit: str, default: str) -> str:
    return {"mmhg": "mmHg", "torr": "mmHg", "%": "%", "percent": "%", "kpa": "kPa"}.get(unit, default)


_TOD = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:[.,]\d+)?))?\s*([AaPp][Mm])?$")
_DATE_TIME = re.compile(r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})[T ,]+(.+)$")
_TZ = re.compile(r"\s*(Z|[+-]\d\d:?\d\d)$")


def _time_cell(s: str) -> float:
    """Seconds (an elapsed time, or a clock time, with its date if it has one)."""
    s = s.strip().strip('"')
    v = _num(s)
    if math.isfinite(v) and re.fullmatch(r"-?\d+(?:[.,]\d+)?(?:e[+-]?\d+)?", s, re.I):
        return v
    days = 0.0
    m = _DATE_TIME.match(s)
    if m:
        d = m.group(1)
        try:
            if "-" in d:
                y, mo, dd = (int(x) for x in d.split("-"))
            else:
                mo, dd, y = (int(x) for x in d.split("/"))
                y += 2000 if y < 100 else 0
            days = datetime(y, mo, dd).toordinal() * 86400.0
        except ValueError:
            days = 0.0
        s = m.group(2)
    s = _TZ.sub("", s.strip())
    m = _TOD.match(s)
    if not m:
        return NAN
    h, mi, sec, ap = int(m.group(1)), int(m.group(2)), float((m.group(3) or "0").replace(",", ".")), m.group(4)
    if ap:
        h = h % 12 + (12 if ap.lower() == "pm" else 0)
    return days + h * 3600 + mi * 60 + sec


def _pick_delimiter(lines: list[str]) -> str | None:
    best, best_n = None, 0
    for d in ("\t", ";", ",", "|"):
        counts = sorted(ln.count(d) for ln in lines)
        n = counts[len(counts) // 2] if counts else 0
        if n > best_n:
            best, best_n = d, n
    return best


def _split(line: str, delim: str | None) -> list[str]:
    return [c.strip().strip('"') for c in (line.split(delim) if delim else line.split())]


def _spread_ties(t: np.ndarray) -> np.ndarray:
    """Rows that share one timestamp (a time given to the second, 20 samples a second)
    are spread evenly over the interval up to the next timestamp."""
    if len(t) < 3 or not np.any(np.diff(t) == 0):
        return t
    out = t.astype(float).copy()
    i = 0
    n = len(t)
    while i < n:
        j = i
        while j + 1 < n and t[j + 1] == t[i]:
            j += 1
        k = j - i + 1
        if k > 1:
            nxt = t[j + 1] if j + 1 < n else t[i] + 1.0
            step = (nxt - t[i]) / k
            out[i : j + 1] = t[i] + step * np.arange(k)
        i = j + 1
    return out


def parse_capnostream_export(text: str, fields: str = "", units: str = "", rate_hz: float = CAPNO_RATE_HZ, interval_s: float = 1.0, barometric_mm_hg: float = 760.0) -> CapnoExport:
    """A Capnostream export (CSV, tab or space separated text) as time and CO2 arrays.

    ``fields`` gives the columns in order for a file without a header (the names of the
    live decoder: ``time,co2,etco2,fico2,rr,spo2,pr,ipi``; ``co2*`` = every remaining
    value on the line is a waveform sample; ``skip`` ignores a column). ``units`` is the
    CO2 unit (``mmHg``, ``%``, ``kPa``) when the header does not say. ``rate_hz`` and
    ``interval_s`` time a file with no time column."""
    lines = [ln for ln in (raw.rstrip("\r") for raw in text.splitlines()) if ln.strip()]
    if not lines:
        raise ValueError("This Capnostream export is empty.")
    notes: list[str] = []
    default_unit = _unit_of(units.lower().replace(" ", ""), "") if units else ""

    names: list[str] = []
    col_units: list[str] = []
    header_at = -1
    positional = [f.strip().lower() for f in fields.split(",") if f.strip()] if fields else []
    delim = _pick_delimiter(lines[: min(len(lines), 60)])
    if not positional:
        for i, ln in enumerate(lines[:120]):
            cells = _split(ln, delim)
            parsed = [_name_and_unit(c) for c in cells]
            known = [n for n, _ in parsed if n in EXPORT_ALIASES or n in TIME_NAMES]
            numeric = sum(1 for c in cells if math.isfinite(_num(c)))
            if known and numeric <= len(cells) // 2:
                names, col_units, header_at = [n for n, _ in parsed], [u for _, u in parsed], i
                break
    time_ms = False
    time_min = False
    cols: dict[str, int] = {}
    star = -1
    if positional:
        for i, nm in enumerate(positional):
            if nm == "co2*":
                star = i
            elif nm in ("time", "t", "skip"):
                if nm != "skip":
                    cols["time"] = i
            elif nm in EXPORT_ALIASES.values() or nm in ("co2", "etco2", "fico2", "rr", "spo2", "pr", "ipi"):
                cols.setdefault(nm, i)
        col_units = [""] * len(positional)
        data_lines = lines
    elif header_at >= 0:
        for i, nm in enumerate(names):
            if nm in TIME_NAMES and "time" not in cols:
                cols["time"] = i
                time_ms = nm == "TIMEMS" or col_units[i] in ("ms", "msec")
                time_min = col_units[i] in ("min", "minutes")
            elif nm in EXPORT_ALIASES:
                cols.setdefault(EXPORT_ALIASES[nm], i)
        data_lines = lines[header_at + 1 :]
    else:
        # no header: a single column of numbers is a waveform
        vals = [_num(_split(ln, delim)[0]) for ln in lines]
        if sum(1 for v in vals if math.isfinite(v)) >= 0.7 * len(vals) and (delim is None or all(len(_split(ln, delim)) == 1 for ln in lines[:20])):
            cols["co2"] = 0
            names = ["CO2"]
            col_units = [""]
            data_lines = lines
            notes.append(f"No header: the single column was taken as a CO2 waveform at {rate_hz:g} Hz.")
        else:
            shown = "; ".join(ln[:60] for ln in lines[:3])
            raise ValueError(
                f'Could not find the columns in this Capnostream export (first lines: {shown}). Give the layout in order, e.g. fields="time,co2,etco2,rr" (--capno-fields).'
            )

    if "co2" not in cols and "etco2" not in cols and star < 0:
        found = ", ".join(c for c in names) or "none"
        raise ValueError(f"This Capnostream export has no CO2 or EtCO2 column. Columns found: {found}.")

    need = max([*cols.values(), star, 0]) + 1
    ts: list[float] = []
    co2: list[float] = []
    etco2: list[float] = []
    rr: list[float] = []
    for ln in data_lines:
        cells = _split(ln, delim)
        if len(cells) < need:
            continue
        t = _time_cell(cells[cols["time"]]) if "time" in cols else NAN
        if "time" in cols and not math.isfinite(t):
            continue
        if time_ms:
            t /= 1000
        if time_min:
            t *= 60
        if star >= 0:  # several waveform samples on each line
            vals = [_num(c) for c in cells[star:]]
            for j, v in enumerate(vals):
                ts.append(t + j / rate_hz if math.isfinite(t) else NAN)
                co2.append(v)
                etco2.append(NAN)
                rr.append(NAN)
            continue
        ts.append(t)
        co2.append(_num(cells[cols["co2"]]) if "co2" in cols else NAN)
        etco2.append(_num(cells[cols["etco2"]]) if "etco2" in cols else NAN)
        rr.append(_num(cells[cols["rr"]]) if "rr" in cols else NAN)
    if star >= 0:
        cols["co2"] = star
    if not ts:
        raise ValueError("No data rows were found in this Capnostream export.")

    t_arr = np.asarray(ts, float)
    co2_a, et_a, rr_a = np.asarray(co2, float), np.asarray(etco2, float), np.asarray(rr, float)
    using_co2 = bool(np.isfinite(co2_a).any())
    if not np.isfinite(t_arr).any():  # no time column: spaced by the rate
        step = 1 / rate_hz if using_co2 and not np.isfinite(et_a).any() else interval_s
        t_arr = np.arange(len(t_arr)) * step
        notes.append(f"No time column: rows were taken {step:g} s apart.")
    else:
        # a clock time that wraps past midnight
        for w in np.flatnonzero(np.diff(t_arr) < -43200):
            t_arr[w + 1 :] += 86400.0
    order = np.argsort(t_arr, kind="stable")
    if not np.array_equal(order, np.arange(len(order))):
        notes.append("Rows were out of time order and were sorted.")
    t_arr, co2_a, et_a, rr_a = t_arr[order], co2_a[order], et_a[order], rr_a[order]
    t0 = float(t_arr[0])
    t_arr = _spread_ties(t_arr - t0)
    dt = np.diff(t_arr)
    med = float(np.median(dt[dt > 0])) if (dt > 0).any() else interval_s
    rate = 1 / med if med > 0 else NAN

    kind = "waveform" if using_co2 and rate >= WAVEFORM_MIN_HZ else "trend"
    if kind == "trend" and not np.isfinite(et_a).any():
        raise ValueError(
            f"The CO2 column in this Capnostream export is sampled only about every {med:g} s, too slowly for a waveform, and there is no EtCO2 column. Columns found: {', '.join(names)}."
        )
    primary = co2_a if kind == "waveform" else et_a  # a slow CO2 column is not used: EtCO2 is

    # units
    cu = ""
    key = "co2" if kind == "waveform" else "etco2"
    if cols.get(key) is not None and cols[key] < len(col_units):
        cu = _unit_of(col_units[cols[key]], "")
    unit = default_unit or cu
    finite = primary[np.isfinite(primary)]
    if not len(finite):
        raise ValueError("The CO2 values in this Capnostream export are all missing.")
    if not unit:
        if float(np.percentile(finite, 99)) < 12:
            raise ValueError('The CO2 values look like % or kPa, not mmHg (none above 12) and the export does not say which. Give the unit: units="%" or "kPa" (--capno-units).')
        unit = "mmHg"
        notes.append("CO2 unit not given: taken as mmHg.")
    primary = np.asarray([to_mmhg(float(v), unit, barometric_mm_hg) if math.isfinite(v) else NAN for v in primary])
    has_rr = np.isfinite(rr_a)
    columns = {k: (names[v] if v < len(names) else str(v)) for k, v in cols.items()}
    return CapnoExport(kind, t_arr, primary, t_arr[has_rr], rr_a[has_rr], rate, len(primary), columns, notes)


# ------------------------------------------------------------------ lining up


def _xcorr(a: np.ndarray, b: np.ndarray, ks: np.ndarray) -> np.ndarray:
    """``c[k] = sum_i a[i] * b[i + k]`` for the shifts ``ks``."""
    n = len(a) + len(b)
    m = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.conj(np.fft.rfft(a, m)) * np.fft.rfft(b, m), m)
    return c[ks % m]


def _lag_scan(x: np.ndarray, y: np.ndarray, kmin: int, kmax: int, min_n: int) -> tuple[np.ndarray, np.ndarray]:
    """Pearson correlation of x[i] with y[i + k] over the samples both have, for each k."""
    wx, wy = np.isfinite(x).astype(float), np.isfinite(y).astype(float)
    x0, y0 = np.where(wx > 0, x, 0.0), np.where(wy > 0, y, 0.0)
    ks = np.arange(kmin, kmax + 1)
    n = _xcorr(wx, wy, ks)
    sx, sy = _xcorr(x0, wy, ks), _xcorr(wx, y0, ks)
    sxx, syy, sxy = _xcorr(x0 * x0, wy, ks), _xcorr(wx, y0 * y0, ks), _xcorr(x0, y0, ks)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (sxy - sx * sy / n) / np.sqrt((sxx - sx * sx / n) * (syy - sy * sy / n))
    r[(n < min_n) | ~np.isfinite(r)] = NAN
    return ks, r


def _grid_mean(t: np.ndarray, v: np.ndarray, dt: float, t0: float, n: int) -> np.ndarray:
    """Average ``v`` into bins of ``dt`` seconds starting at ``t0`` (NaN where empty)."""
    ok = np.isfinite(v) & np.isfinite(t)
    i = np.floor((t[ok] - t0) / dt).astype(int)
    keep = (i >= 0) & (i < n)
    s = np.bincount(i[keep], weights=v[ok][keep], minlength=n)
    c = np.bincount(i[keep], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, s / c, NAN)


#: peaks this close in correlation to the best are not told apart
AMBIGUOUS_MARGIN = 0.02


@dataclass
class Lag:
    #: seconds the monitor's trace is behind the flow for the same gas (tc = ts + shift)
    shift_s: float
    r: float
    #: the shifts (s) of every peak within ``AMBIGUOUS_MARGIN`` of the best, nearest the
    #: expected one first. More than one means the data cannot say which is right: regular
    #: breathing fits almost as well a whole breath out.
    candidates: list[float] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _best_lag(x: np.ndarray, x0: float, y: np.ndarray, y0: float, dt: float, centre_s: float, search_s: float, min_overlap_s: float) -> Lag | None:
    """The shift L (tc = ts + L, in seconds) that best lines y up with x. x[i] is at
    time x0 + i*dt on the flow's clock, y[j] at y0 + j*dt on the monitor's. Among peaks
    that fit equally well, the one nearest ``centre_s`` is taken."""
    base = y0 - x0
    kmin = max(int(math.floor((centre_s - search_s - base) / dt)), -len(x) + 1)
    kmax = min(int(math.ceil((centre_s + search_s - base) / dt)), len(y) - 1)
    if kmax <= kmin:
        return None
    ks, r = _lag_scan(x, y, kmin, kmax, max(3, int(min_overlap_s / dt)))
    if not np.isfinite(r).any():
        return None
    best = float(np.nanmax(r))
    w = max(1, int(round(1.0 / dt)))  # peaks are the highest point within 1 s either side
    peaks = [i for i in np.flatnonzero(np.nan_to_num(r, nan=-1.0) >= best - AMBIGUOUS_MARGIN) if r[i] >= np.nanmax(r[max(0, i - w) : i + w + 1])]

    def refined(i: int) -> float:
        shift = base + ks[i] * dt
        if 0 < i < len(r) - 1 and np.isfinite(r[i - 1]) and np.isfinite(r[i + 1]):  # parabola through the peak
            a, b, c = float(r[i - 1]), float(r[i]), float(r[i + 1])
            den = a - 2 * b + c
            if den < 0:
                shift += 0.5 * (a - c) / den * dt
        return float(shift)

    cands = sorted((refined(i) for i in peaks), key=lambda s: abs(s - centre_s))
    return Lag(cands[0], best, cands)


def _expiration_signal(s: ParsedSession, dt: float, flow_delay_s: float) -> tuple[np.ndarray, float]:
    """1 while the flow is expiratory (inspiration is positive), 0 while not, on a grid."""
    t = s.time_s - flow_delay_s
    flow = s.pneumo_lpm.astype(float)
    ok = np.isfinite(flow)
    if ok.sum() < 100:
        raise ValueError("This session has no flow to line the Capnostream up with.")
    ind = np.where(ok, (flow < -3.0).astype(float), NAN)
    t0 = float(t[0])
    n = int(math.ceil((float(t[-1]) - t0) / dt)) + 1
    return _grid_mean(t, ind, dt, t0, n), t0


def _session_rr(s: ParsedSession, dt: float) -> tuple[np.ndarray, float] | None:
    """The respiratory rate worked out from the flow (60 / breath length), held between
    breaths, on a grid; None when there are too few breaths to compare."""
    pts = [(b.time_s, 60 / b.period_s) for b in s.breaths if math.isfinite(b.period_s) and b.period_s > 0.5]
    if len(pts) < 10:
        return None
    bt = np.asarray([p[0] for p in pts])
    bv = np.asarray([p[1] for p in pts])
    t0 = float(bt[0])
    grid = t0 + np.arange(int(math.ceil((float(bt[-1]) - t0) / dt)) + 1) * dt
    idx = np.clip(np.searchsorted(bt, grid, side="right") - 1, 0, len(bt) - 1)
    return bv[idx], t0


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return x
    w = np.isfinite(x).astype(float)
    x0 = np.where(w > 0, x, 0.0)
    ker = np.ones(k)
    num = np.convolve(x0, ker, mode="same")
    den = np.convolve(w, ker, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, NAN)


# ------------------------------------------------------------------ the merge


@dataclass
class CapnoMergeResult:
    text: str
    kind: str
    #: tc = ts + shift_s: how late the monitor shows the gas, flow clock to monitor clock
    shift_s: float
    #: "given", "waveform", "respiratory rate" or "assumed"
    method: str
    r: float = NAN
    #: how much of the recording has monitor CO2 (0-1)
    coverage: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def start_offset_s(self) -> float:
        """The monitor's recording started this many seconds before the Arduino's (an
        estimate: the shift without the sample line's delay)."""
        return self.shift_s - DEFAULT_DELAY_S


def _trend_margin(s: ParsedSession) -> float:
    """How long after the sample-line delay to look for a breath's EtCO2 in a trend:
    half the usual breath length, so that it is after that breath's value has appeared
    and before the next one's."""
    periods = [b.period_s for b in s.breaths if math.isfinite(b.period_s) and b.period_s > 0]
    half = 0.5 * float(np.median(periods)) if periods else 1.0
    return min(max(half, TREND_MARGIN_S[0]), TREND_MARGIN_S[1])


def _ambiguous(lag: Lag, centre: float) -> str:
    shown = ", ".join(f"{c:.1f}" for c in sorted(lag.candidates))
    return (
        f"The lining up is ambiguous: the monitor's CO2 matches the flow about equally well at shifts of {shown} s "
        f"(regular breathing fits almost as well a whole breath out). The one nearest the expected {centre:.1f} s was used, {lag.shift_s:.1f} s. "
        "If the monitor was started a known time before the Arduino, give it (--capno-start-offset); better, hold your breath for about 10 s near the start of a study, "
        "which makes the match unique; or give the shift yourself (--capno-shift)."
    )


def _at(t_src: np.ndarray, v_src: np.ndarray, q: np.ndarray, max_gap: float, hold: bool) -> np.ndarray:
    """The monitor's value at times ``q``: interpolated (waveform) or held (trend); NaN
    outside the data and across gaps longer than ``max_gap`` seconds."""
    ok = np.isfinite(v_src)
    t, v = t_src[ok], v_src[ok]
    if len(t) == 0:
        return np.full(len(q), NAN)
    if hold:
        i = np.searchsorted(t, q, side="right") - 1
        out = np.where(i >= 0, v[np.clip(i, 0, len(v) - 1)], NAN)
        late = np.where(i >= 0, q - t[np.clip(i, 0, len(t) - 1)], np.inf)
        out[late > max_gap] = NAN
        return out
    out = np.interp(q, t, v, left=NAN, right=NAN)
    if len(t) > 1:
        i1 = np.clip(np.searchsorted(t, q), 1, len(t) - 1)
        out[(t[i1] - t[i1 - 1]) > max_gap] = NAN
    return out


def merge_capnostream_export(
    session: ParsedSession,
    export_text: str,
    *,
    fields: str = "",
    units: str = "",
    delay_s: float = DEFAULT_DELAY_S,
    shift_s: float | None = None,
    start_offset_s: float = 0.0,
    search_s: float = DEFAULT_SEARCH_S,
    app_version: str = "",
) -> CapnoMergeResult:
    """The session's flow with a Capnostream export's CO2, as a new session file.

    ``shift_s`` skips the lining up: the monitor's trace is that many seconds behind
    the flow for the same gas (clock difference plus the sample line's delay). Without
    it the shift is found from the data, searching ``search_s`` either side of "both
    recordings started together" (moved by ``start_offset_s``, plus ``delay_s``)."""
    if session.channels is None:
        raise ValueError("This session has no channel settings, so it cannot be processed again.")
    opts = from_dict(ProcessingOptions, _json(session.meta.get("processing")))
    spo2 = from_dict(Spo2Settings, _json(session.meta.get("spo2"))) if session.meta.get("spo2") else None
    exp = parse_capnostream_export(export_text, fields, units, interval_s=1.0, barometric_mm_hg=opts.barometric_mm_hg)
    warnings = list(exp.notes)
    flow_delay = opts.co2_delay_ms / 1000

    centre = start_offset_s + delay_s
    method, r = "given", NAN
    if shift_s is not None:
        L = float(shift_s)
    elif exp.kind == "waveform":
        dt = 0.1
        x, x0 = _expiration_signal(session, dt, flow_delay)
        t0c = float(exp.t[0])
        n = int(math.ceil((float(exp.t[-1]) - t0c) / dt)) + 1
        y = _grid_mean(exp.t, exp.co2, dt, t0c, n)
        lag = _best_lag(x, x0, y, t0c, dt, centre, search_s, min_overlap_s=60.0)
        if lag is None or not math.isfinite(lag.r) or lag.r < MIN_R:
            got = "no overlap" if lag is None else f"best correlation {lag.r:.2f}"
            raise ValueError(
                f"Could not line the Capnostream up with the flow ({got}). Are they the same study? Give the shift yourself (--capno-shift, seconds the monitor is behind the flow) or widen --capno-search."
            )
        L, r, method = lag.shift_s, lag.r, "waveform"
        if lag.ambiguous:
            warnings.append(_ambiguous(lag, centre))
        elif lag.r < 0.3:
            warnings.append(f"The lining up is weak (correlation {lag.r:.2f}): check it, or give the shift with --capno-shift.")
    else:
        L, r, method = centre, NAN, "assumed"
        a = _session_rr(session, 1.0)
        if a is not None and len(exp.rr) >= 10:
            sx, sx0 = a
            ry = _grid_mean(exp.rr_t, exp.rr, 1.0, float(exp.rr_t[0]), int(math.ceil((float(exp.rr_t[-1]) - float(exp.rr_t[0])) / 1.0)) + 1)
            # the monitor's own rate is averaged over several breaths: smooth both alike
            lag = _best_lag(_smooth(sx, 15), sx0, _smooth(ry, 15), float(exp.rr_t[0]), 1.0, start_offset_s, search_s, min_overlap_s=60.0)
            if lag is not None and math.isfinite(lag.r) and lag.r >= 0.5 and not lag.ambiguous:
                L, r, method = lag.shift_s + delay_s, lag.r, "respiratory rate"
        if method == "assumed":
            warnings.append(
                "This export has EtCO2 values but no CO2 waveform, and its respiratory rate could not be matched to the flow's, so the two recordings are assumed to have started together"
                + (f" (the monitor {start_offset_s:g} s earlier)" if start_offset_s else "")
                + ". EtCO2 is taken from the nearest monitor value for each breath: check a few against the monitor's screen, or give --capno-shift."
            )
        else:
            warnings.append("This export has EtCO2 values but no CO2 waveform: each breath takes the monitor's EtCO2, and FiCO2 is not available.")

    # --- the CO2 for every row, on the flow's clock
    margin = _trend_margin(session)
    ts = session.time_s
    if exp.kind == "waveform":
        co2_mmhg = _at(exp.t, exp.co2, ts + L, max_gap=1.0, hold=False)
    else:
        step = float(np.median(np.diff(exp.t))) if len(exp.t) > 1 else 1.0
        co2_mmhg = _at(exp.t, exp.co2, ts + L + margin, max_gap=max(10.0, 3 * step), hold=True)
    coverage = float(np.isfinite(co2_mmhg).mean()) if len(co2_mmhg) else 0.0
    if coverage < 0.5:
        warnings.append(f"The monitor's data covers only {coverage * 100:.0f} % of the recording: check the shift ({L:.1f} s).")
    elif coverage < 0.98:
        warnings.append(f"The monitor's data covers {coverage * 100:.0f} % of the recording; the rest has no CO2.")

    text = _build_session(session, opts, spo2, exp, co2_mmhg, L, margin, method, r, app_version)
    return CapnoMergeResult(text, exp.kind, L, method, r, coverage, warnings)


def _json(s: str | None):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


#: events of the original recording that the new processing makes again, or that no longer fit
_DROP = ("BREATH_REJECTED", "BREATH_EXCLUDED", "BREATH_INCLUDED", "MARK", "MARK_RENAMED", "ALARM_")


def _build_session(
    s: ParsedSession, opts: ProcessingOptions, spo2: Spo2Settings | None, exp: CapnoExport, co2_mmhg: np.ndarray, L: float, margin: float, method: str, r: float, app_version: str
) -> str:
    from ..channels import clone_channels

    ch = clone_channels(s.channels)
    ch["channels"]["co2"].update(
        calibration={"type": "linear", "gain": 1.0, "offset": 0.0},
        zeroOffset=0,
        calibratedAt=None,
        railCheck="none",
        note="Capnostream export: the monitor's CO2 was already calibrated; its values are written in % in the CO2 volts column",
    )
    new = ProcessingOptions(**{**vars(opts), "co2_delay_ms": 0})  # the shift already lines the CO2 up with the flow
    pct = np.asarray([mmhg_to_pct(float(v), new.barometric_mm_hg, new.water_vapor) if math.isfinite(v) else NAN for v in co2_mmhg])

    rate = NAN
    try:
        rate = float(s.meta.get("rate_hz", ""))
    except ValueError:
        pass
    if not math.isfinite(rate) or rate <= 0:
        rate = new.sample_rate_hz
    src = clean_text(f"{s.meta.get('source', 'recording')} + Capnostream 35 export ({exp.kind}, {exp.rows} values, lined up by {method}, shift {L:.1f} s)")
    meta = SessionMeta(
        study_code=s.study_code,
        started_ms=s.started_ms,
        app_version=app_version or "unknown",
        source=src,
        firmware=s.meta.get("firmware", ""),
        rate_hz=rate,
        channels=ch,
        processing=new,
        alarm_settings=_json(s.meta.get("alarm_settings")) or None,
        test=_json(s.meta.get("test")) or None,
        spo2=spo2 if s.meta.get("spo2") else None,
    )
    head = format_header(meta)
    extra = [f"# capnostream_export,{exp.kind}", f"# capno_shift_s,{L:.3f}", f"# capno_alignment,{method}" + (f" r={r:.2f}" if math.isfinite(r) else "")]
    head = re.sub(r"\n(?=time_s,)", "\n" + "\n".join(extra) + "\n", head, count=1)
    out = [head]

    pipe = Pipeline(ch, new, spo2)
    marks = session_markers(s.events)
    others = [(e.time_s, e.text) for e in s.events if not e.text.startswith(_DROP)]
    pending_src = sorted([(t, f"MARK {label}") for t, label in marks] + others, key=lambda p: p[0])
    pi = 0
    pending = ["MERGED Capnostream 35 export"]
    started = s.started_ms
    has_v3 = len(s.v3) == s.n and s.n and bool(np.isfinite(s.v3).any())
    trend = exp.kind == "trend"
    et_t, et_v = (exp.t, exp.co2) if trend else (None, None)
    step = float(np.median(np.diff(exp.t))) if trend and len(exp.t) > 1 else 1.0

    breaths: list = []
    good: list = []
    by_id: dict[int, int] = {}
    n_breath = 0
    k = 0  # the input row that the next processed sample is

    def emit(pr) -> None:
        nonlocal pi, n_breath, k
        ts = float(s.time_s[k])
        while pi < len(pending_src) and pending_src[pi][0] <= ts:
            pending.append(pending_src[pi][1])
            pi += 1
        cells = None
        if pr.outlier is not None and id(pr.outlier) in by_id:
            if pr.outlier in good:
                good.remove(pr.outlier)
            pending.append(f"BREATH_REJECTED {by_id[id(pr.outlier)]} outlier: {pr.outlier.reason}")
        b = pr.breath
        if b is not None:
            n_breath += 1
            by_id[id(b)] = n_breath
            fico2 = b.fico2
            if trend:  # the monitor's EtCO2 for this breath, as it showed it after the breath
                q = np.asarray([ts + L + margin])
                v = float(_at(et_t, et_v, q, max_gap=max(10.0, 3 * step), hold=True)[0])
                b.etco2, fico2 = v, NAN
                if not math.isfinite(v):
                    b.quality = "artifact"
            breaths.append(b)
            if b.quality == "ok":
                good.append(b)
            cells = BreathCells(n_breath, b.etco2, fico2, respiratory_rate(good), b.vt_ml, minute_ventilation(good, b.t_end), b.peak_insp_lpm, b.quality, b.period_sec, b.vte_ml)
        out.append(format_row(ts, started + ts * 1000, pr.t_ms, pr.seq, pr.volts, pr.co2_mmhg, pr.co2_pct, pr.flow_lpm, pr.pneumo_pa, pr.pneumo_lpm, cells, list(pending), pr.spo2))
        pending.clear()
        k += 1

    v1, v2, v3 = s.v1.tolist(), s.v2.tolist(), s.v3.tolist() if has_v3 else None
    t_ms, seq = s.t_ms.tolist(), s.seq.tolist()
    for i in range(s.n):
        volts = [float(pct[i]), v1[i], v2[i]]
        if v3 is not None:
            volts.append(v3[i])
        pr = pipe.process(t_ms[i], volts, seq[i])
        if pr is not None:
            emit(pr)
    for pr in pipe.flush():
        emit(pr)
    end = float(s.time_s[-1]) if s.n else 0.0
    late = [text for _, text in pending_src[pi:]] + pending
    if late:
        out.append(format_row(end, started + end * 1000, events=late))
    return "".join(out)
