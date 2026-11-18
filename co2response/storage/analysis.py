"""In-depth analysis of a recorded session (any session CSV, recorded, imported or resumed).

Everything is worked out again from the file itself, so a recording made on another
computer, or weeks ago, gives the same answer:

- the recording: length, sample rate, dropped samples, gaps, resumed parts, sensors
- data quality: how many breaths were left out and why, signal ranges, flow at the
  SFM3300's ±250 L/min limit, CO2 sensor not answering
- the breath timing from the samples: inspiratory and expiratory time (Ti, Te), Ti/Ttot,
  mean inspiratory flow (VT/Ti), the capnogram's phase III slope, and how long after
  expiration starts the CO2 rises (the sidestream sample line's delay)
- the flow sensor's bias: the net flow over whole breaths, which a sidestream sample pump
  drawing through the flow sensor adds (inspired volume reads high, expired low)
- each phase of the test (before, baseline, rebreathing, after): breaths, EtCO2, RR, VT,
  MV, Ti/Ttot and their variability
- the ventilatory response: the slope with its standard error and 95 % confidence
  interval, r and r², the x-intercept, and how fast EtCO2 rose during rebreathing

``analysis_csv`` writes it as one tidy table (section, metric, value, unit, note) that
opens in Excel; ``breath_detail_csv`` writes one row per breath with the timing added.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from ..processing.metrics import linear_fit, test_window
from .report import breath_phases, session_response
from .session_file import BreathRec, ParsedSession, dropped_samples, session_markers

NAN = float("nan")
#: the SFM3300-250-D's range, L/min either way; flow within 2 % of it is "at the limit"
FLOW_LIMIT_LPM = 250.0
#: a gap in time_s longer than this between samples counts as a gap in the recording
GAP_S = 0.5


@dataclass
class Metric:
    section: str
    name: str
    value: float | str
    unit: str = ""
    note: str = ""


@dataclass
class BreathDetail:
    n: float
    time_s: float
    phase: str
    quality: str
    etco2: float
    fico2: float
    rr: float
    vt_ml: float
    vte_ml: float
    mv_breath: float
    period_s: float
    ti_s: float = NAN
    te_s: float = NAN
    ti_ttot: float = NAN
    mean_insp_flow_lpm: float = NAN
    phase3_slope_mmhg_s: float = NAN
    co2_delay_s: float = NAN
    net_flow_lpm: float = NAN


@dataclass
class Analysis:
    file_name: str
    metrics: list[Metric] = field(default_factory=list)
    breaths: list[BreathDetail] = field(default_factory=list)

    def get(self, section: str, name: str) -> float | str | None:
        for m in self.metrics:
            if m.section == section and m.name == name:
                return m.value
        return None


# ---------------------------------------------------------------- small statistics


def _finite(xs: Iterable[float]) -> list[float]:
    return [float(x) for x in xs if x is not None and math.isfinite(float(x))]


def _mean(xs: Iterable[float]) -> float:
    v = _finite(xs)
    return sum(v) / len(v) if v else NAN


def _sd(xs: Iterable[float]) -> float:
    v = _finite(xs)
    if len(v) < 2:
        return NAN
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def _cv_pct(xs: Iterable[float]) -> float:
    v = _finite(xs)
    m = _mean(v)
    return _sd(v) / m * 100 if v and m and math.isfinite(m) and m != 0 else NAN


def _median(xs: Iterable[float]) -> float:
    v = sorted(_finite(xs))
    if not v:
        return NAN
    k = len(v) // 2
    return v[k] if len(v) % 2 else (v[k - 1] + v[k]) / 2


#: two-sided 95 % Student t for 1..30 degrees of freedom (beyond: 1.96)
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t95(dof: int) -> float:
    if dof < 1:
        return NAN
    return _T95[dof - 1] if dof <= len(_T95) else 1.96


def slope_stats(xs: list[float], ys: list[float]) -> tuple[float, float, float, float, float]:
    """OLS slope, its standard error, the 95 % CI half-width, r², and n."""
    n = min(len(xs), len(ys))
    fit = linear_fit(xs, ys)
    if fit is None or n < 3:
        return NAN, NAN, NAN, NAN, float(n)
    mx = sum(xs[:n]) / n
    sxx = sum((x - mx) ** 2 for x in xs[:n])
    resid = [y - (fit.slope * x + fit.intercept) for x, y in zip(xs[:n], ys[:n])]
    s2 = sum(r * r for r in resid) / (n - 2)
    se = math.sqrt(s2 / sxx) if sxx > 0 else NAN
    return fit.slope, se, t95(n - 2) * se, fit.r2, float(n)


# ---------------------------------------------------------------- per-breath timing


def _breath_timing(s: ParsedSession, b: BreathRec, t: np.ndarray, flow: np.ndarray, co2: np.ndarray) -> dict[str, float]:
    """Ti, Te, phase III slope and CO2 delay of one breath, from its samples.

    The breath runs from (end - period) to end, starting with inspiration (flow > 0).
    Expiration starts where the flow last turns negative and stays so; the CO2 delay is
    the time from there to where the CO2 crosses half-way from its inspired level to the
    breath's EtCO2."""
    out = {"ti": NAN, "te": NAN, "slope": NAN, "delay": NAN, "net": NAN}
    p = b.period_s
    if not (math.isfinite(p) and p > 0):
        return out
    i0, i1 = int(np.searchsorted(t, b.time_s - p)), int(np.searchsorted(t, b.time_s, side="right"))
    if i1 - i0 < 10:
        return out
    tt, ff, cc = t[i0:i1], flow[i0:i1], co2[i0:i1]
    ok = np.isfinite(ff)
    if ok.sum() < 10:
        return out
    dt = float(np.median(np.diff(tt))) if len(tt) > 1 else NAN
    # a 0.5 L/min dead band so noise around zero does not count as either
    insp = ff > 0.5
    exp_ = ff < -0.5
    if insp.any():
        out["ti"] = float(insp.sum()) * dt
    if exp_.any():
        out["te"] = float(exp_.sum()) * dt
    out["net"] = float(np.nanmean(ff[ok]))
    # start of expiration: the first expiratory sample after inspiration began
    ins_idx = np.flatnonzero(insp)
    if not len(ins_idx):
        return out
    first = ins_idx[0]
    neg_after = np.flatnonzero(exp_[first:])
    if not len(neg_after):
        return out
    k_exp = first + int(neg_after[0])
    t_exp = float(tt[k_exp])
    c_ok = np.isfinite(cc)
    if c_ok.sum() < 10 or not math.isfinite(b.etco2):
        return out
    lo = float(np.nanpercentile(cc[: max(k_exp, 1)], 10)) if k_exp > 0 and np.isfinite(cc[:k_exp]).any() else float(np.nanmin(cc))
    half = lo + (b.etco2 - lo) / 2
    # the CO2 at the start of the breath can still be falling from the previous one: look
    # for the rise after the inspired low point
    k_low = int(np.nanargmin(np.where(c_ok, cc, np.inf)))
    rise = np.flatnonzero((cc[k_low:] >= half) & c_ok[k_low:])
    if len(rise):
        t_half = float(tt[k_low + int(rise[0])])
        d = t_half - t_exp
        if -1.0 < d < 5.0:
            out["delay"] = d
    # phase III: the last half of the time the CO2 is above 80 % of its rise
    plateau = np.flatnonzero(c_ok & (cc >= lo + 0.8 * (b.etco2 - lo)))
    if len(plateau) >= 6:
        seg = plateau[len(plateau) // 2 :]
        fit = linear_fit(tt[seg].tolist(), cc[seg].tolist())
        if fit is not None:
            out["slope"] = fit.slope
    return out


# ---------------------------------------------------------------- the analysis


def _phase_of(t: float, win) -> str:
    if win.start is not None and t >= win.start and (win.end is None or t < win.end):
        return "rebreathing"
    if win.end is not None and win.start is not None and t >= win.end:
        return "after the test"
    if win.baseline is not None and t >= win.baseline and (win.start is None or t < win.start):
        return "baseline"
    return "before the test"


def analyze_session(s: ParsedSession, file_name: str = "") -> Analysis:
    a = Analysis(file_name)
    M = a.metrics

    def add(section: str, name: str, value: float | str, unit: str = "", note: str = "") -> None:
        M.append(Metric(section, name, value, unit, note))

    # ---- the recording
    t = np.asarray(s.time_s, float)
    add("recording", "file", file_name or "-")
    add("recording", "study code", s.study_code or "-")
    add("recording", "source", s.meta.get("source", "-") or "-")
    add("recording", "firmware", s.meta.get("firmware", "-") or "-")
    add("recording", "format version", float(s.version))
    add("recording", "duration", s.duration_s, "s")
    add("recording", "samples", float(s.n))
    if s.n > 1:
        dts = np.diff(t)
        pos = dts[dts > 0]
        rate = 1 / float(np.median(pos)) if len(pos) else NAN
        add("recording", "sample rate (measured)", rate, "Hz")
        gaps = dts[dts > GAP_S]
        add("recording", "gaps > 0.5 s", float(len(gaps)), "", "pauses, reconnects or a resumed recording")
        add("recording", "time in gaps", float(gaps.sum()) if len(gaps) else 0.0, "s")
    add("recording", "dropped samples", float(dropped_samples(s.seq, s.t_ms)), "", "gaps in the board's sequence numbers")
    resumed = [e for e in s.events if e.text.startswith("RECORDING_RESUMED")]
    add("recording", "times resumed", float(len(resumed)))
    alarms = [e for e in s.events if e.text.startswith("ALARM_START")]
    add("recording", "alarms", float(len(alarms)))
    markers = session_markers(s.events)
    add("recording", "markers", float(len(markers)), "", "; ".join(f"{_clock(m[0])} {m[1]}" for m in markers)[:300])

    # ---- data quality
    q_counts: dict[str, int] = {}
    for b in s.breaths:
        q = b.quality or "ok"
        q_counts[q] = q_counts.get(q, 0) + 1
    total = sum(q_counts.values())
    add("quality", "breaths", float(total))
    for q in ("ok", "noisy", "artifact", "outlier", "excluded"):
        if q in q_counts or q == "ok":
            add("quality", f"breaths {q}", float(q_counts.get(q, 0)), "", f"{q_counts.get(q, 0) / total * 100:.0f} % of breaths" if total else "")
    co2 = np.asarray(s.co2_mmhg, float)
    flow = np.asarray(s.pneumo_lpm, float) if np.isfinite(np.asarray(s.pneumo_lpm, float)).any() else np.asarray(s.flow_lpm, float)
    if s.n:
        add("quality", "CO2 missing", float(np.mean(~np.isfinite(co2)) * 100), "%", "samples with no CO2 value (sensor not answering, or removed as noise)")
        add("quality", "flow missing", float(np.mean(~np.isfinite(flow)) * 100), "%")
        fin = flow[np.isfinite(flow)]
        if len(fin):
            add("quality", "flow min", float(fin.min()), "L/min")
            add("quality", "flow max", float(fin.max()), "L/min")
            add("quality", "flow at the sensor's limit", float(np.mean(np.abs(fin) >= FLOW_LIMIT_LPM * 0.98) * 100), "%", "the SFM3300-250-D reads ±250 L/min")
        cf = co2[np.isfinite(co2)]
        if len(cf):
            add("quality", "CO2 min", float(cf.min()), "mmHg")
            add("quality", "CO2 max", float(cf.max()), "mmHg")
    if s.has_spo2:
        sp = np.asarray(s.spo2, float)
        sp = sp[np.isfinite(sp)]
        if len(sp):
            add("quality", "SpO2 lowest", float(sp.min()), "%")

    # ---- per breath, from the samples
    res = session_response(s)
    phases = breath_phases(s, res)
    win = test_window(markers)
    for b in s.breaths:
        tm = _breath_timing(s, b, t, flow, co2)
        ti, te = tm["ti"], tm["te"]
        d = BreathDetail(
            n=b.n,
            time_s=b.time_s,
            phase=_phase_of(b.time_s, win),
            quality=b.quality or "ok",
            etco2=b.etco2,
            fico2=b.fico2,
            rr=60 / b.period_s if math.isfinite(b.period_s) and b.period_s > 0 else b.rr,
            vt_ml=b.vt,
            vte_ml=b.vte,
            mv_breath=b.mv_breath,
            period_s=b.period_s,
            ti_s=ti,
            te_s=te,
            ti_ttot=ti / b.period_s if math.isfinite(ti) and math.isfinite(b.period_s) and b.period_s > 0 else NAN,
            mean_insp_flow_lpm=(b.vt / 1000) / ti * 60 if math.isfinite(ti) and ti > 0 and math.isfinite(b.vt) else NAN,
            phase3_slope_mmhg_s=tm["slope"],
            co2_delay_s=tm["delay"],
            net_flow_lpm=tm["net"],
        )
        a.breaths.append(d)
    test_role = {k: v for k, v in phases.items()}
    clean = [d for d in a.breaths if d.quality in ("ok", "excluded")]

    # ---- sensors and set-up, from the clean breaths
    delay = _median(d.co2_delay_s for d in clean)
    add(
        "set-up",
        "CO2 delay after expiration starts",
        delay,
        "s",
        "median of clean breaths; about 0.1-0.3 s is dead space, the rest is the sample line. Set Settings > Processing > CO2 delay to (this - 0.2 s) x 1000 ms",
    )
    proc_delay = _json_num(s.meta.get("processing", ""), "co2DelayMs")
    add("set-up", "CO2 delay already removed", proc_delay if math.isfinite(proc_delay) else 0.0, "ms", "from the recording's processing settings")
    bias = _median(d.net_flow_lpm for d in clean)
    add(
        "set-up",
        "flow bias (net flow over a breath)",
        bias,
        "L/min",
        "should be ~0. A positive bias the size of the sample pump's flow means the pump draws through the flow sensor: zero the flow with the mouthpiece capped and the pump on",
    )
    mism = [(d.vt_ml - d.vte_ml) / d.vt_ml * 100 for d in clean if math.isfinite(d.vt_ml) and math.isfinite(d.vte_ml) and d.vt_ml > 0]
    add("set-up", "inspired - expired volume", _median(mism), "%", "a leak or a flow-sensor zero error")

    # ---- each phase of the test
    for ph in ("before the test", "baseline", "rebreathing", "after the test"):
        ds = [d for d in clean if d.phase == ph and d.quality == "ok"]
        if not ds:
            continue
        sec = f"phase: {ph}"
        add(sec, "breaths", float(len(ds)))
        add(sec, "span", ds[-1].time_s - ds[0].time_s, "s", f"{_clock(ds[0].time_s)} to {_clock(ds[-1].time_s)}")
        for name, unit, vals in (
            ("EtCO2", "mmHg", [d.etco2 for d in ds]),
            ("RR", "/min", [d.rr for d in ds]),
            ("VT", "mL", [d.vt_ml for d in ds]),
            ("MV (each breath)", "L/min", [d.mv_breath for d in ds]),
            ("Ti", "s", [d.ti_s for d in ds]),
            ("Te", "s", [d.te_s for d in ds]),
            ("Ti/Ttot", "", [d.ti_ttot for d in ds]),
            ("mean inspiratory flow VT/Ti", "L/min", [d.mean_insp_flow_lpm for d in ds]),
            ("phase III slope", "mmHg/s", [d.phase3_slope_mmhg_s for d in ds]),
        ):
            add(sec, f"{name} mean", _mean(vals), unit)
            add(sec, f"{name} SD", _sd(vals), unit)
        add(sec, "VT CV", _cv_pct(d.vt_ml for d in ds), "%", "breath-to-breath variability")
        add(sec, "RR CV", _cv_pct(d.rr for d in ds), "%")

    # ---- the ventilatory response
    sec = "response"
    add(sec, "rebreathing marker", "yes" if res.split else "no", "", "" if res.split else "every breath was fitted")
    xs = [p.etco2 for p in res.rebreathing]
    ys = [p.mv for p in res.rebreathing]
    slope, se, ci, r2, n = slope_stats(xs, ys)
    add(sec, "slope", slope, "L/min/mmHg", "each breath's own MV against its EtCO2, least squares")
    add(sec, "slope SE", se, "L/min/mmHg")
    add(sec, "slope 95 % CI low", slope - ci if math.isfinite(ci) else NAN, "L/min/mmHg")
    add(sec, "slope 95 % CI high", slope + ci if math.isfinite(ci) else NAN, "L/min/mmHg")
    add(sec, "r", res.r, "")
    add(sec, "r²", r2, "")
    add(sec, "breaths fitted", n)
    add(sec, "x-intercept (apnoeic threshold estimate)", res.x_intercept, "mmHg", "EtCO2 where the fitted line reaches MV = 0")
    add(sec, "baseline EtCO2", res.baseline_etco2, "mmHg")
    add(sec, "baseline MV", res.baseline_mv, "L/min")
    add(sec, "breaths excluded by hand", float(len(res.excluded)))
    add(sec, "breaths left out at the start", float(len(res.skipped)), "", f"the first {res.skip_sec:g} s of rebreathing" if res.skip_sec else "")
    if res.rebreathing:
        rt = [p.t for p in res.rebreathing]
        re_ = [p.etco2 for p in res.rebreathing]
        rise = linear_fit(rt, re_)
        add(sec, "EtCO2 rise during rebreathing", rise.slope * 60 if rise else NAN, "mmHg/min", "should be steady (4-8 mmHg/min is typical of Read's method)")
        add(sec, "EtCO2 range fitted", f"{min(re_):.1f}-{max(re_):.1f}", "mmHg")
    if win.start is not None:
        add(sec, "rebreathing length", ((win.end if win.end is not None else s.duration_s) - win.start), "s")
    a.breaths.sort(key=lambda d: d.time_s)
    for d in a.breaths:
        d.phase = test_role.get(d.n, d.phase) if d.quality in ("ok", "excluded") else f"rejected ({d.quality})"
    return a


def _json_num(text: str, key: str) -> float:
    import json

    try:
        v = json.loads(text or "{}").get(key)
    except (ValueError, AttributeError):
        return NAN
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else NAN


def _clock(sec: float) -> str:
    if not math.isfinite(sec):
        return "--"
    t = max(0, round(sec))
    return f"{t // 60}:{t % 60:02d}"


def _cell(v: float | str, digits: int = 4) -> str:
    if isinstance(v, str):
        return '"' + v.replace('"', '""') + '"' if ("," in v or '"' in v) else v
    if v is None or not math.isfinite(v):
        return ""
    if float(v).is_integer() and abs(v) < 1e12:
        return str(int(v))
    return f"{v:.{digits}g}" if abs(v) >= 1e4 else f"{round(v, digits)}"


def analysis_csv(a: Analysis) -> str:
    """One row per number: section, metric, value, unit, note."""
    out = io.StringIO()
    out.write("section,metric,value,unit,note\n")
    for m in a.metrics:
        out.write(",".join((_cell(m.section), _cell(m.name), _cell(m.value), _cell(m.unit), _cell(m.note))) + "\n")
    return out.getvalue()


BREATH_COLUMNS = (
    "breath",
    "time_s",
    "time_mmss",
    "phase",
    "quality",
    "etco2_mmHg",
    "fico2_mmHg",
    "rr_bpm",
    "vt_ml",
    "vte_ml",
    "mv_breath_lpm",
    "period_s",
    "ti_s",
    "te_s",
    "ti_ttot",
    "mean_insp_flow_lpm",
    "phase3_slope_mmHg_s",
    "co2_delay_s",
    "net_flow_lpm",
)


def breath_detail_csv(a: Analysis) -> str:
    out = io.StringIO()
    out.write(",".join(BREATH_COLUMNS) + "\n")
    for d in a.breaths:
        row = (
            _cell(d.n),
            _cell(d.time_s),
            _clock(d.time_s),
            _cell(d.phase),
            d.quality,
            _cell(d.etco2),
            _cell(d.fico2),
            _cell(d.rr),
            _cell(d.vt_ml),
            _cell(d.vte_ml),
            _cell(d.mv_breath),
            _cell(d.period_s),
            _cell(d.ti_s),
            _cell(d.te_s),
            _cell(d.ti_ttot),
            _cell(d.mean_insp_flow_lpm),
            _cell(d.phase3_slope_mmhg_s),
            _cell(d.co2_delay_s),
            _cell(d.net_flow_lpm),
        )
        out.write(",".join(row) + "\n")
    return out.getvalue()


def analysis_text(a: Analysis) -> str:
    """The analysis as plain text, for a terminal or a review screen."""
    lines: list[str] = []
    section = None
    for m in a.metrics:
        if m.section != section:
            section = m.section
            lines.append("")
            lines.append(section.upper())
        v = m.value if isinstance(m.value, str) else ("--" if not math.isfinite(m.value) else f"{m.value:.3g}" if abs(m.value) < 1000 else f"{m.value:.0f}")
        lines.append(f"  {m.name:<44} {v:>12} {m.unit}".rstrip())
    return "\n".join(lines).lstrip("\n") + "\n"
