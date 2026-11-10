"""
Load a CO2-Response session file (one .csv per recording) for analysis.

    from co2r.session import load_session
    s = load_session("co2r_STUDY-01_20261105-010203.csv")
    s.samples      # one row per sample: time_s, volts, co2_mmHg, flow_lpm, pneumo_lpm, ..., breath_quality
    s.breaths      # one row per CLEAN breath: time_s, etco2_mmHg, rr_bpm, vt_ml, mv_lpm, mv_breath_lpm, ...
    s.breaths_all  # every breath, with breath_quality: ok, noisy, artifact, outlier or excluded
    s.events       # alarms, markers, connections: time_s, type, event
    s.meta         # study code, start time, firmware, sample rate, ...
    s.channels     # the calibration that was in use (dict)

    co2_response(s)  # MV vs EtCO2 as Read's rebreathing method reports it (dict):
                     # baseline averages before the "Rebreathing" marker, slope, r and
                     # x-intercept of the line fitted to the rebreathing breaths only
                     # (each breath's own MV; not the first seconds of rebreathing, not
                     # after the "End of test" marker, not breaths excluded by hand)

Noise is removed before anything is analysed, the same way the monitor does it:

- the calibrated sample columns (co2_mmHg, flow_lpm, pneumo_lpm, ...) were recorded
  after spike removal and low-pass filtering; v0-v2 are the raw volts, untouched;
- every breath carries a quality from the monitor's breath screen (noisy, a partial
  breath with no plateau, an impossible value, or an isolated EtCO2 jump), or
  ``excluded`` if the operator took it out by hand, and ``s.breaths`` keeps only the
  ``ok`` ones;
- ``mv_lpm`` is the 60 s average the monitor shows; ``mv_breath_lpm`` is the breath's
  own minute ventilation (vt_ml / 1000 x 60 / period_s), which the response is fitted to;
- each sample carries the quality of the breath it belongs to, and in ``s.samples`` the
  calibrated values of samples inside a rejected breath are blanked (NaN), so averages,
  plots and fits of the samples skip them too. The raw volts and every row stay.
  Pass ``clean=False`` to keep every breath and every calibrated value.
  Files from before the screen existed (v2) are screened here, for isolated jumps and
  impossible values (noise can only be judged live, from the samples), when the
  monitor's code (co2response/) is next to this package.

Or from the command line, for a quick summary and the per-breath table:

    python -m co2r.session co2r_STUDY-01_20261105-010203.csv --breaths breaths.csv
    python -m co2r.session session.csv --breaths all.csv --all-breaths
    python -m co2r.session session.csv --exclude 12,15 --skip 20

Needs pandas (pip install "co2r[analysis]" or pip install pandas). Reads v2, v3 and v4
files; v1 files from the first version of the app can be converted by importing them in
the app and exporting again.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

BREATH_COLS = ["time_s", "wall_time_iso", "breath", "etco2_mmHg", "fico2_mmHg", "rr_bpm", "vt_ml", "mv_lpm", "peak_insp_lpm", "period_s", "vte_ml", "breath_quality"]
SAMPLE_COLS = ["time_s", "wall_time_iso", "t_ms", "seq", "v0", "v1", "v2", "co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm"]
#: the cleaned, calibrated sample columns (v0-v2, the raw volts, are never blanked)
CALIBRATED_COLS = ["co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm"]


@dataclass
class Session:
    meta: dict
    channels: dict
    samples: pd.DataFrame
    breaths: pd.DataFrame
    events: pd.DataFrame
    breaths_all: pd.DataFrame = field(repr=False)
    raw: pd.DataFrame = field(repr=False)


def _screen(etco2: list, quality: list) -> list:
    """The monitor's own breath screen (co2response.processing.denoise), if available."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from co2response.processing.denoise import screen_series
    except ImportError:
        print("note: the monitor's code (co2response/) was not found; breaths in older files are not screened", file=sys.stderr)
        return [q or "ok" for q in quality]
    return screen_series(etco2, quality)


