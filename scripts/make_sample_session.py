"""Write sample-data/synthetic_session.csv: 150 s of simulated data (60 s of normal
breathing, then a CO2 ramp that crosses the 50 mmHg limit), recorded through the same
parser, processing, alarm engine and session recorder as the live app.

    python scripts/make_sample_session.py [output.csv]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from co2response import __version__  # noqa: E402
from co2response.alarms.engine import AlarmEngine  # noqa: E402
from co2response.channels import DEFAULT_CHANNELS, clone_channels  # noqa: E402
from co2response.jsfmt import parse_iso_ms  # noqa: E402
from co2response.processing.calibration import invert_calibration  # noqa: E402
from co2response.processing.metrics import minute_ventilation, respiratory_rate  # noqa: E402
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline  # noqa: E402
from co2response.protocol import parse_line  # noqa: E402
from co2response.settings import DEFAULT_ALARMS  # noqa: E402
from co2response.sim.core import Encoders, SimulatorCore  # noqa: E402
from co2response.storage.library import SessionLibrary  # noqa: E402
from co2response.storage.recorder import SessionRecorder  # noqa: E402
from co2response.storage.session_file import SessionMeta  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def make(out: Path, app_version: str = __version__) -> int:
    ch = clone_channels(DEFAULT_CHANNELS)
    c = DEFAULT_CHANNELS["channels"]
    enc = Encoders(
        co2=lambda y: invert_calibration(c["co2"]["calibration"], y),
        flow=lambda y: invert_calibration(c["flow"]["calibration"], y),
        pneumo=lambda y: invert_calibration(c["pneumo"]["calibration"], y),
    )
    core = SimulatorCore(enc, scenario="normal", seed=2026, ramp_mm_hg_per_min=12)
    pipe = Pipeline(ch, DEFAULT_PROCESSING)
    start = parse_iso_ms("2026-11-05T05:00:00.000Z")
    wall = [start]
    with tempfile.TemporaryDirectory() as tmp:
        lib = SessionLibrary(Path(tmp))
        rec = SessionRecorder(lib, clock=lambda: wall[0])
        meta = SessionMeta("SAMPLE", start, app_version, "Simulator", "sim-1.0.0", 100, ch, DEFAULT_PROCESSING, DEFAULT_ALARMS)
        rec.start(meta, start)

        def log(e):
            aid = f" {e.id}" if e.id else ""
            pri = f" {e.priority}" if e.priority else ""
            rec.event(e.t, f"ALARM_{e.kind.upper()}{aid}{pri} {e.message}")

        alarms = AlarmEngine(DEFAULT_ALARMS, log)
        breaths = []
        rec.event(wall[0], "RECORDING_STARTED source=Simulator")
        for i in range(150 * 100):
            if i == 60 * 100:
                core.model.set_params(scenario="co2Ramp")
                rec.event(wall[0], "MARK CO2 rebreathing started")
            for line in core.next_sample():
                d = parse_line(line)
                if d.kind != "data":
                    continue
                p = pipe.process(d.t_ms, d.volts)
                if p.breath:
                    b = p.breath
                    breaths.append(b)
                    rec.breath(b.etco2, b.fico2, respiratory_rate(breaths), b.vt_ml, minute_ventilation(breaths, b.t_end), b.peak_insp_lpm)
                    alarms.on_breath(b.etco2, wall[0])
                rec.sample(wall[0], d.t_ms, d.seq, d.volts, p.co2_mmhg, p.co2_pct, p.flow_lpm, p.pneumo_pa, p.pneumo_lpm)
            wall[0] += 10
        rec.event(wall[0], "RECORDING_STOPPED")
        stored, _ = rec.stop()
        text = lib.text(stored.id)
    out.write_text(text, encoding="utf-8", newline="")
    return len(breaths)


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample-data" / "synthetic_session.csv"
    n = make(dest)
    print(f"wrote {dest}: {dest.stat().st_size / 1e6:.2f} MB, {n} breaths")
