"""Write sample-data/synthetic_session.csv: 150 s recorded through the real monitor
engine (the same path as the live app, noise removal included) on a simulated clock:
60 s of breathing with artifacts (ADC spikes and partial breaths, which the noise
removal takes out), then a CO2 ramp (rebreathing) that crosses the 50 mmHg limit.

    python scripts/make_sample_session.py [output.csv]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from co2response.alarms.audio import SilentAudio  # noqa: E402
from co2response.engine.monitor import MonitorEngine  # noqa: E402
from co2response.jsfmt import parse_iso_ms  # noqa: E402
from co2response.settings import SettingsStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
START = parse_iso_ms("2026-11-05T05:00:00.000Z")


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(out: Path, seconds: int = 150) -> int:
    clock = _Clock(START)
    with tempfile.TemporaryDirectory() as tmp:
        e = MonitorEngine(store=SettingsStore(Path(tmp) / "cfg"), audio=SilentAudio(), clock=clock, sessions_dir=Path(tmp) / "s", prevent_sleep=False)
        e.update_settings(study_code="SAMPLE")
        e.set_sim(scenario="artifacts")
        e.connect("simulator")
        e.source.core.model.set_params(ramp_mm_hg_per_min=12)
        e.start_recording()
        for step in range(seconds * 50):
            if step == 60 * 50:
                e.source.core.model.set_params(scenario="co2Ramp")
                e.add_marker("CO2 rebreathing started")
            clock.t += 20
            e.poll()
            if step % 12 == 11:
                e.tick()
        res = e.stop_recording()
        shutil.copyfile(res.path, out)
        return len(e.breaths)


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample-data" / "synthetic_session.csv"
    n = make(dest)
    print(f"wrote {dest}: {dest.stat().st_size / 1e6:.2f} MB, {n} breaths")
