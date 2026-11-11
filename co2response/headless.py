"""A whole simulated rebreathing test with no window, as fast as the computer runs it.

    co2response --fast-test normal      # or blunted, brisk

The real engine runs on a fake clock (the same way the tests do), so a test of three
or four minutes takes about a second. The session is recorded like any other, the PDF
report is written next to it, and the MV-vs-EtCO2 graph is printed in the terminal.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from . import APP_NAME, __version__
from .alarms.audio import AlarmAudio
from .engine.monitor import MonitorEngine
from .settings import SettingsStore

TESTS = {"normal": "test_normal", "blunted": "test_blunted", "brisk": "test_brisk"}


def run_fast_test(kind: str = "normal", out_dir: str | Path | None = None, board: str = "digital", seed: int = 7, variability: float | None = None, quiet: bool = False):
    """Run one whole simulated test. Returns (engine, session path, pdf path)."""
    t = [round(time.time()) * 1000.0]  # a fake clock from now, advanced as fast as the computer runs
    store = SettingsStore(Path(tempfile.mkdtemp(prefix="co2r-fast-")))
    e = MonitorEngine(store=store, audio=AlarmAudio(), clock=lambda: t[0], prevent_sleep=False, sessions_dir=Path(out_dir) if out_dir else None)
    e.update_settings(hardware=board, study_code=f"SIM-{kind.upper()}")
    e.start_recording()
    e.apply_situation(TESTS[kind])
    if variability is not None:
        e.set_sim(variability_pct=float(variability))
    started = False
    for i in range(int(900 * 1000 / 20)):  # at most 15 simulated minutes
        t[0] += 20
        e.poll()
        if i % 12 == 0:
            e.tick()
        started = started or e._sim_test is not None
        if started and e._sim_test is None:
            break
    for _ in range(50):  # the last breaths and the recording's last write
        t[0] += 20
        e.poll()
    e.tick()
    res = e.stop_recording()
    pdf = None
    if res.path:
        from .tui.app import write_report

        pdf = write_report(res.path)
    if not quiet:
        print_result(e, res.path, pdf)
    e.disconnect()
    return e, res.path, pdf


def print_result(e: MonitorEngine, csv: Path | None, pdf: Path | None) -> None:
    from rich.console import Console
    from rich.panel import Panel

    from .processing.metrics import co2_response_summary
    from .tui.canvas import Canvas, draw_response

    con = Console()
    width = min(110, max(60, con.width - 4))
    cv = Canvas(width, 24)
    res = e.co2_response()
    draw_response(cv, res, title=e.settings.study_code)
    con.print(Panel(cv.render(), title="Ventilatory response · MV of each breath vs its EtCO2", border_style="grey35", expand=False))
    con.print(co2_response_summary(res, lambda v, d: f"{v:.{d}f}"))
    good = [q for q in e.trend if q.quality == "ok"]
    con.print(f"{len(good)} breaths; minute ventilation of each breath = VT × 60 / breath length.")
    if csv:
        con.print(f"Session: {csv}")
    if pdf:
        con.print(f"Report:  {pdf}")


def main(kind: str, out_dir: str | None = None, board: str = "digital", variability: float | None = None) -> int:
    t0 = time.perf_counter()
    _, csv, _ = run_fast_test(kind, out_dir, board, variability=variability)
    print(f"{APP_NAME} {__version__}: whole simulated test in {time.perf_counter() - t0:.1f} s")
    return 0 if csv else 1