def sample_quality(sample_t, breath_end_t, breath_quality, first_period: float = 4.0) -> np.ndarray:
    """The quality of the breath each sample belongs to ("" before the first breath and
    after the last). A breath runs from the end of the previous one to its own end, the
    sample on which it was recorded; the first is taken to last ``first_period`` s."""
    t = np.asarray(sample_t, float)
    ends = np.asarray(breath_end_t, float)
    q = np.asarray(breath_quality, dtype=object)
    out = np.full(len(t), "", dtype=object)
    if len(ends) == 0:
        return out
    i = np.searchsorted(ends, t, side="left")  # first breath ending at or after each sample
    inside = (i < len(ends)) & (t > ends[0] - first_period)
    out[inside] = q[i[inside]]
    return out


def load_session(path: str, clean: bool = True) -> Session:
    meta: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            key, _, value = line[1:].strip().partition(",")
            meta[key.strip()] = value
    version = meta.get("CO2-Response session", "")
    if version not in ("v2", "v3", "v4"):
        raise ValueError(f"{path}: expected a v2, v3 or v4 session file, got {version or 'no version line'}. Import it in the app and export it again to convert.")

    raw = pd.read_csv(path, comment="#", dtype={"event": "string", "breath_quality": "string"})
    raw["wall_time_iso"] = pd.to_datetime(raw["wall_time_iso"], utc=True)
    if "breath_quality" not in raw:
        raw["breath_quality"] = pd.NA
    for col in ("period_s", "vte_ml"):  # new in v4
        if col not in raw:
            raw[col] = np.nan

    # v3 and spo2_pct are there when an oximeter was on the fourth input (format v4)
    extra = [c for c in ("v3", "spo2_pct") if c in raw and raw[c].notna().any()]
    samples = raw.loc[raw["t_ms"].notna(), SAMPLE_COLS + extra].reset_index(drop=True)
    breaths = raw.loc[raw["breath"].notna(), BREATH_COLS].reset_index(drop=True)
    breaths["breath"] = breaths["breath"].astype(int)
    # files before v4 have no period column: a breath runs from the end of the one
    # before it to its own end, so the time between breath rows is its length
    gap = breaths["time_s"].diff()
    est = gap.where((gap > 0) & (gap < 60), 60 / breaths["rr_bpm"].where(breaths["rr_bpm"] > 0))
    breaths["period_s"] = breaths["period_s"].astype(float).fillna(est)
    # each breath's own minute ventilation: what the CO2 response is fitted to
    breaths.insert(breaths.columns.get_loc("vte_ml") + 1, "mv_breath_lpm", breaths["vt_ml"] / 1000 * 60 / breaths["period_s"])

    ev = raw.loc[raw["event"].notna(), ["time_s", "wall_time_iso", "event"]]
    ev = ev.assign(event=ev["event"].str.split(r" \| ", regex=True)).explode("event").reset_index(drop=True)
    ev.insert(2, "type", ev["event"].str.extract(r"^([A-Z_]+)", expand=False).fillna("NOTE"))

    # breaths found to be isolated jumps one breath later are named in BREATH_REJECTED events
    quality = breaths["breath_quality"].fillna("").astype(str).tolist()
    by_n = {n: i for i, n in enumerate(breaths["breath"])}
    for text in ev.loc[ev["type"] == "BREATH_REJECTED", "event"]:
        parts = str(text).split()
        if len(parts) > 2 and parts[1].isdigit() and int(parts[1]) in by_n:
            quality[by_n[int(parts[1])]] = parts[2].rstrip(":")
    if any(q == "" for q in quality):
        quality = _screen(breaths["etco2_mmHg"].tolist(), quality)
    # breaths the operator took out by hand (or put back), in the order it was done
    screened = ["ok" if q == "excluded" else q for q in quality]
    for kind, text in zip(ev["type"], ev["event"].astype(str)):
        if kind in ("BREATH_EXCLUDED", "BREATH_INCLUDED"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) in by_n:
                i = by_n[int(parts[1])]
                quality[i] = "excluded" if kind == "BREATH_EXCLUDED" else screened[i]
    breaths["breath_quality"] = quality
    good = breaths[breaths["breath_quality"] == "ok"].reset_index(drop=True)

    # each sample gets its breath's quality; with clean=True the calibrated values of
    # samples inside a rejected breath are blanked, so they are not analysed either
    rr0 = breaths["rr_bpm"].iloc[0] if len(breaths) else float("nan")
    first = 60 / rr0 if np.isfinite(rr0) and rr0 > 0 else 4.0
    samples["breath_quality"] = sample_quality(samples["time_s"], breaths["time_s"], breaths["breath_quality"], first)
    if clean:
        rejected = samples["breath_quality"].isin(["noisy", "artifact", "outlier"])
        samples.loc[rejected, CALIBRATED_COLS] = np.nan

    try:
        channels = json.loads(meta.get("channels", "{}"))
    except json.JSONDecodeError:
        channels = {}
    return Session(meta=meta, channels=channels, samples=samples, breaths=good if clean else breaths, events=ev, breaths_all=breaths, raw=raw)


