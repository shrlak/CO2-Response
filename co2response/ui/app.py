"""Start the desktop app."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from .. import APP_NAME
from ..alarms.audio import AlarmAudio
from ..engine.monitor import MonitorEngine
from ..storage.session_file import read_session
from .main_window import MainWindow

FONTS = Path(__file__).resolve().parent.parent / "fonts"


def load_fonts() -> None:
    if FONTS.is_dir():
        for f in sorted(FONTS.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))


def run(sim: bool = False, port: str | None = None, replay: str | None = None, argv: list[str] | None = None, exit_after: float | None = None, situation: str | None = None, speed: float | None = None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CMU BME")
    load_fonts()
    engine = MonitorEngine(audio=AlarmAudio(fallback_beep=QApplication.beep))
    engine.arm_audio(confirm=False)
    win = MainWindow(engine)
    recovered = engine.init()
    win.show()

    def start() -> None:
        if recovered:
            n = len(recovered)
            win.toast(f"Recovered {n} recording{'s' if n > 1 else ''} left unfinished when the app closed. Find {'them' if n > 1 else 'it'} in Settings > Data & recordings.", 9000)
        if speed is not None:
            engine.set_sim(speed=float(speed))
        if situation:
            engine.apply_situation(situation)
        elif sim:
            engine.connect("simulator")
        elif port:
            if not engine.connect("serial", port=port):
                win.toast(engine.snapshot().status_detail, 8000)
        elif replay:
            try:
                s = read_session(replay)
                engine.connect("replay", session=s, file_name=Path(replay).name)
            except (OSError, ValueError) as e:
                win.toast(f"Could not replay {replay}: {e}", 8000)

    QTimer.singleShot(0, start)
    if exit_after is not None:
        # an installation check: run for a moment, say what the monitor measured, and close
        def finish() -> None:
            n = engine.snapshot().numbers
            print(f"{APP_NAME}: ran {exit_after:g} s; {len(engine.breaths)} breaths, EtCO2 {n.etco2:.0f} mmHg, RR {n.rr:.0f} /min", flush=True)
            win.close()
            app.quit()

        QTimer.singleShot(int(exit_after * 1000), finish)
    return app.exec()
