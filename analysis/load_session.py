"""
Load a CO2-Response session file (one .csv per recording) for analysis.

    from load_session import load_session
    s = load_session("co2r_STUDY-01_20261105-010203.csv")
    s.samples      # one row per sample: time_s, volts, co2_mmHg, flow_lpm, pneumo_lpm, ...
    s.breaths      # one row per CLEAN breath: time_s, etco2_mmHg, rr_bpm, vt_ml, mv_lpm, ...
    s.breaths_all  # every breath, with breath_quality: ok, noisy, artifact or outlier
    s.events       # alarms, markers, connections: time_s, type, event
    s.meta         # study code, start time, firmware, sample rate, ...
    s.channels     # the calibration that was in use (dict)

Noise is removed before anything is analysed, the same way the monitor does it:

- the calibrated sample columns (co2_mmHg, flow_lpm, pneumo_lpm, ...) were recorded
  after spike removal and low-pass filtering; v0-v2 are the raw volts, untouched;
- every breath carries a quality from the monitor's breath screen (noisy, a partial
  breath with no plateau, an impossible value, or an isolated EtCO2 jump), and
  ``s.breaths`` keeps only the ``ok`` ones. Pass ``clean=False`` to keep them all.
  Files from before the screen existed (v2) are screened here, for isolated jumps and
  impossible values (noise can only be judged live, from the samples).

Or from the command line, for a quick summary and the per-breath table:

    python load_session.py co2r_STUDY-01_20261105-010203.csv --breaths breaths.csv
    python load_session.py session.csv --breaths all.csv --all-breaths

Needs pandas (pip install pandas). Reads v2 and v3 files; v1 files from the first
version of the app can be converted by importing them in the app and exporting again.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BREATH_COLS = ["time_s", "wall_time_iso", "breath", "etco2_mmHg", "fico2_mmHg", "rr_bpm", "vt_ml", "mv_lpm", "peak_insp_lpm", "breath_quality"]
SAMPLE_COLS = ["time_s", "wall_time_iso", "t_ms", "seq", "v0", "v1", "v2", "co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm"]


@dataclass
class Session:
    meta: dict
    channels: dict
    samples: pd.DataFrame
    breaths: pd.DataFrame
    events: pd.DataFrame
    breaths_all: pd.DataFrame = field(repr=False)
    raw: pd.DataFrame = field(repr=False)


def _screen(etco2: list[float], quality: list[str]) -> list[str]:
    """The monitor's own breath screen (co2response.processing.denoise), if available."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from co2response.processing.denoise import screen_series
    except ImportError:
        print("note: co2response not found next to this script; older files are not screened", file=sys.stderr)
        return [q or "ok" for q in quality]
    return screen_series(etco2, quality)


def load_session(path: str, clean: bool = True) -> Session:
    meta: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            key, _, value = line[1:].strip().partition(",")
            meta[key.strip()] = value
    version = meta.get("CO2-Response session", "")
    if version not in ("v2", "v3"):
        raise ValueError(f"{path}: expected a v2 or v3 session file, got {version or 'no version line'}. Import it in the app and export it again to convert.")

    raw = pd.read_csv(path, comment="#", dtype={"event": "string", "breath_quality": "string"})
    raw["wall_time_iso"] = pd.to_datetime(raw["wall_time_iso"], utc=True)
    if "breath_quality" not in raw:
        raw["breath_quality"] = pd.NA

    samples = raw.loc[raw["t_ms"].notna(), SAMPLE_COLS].reset_index(drop=True)
    breaths = raw.loc[raw["breath"].notna(), BREATH_COLS].reset_index(drop=True)
    breaths["breath"] = breaths["breath"].astype(int)

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
    breaths["breath_quality"] = quality
    good = breaths[breaths["breath_quality"] == "ok"].reset_index(drop=True)

    try:
        channels = json.loads(meta.get("channels", "{}"))
    except json.JSONDecodeError:
        channels = {}
    return Session(meta=meta, channels=channels, samples=samples, breaths=good if clean else breaths, events=ev, breaths_all=breaths, raw=raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize a CO2-Response session file.")
    ap.add_argument("path")
    ap.add_argument("--breaths", help="also write the per-breath table to this CSV")
    ap.add_argument("--all-breaths", action="store_true", help="keep breaths the noise screen left out (marked in breath_quality)")
    args = ap.parse_args()
    s = load_session(args.path, clean=not args.all_breaths)
    dur = s.raw["time_s"].max()
    b = s.breaths
    left_out = len(s.breaths_all) - (s.breaths_all["breath_quality"] == "ok").sum()
    print(f"study {s.meta.get('study_code')}  started {s.meta.get('started')}  length {dur:.1f} s")
    print(f"{len(s.samples)} samples at {s.meta.get('rate_hz')} Hz, {len(s.breaths_all)} breaths ({left_out} left out as noisy), "
          f"{(s.events['type'] == 'ALARM_START').sum()} alarms, {(s.events['type'] == 'MARK').sum()} markers")
    if len(b):
        print(f"EtCO2 {b.etco2_mmHg.mean():.1f} mmHg (min {b.etco2_mmHg.min():.1f}, max {b.etco2_mmHg.max():.1f}); "
              f"RR {b.rr_bpm.mean():.1f}/min; VT {b.vt_ml.mean():.0f} mL; MV {b.mv_lpm.mean():.2f} L/min"
              f"{'' if args.all_breaths else ' (clean breaths only)'}")
    if args.breaths:
        b.to_csv(args.breaths, index=False)
        print(f"wrote {args.breaths}")


if __name__ == "__main__":
    main()