REBREATHING_WORD = "rebreath"
_WORD = re.compile(r"[a-z]+")


def marker_kind(label: str) -> str | None:
    """What a marker means for the test: 'baseline', 'start', 'end' or None. The same
    rule as the monitor (co2response/processing/metrics.py):

    - end: "End of test", "Test ended", "Stop rebreathing" ... (a word starting with
      end/stop/finish, with "test" or "rebreath" in the label)
    - start: any other label containing "rebreath" ("Rebreathing", "CO2 rebreathing started")
    - baseline: a label containing "baseline"
    """
    low = label.lower()
    words = _WORD.findall(low)
    if any(w.startswith(("end", "stop", "finish")) for w in words) and (REBREATHING_WORD in low or "test" in words):
        return "end"
    if REBREATHING_WORD in low:
        return "start"
    if "baseline" in low:
        return "baseline"
    return None


def markers(events: pd.DataFrame) -> list:
    """(time_s, label) of every event marker, with the label it ended up with.

    A marker placed with the M key is written as ``MARK Event <n>`` and, once labelled,
    ``MARK_RENAMED <n> <label>``; a renamed marker counts from when it was placed."""
    marks = events[events["type"].isin(["MARK", "MARK_RENAMED"])]
    out: list = []
    by_id: dict = {}
    for t, text in zip(marks["time_s"], marks["event"].astype(str)):
        kind, _, rest = text.partition(" ")
        if kind == "MARK":
            m = [float(t), rest]
            out.append(m)
            if rest.startswith("Event ") and rest[6:].isdigit():
                by_id[rest[6:]] = m
        else:
            mid, _, label = rest.partition(" ")
            if mid in by_id:
                by_id[mid][1] = label
            else:
                by_id[mid] = [float(t), label]
                out.append(by_id[mid])
    return [(t, label) for t, label in out]


def test_window(events: pd.DataFrame) -> dict:
    """The latest test in the recording: {'baseline': s, 'start': s, 'end': s}, each
    None where no marker says so.

    Rebreathing starts at the last start marker and ends at the first end marker after
    it. The baseline starts at the last baseline marker before the start, or at the
    end of an earlier test. A baseline marker after the latest test opens a new one."""
    ms = sorted(((t, marker_kind(label)) for t, label in markers(events)), key=lambda m: m[0])
    starts = [t for t, k in ms if k == "start"]
    bases = [t for t, k in ms if k == "baseline"]
    if not starts or (bases and bases[-1] > starts[-1]):
        return {"baseline": bases[-1] if bases else None, "start": None, "end": None}
    start = starts[-1]
    end = next((t for t, k in ms if k == "end" and t > start), None)
    before = [t for t, k in ms if t < start and k in ("baseline", "end")]
    return {"baseline": max(before) if before else None, "start": start, "end": end}


