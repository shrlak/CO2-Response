"""
Load a CO2-Response session file (one .csv per recording) for analysis.

    from load_session import load_session
    s = load_session("co2r_STUDY-01_20261105-010203.csv")
    s.samples   # one row per sample: time_s, volts, co2_mmHg, flow_lpm, pneumo_lpm, ...
    s.breaths   # one row per breath: time_s, etco2_mmHg, rr_bpm, vt_ml, mv_lpm, ...
    s.events    # alarms, markers, connections: time_s, type, event
    s.meta      # study code, start time, firmware, sample rate, ...
    s.channels  # the calibration that was in use (dict)

Or from the command line, for a quick summary and the per-breath table:

    python load_session.py co2r_STUDY-01_20261105-010203.csv --breaths breaths.csv

Needs pandas (pip install pandas). Reads v2 files; v1 files from the first version
of the app can be converted by importing them in the app and exporting again.
"""
from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass, field

import pandas as pd

BREATH_COLS = ["time_s", "wall_time_iso", "breath", "etco2_mmHg", "fico2_mmHg", "rr_bpm", "vt_ml", "mv_lpm", "peak_insp_lpm"]
SAMPLE_COLS = ["time_s", "wall_time_iso", "t_ms", "seq", "v0", "v1", "v2", "co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm"]


@dataclass
class Session:
    meta: dict
    channels: dict
    samples: pd.DataFrame
    breaths: pd.DataFrame
    events: pd.DataFrame
    raw: pd.DataFrame = field(repr=False)


def load_session(path: str) -> Session:
    meta: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            key, _, value = line[1:].strip().partition(",")
            meta[key.strip()] = value
    version = meta.get("CO2-Response session", "")
    if version != "v2":
        raise ValueError(f"{path}: expected a v2 session file, got {version or 'no version line'}. Import it in the app and export it again to convert.")

    raw = pd.read_csv(path, comment="#", dtype={"event": "string"})
    raw["wall_time_iso"] = pd.to_datetime(raw["wall_time_iso"], utc=True)

    samples = raw.loc[raw["t_ms"].notna(), SAMPLE_COLS].reset_index(drop=True)
    breaths = raw.loc[raw["breath"].notna(), BREATH_COLS].reset_index(drop=True)
    breaths["breath"] = breaths["breath"].astype(int)

    ev = raw.loc[raw["event"].notna(), ["time_s", "wall_time_iso", "event"]]
    ev = ev.assign(event=ev["event"].str.split(r" \| ", regex=True)).explode("event").reset_index(drop=True)
    ev.insert(2, "type", ev["event"].str.extract(r"^([A-Z_]+)", expand=False).fillna("NOTE"))

    try:
        channels = json.loads(meta.get("channels", "{}"))
    except json.JSONDecodeError:
        channels = {}
    return Session(meta=meta, channels=channels, samples=samples, breaths=breaths, events=ev, raw=raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize a CO2-Response session file.")
    ap.add_argument("path")
    ap.add_argument("--breaths", help="also write the per-breath table to this CSV")
    args = ap.parse_args()
    s = load_session(args.path)
    dur = s.raw["time_s"].max()
    b = s.breaths
    print(f"study {s.meta.get('study_code')}  started {s.meta.get('started')}  length {dur:.1f} s")
    print(f"{len(s.samples)} samples at {s.meta.get('rate_hz')} Hz, {len(b)} breaths, "
          f"{(s.events['type'] == 'ALARM_START').sum()} alarms, {(s.events['type'] == 'MARK').sum()} markers")
    if len(b):
        print(f"EtCO2 {b.etco2_mmHg.mean():.1f} mmHg (min {b.etco2_mmHg.min():.1f}, max {b.etco2_mmHg.max():.1f}); "
              f"RR {b.rr_bpm.mean():.1f}/min; VT {b.vt_ml.mean():.0f} mL; MV {b.mv_lpm.mean():.2f} L/min")
    if args.breaths:
        b.to_csv(args.breaths, index=False)
        print(f"wrote {args.breaths}")


if __name__ == "__main__":
    main()