test_window.__test__ = False  # not a pytest function


def rebreathing_start(events: pd.DataFrame) -> float | None:
    """time_s of the marker that started the (latest) rebreathing: any label containing
    "rebreath", e.g. "Rebreathing" or "CO2 rebreathing started", or None."""
    return test_window(events)["start"]


def _meta_json(s: Session, key: str) -> dict:
    try:
        v = json.loads(s.meta.get(key, "") or "{}")
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def co2_response(s: Session, start: float | None = None, end: float | None = None, skip_s: float | None = None, exclude=()) -> dict:
    """Ventilatory response to CO2 from the session's (clean) breaths, the way the
    monitor fits it.

    Breaths before ``start`` (default: the rebreathing marker) are the baseline and are
    averaged. The line is fitted to each later breath's own minute ventilation
    (``mv_breath_lpm``) against its EtCO2, leaving out the first ``skip_s`` seconds of
    rebreathing (default: what the recording's header says, ``responseSkipSec``; 0 for
    files that do not say), everything from ``end`` on (default: the end-of-test
    marker), and the breath numbers in ``exclude``. With no marker every breath is
    fitted.

    Returns slope (L/min/mmHg), intercept, r, x_intercept (mmHg, where the line reaches
    MV = 0), n, baseline_n, baseline_etco2_mmHg, baseline_mv_lpm, rebreathing_start_s,
    end_s, skip_s, skipped_n, and, when the header has the subject's size,
    slope_per_kg (mL/min/mmHg/kg) and slope_per_m2 (L/min/mmHg/m2, Mosteller)."""
    base_start = None
    if start is None:
        w = test_window(s.events)
        base_start, start = w["baseline"], w["start"]
        if end is None:
            end = w["end"]
    test = _meta_json(s, "test")
    if skip_s is None:
        skip_s = float(test.get("responseSkipSec") or 0)
    b = s.breaths.dropna(subset=["etco2_mmHg", "mv_breath_lpm"])
    b = b[~b["breath"].isin(list(exclude))]
    if base_start is not None:
        b = b[b["time_s"] >= base_start]
    nan = float("nan")
    if start is None:
        base, reb, skipped = (b, b.iloc[0:0], b.iloc[0:0]) if base_start is not None else (b.iloc[0:0], b, b.iloc[0:0])
    else:
        base = b[b["time_s"] < start]
        after = b[b["time_s"] >= start]
        out_of_fit = (after["time_s"] < start + skip_s) | ((after["time_s"] >= end) if end is not None else False)
        reb, skipped = after[~out_of_fit], after[out_of_fit]
    out = {
        "rebreathing_start_s": start,
        "end_s": end,
        "skip_s": skip_s if start is not None else 0.0,
        "n": len(reb),
        "skipped_n": len(skipped),
        "slope": nan,
        "intercept": nan,
        "r": nan,
        "x_intercept": nan,
        "baseline_n": len(base),
        "baseline_etco2_mmHg": float(base["etco2_mmHg"].mean()) if len(base) else nan,
        "baseline_mv_lpm": float(base["mv_breath_lpm"].mean()) if len(base) else nan,
        "slope_per_kg": nan,
        "slope_per_m2": nan,
    }
    x = reb["etco2_mmHg"].to_numpy(float)
    y = reb["mv_breath_lpm"].to_numpy(float)
    if len(reb) >= 3 and np.ptp(x) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        out.update(slope=float(slope), intercept=float(intercept))
        if np.ptp(y) > 0:
            out["r"] = float(np.corrcoef(x, y)[0, 1])
        if slope != 0:
            out["x_intercept"] = float(-intercept / slope)
        h, w_kg = test.get("heightCm"), test.get("weightKg")
        if isinstance(w_kg, (int, float)) and w_kg > 0:
            out["slope_per_kg"] = float(slope) * 1000 / w_kg
            if isinstance(h, (int, float)) and h > 0:
                out["slope_per_m2"] = float(slope) / math.sqrt(h * w_kg / 3600)
    return out


def main(argv: list | None = None) -> None:
    ap = argparse.ArgumentParser(description="Summarize a CO2-Response session file.")
    ap.add_argument("path")
    ap.add_argument("--breaths", help="also write the per-breath table to this CSV")
    ap.add_argument("--all-breaths", action="store_true", help="keep breaths the noise screen left out (marked in breath_quality)")
    ap.add_argument("--exclude", default="", help="breath numbers to leave out of the CO2 response fit, e.g. 12,15")
    ap.add_argument("--skip", type=float, default=None, help="seconds of rebreathing to leave out of the fit (default: what the file's header says)")
    args = ap.parse_args(argv)
    s = load_session(args.path, clean=not args.all_breaths)
    dur = s.raw["time_s"].max()
    b = s.breaths
    left_out = len(s.breaths_all) - (s.breaths_all["breath_quality"] == "ok").sum()
    blanked = int(s.samples["co2_mmHg"].isna().sum() - s.raw.loc[s.raw["t_ms"].notna(), "co2_mmHg"].isna().sum())
    print(f"study {s.meta.get('study_code')}  started {s.meta.get('started')}  length {dur:.1f} s")
    print(f"{len(s.samples)} samples at {s.meta.get('rate_hz')} Hz, {len(s.breaths_all)} breaths ({left_out} left out as noisy), "
          f"{(s.events['type'] == 'ALARM_START').sum()} alarms, {(s.events['type'] == 'MARK').sum()} markers")
    if blanked:
        print(f"{blanked} samples inside noisy breaths blanked (raw volts kept)")
    if len(b):
        print(f"EtCO2 {b.etco2_mmHg.mean():.1f} mmHg (min {b.etco2_mmHg.min():.1f}, max {b.etco2_mmHg.max():.1f}); "
              f"RR {b.rr_bpm.mean():.1f}/min; VT {b.vt_ml.mean():.0f} mL; MV {b.mv_lpm.mean():.2f} L/min"
              f"{'' if args.all_breaths else ' (clean breaths only)'}")
    exclude = [int(x) for x in args.exclude.replace(" ", "").split(",") if x.isdigit()]
    r = co2_response(load_session(args.path) if args.all_breaths else s, skip_s=args.skip, exclude=exclude)
    if r["n"] >= 3:
        if r["rebreathing_start_s"] is not None:
            print(f"baseline (before rebreathing at {r['rebreathing_start_s']:.1f} s): EtCO2 {r['baseline_etco2_mmHg']:.1f} mmHg, "
                  f"MV {r['baseline_mv_lpm']:.2f} L/min, n {r['baseline_n']}")
            why = ([f"the first {r['skip_s']:g} s of rebreathing"] if r["skip_s"] else []) + ([f"after the end of the test at {r['end_s']:.1f} s"] if r["end_s"] is not None else [])
            if r["skipped_n"] or exclude:
                print(f"{r['skipped_n']} breaths outside the fit ({' and '.join(why) or 'none'})" + (f"; {len(exclude)} excluded by hand" if exclude else ""))
        else:
            print("no \"Rebreathing\" marker: the response is fitted to every breath")
        print(f"CO2 response: slope {r['slope']:.3f} L/min/mmHg, r {r['r']:.3f}, x-intercept {r['x_intercept']:.1f} mmHg, n {r['n']} (each breath's own MV)")
        if math.isfinite(r["slope_per_kg"]):
            per_m2 = f", {r['slope_per_m2']:.3f} L/min/mmHg/m2" if math.isfinite(r["slope_per_m2"]) else ""
            print(f"normalized for body size: {r['slope_per_kg']:.1f} mL/min/mmHg/kg{per_m2}")
    if args.breaths:
        b.to_csv(args.breaths, index=False)
        print(f"wrote {args.breaths}")


if __name__ == "__main__":
    main()
