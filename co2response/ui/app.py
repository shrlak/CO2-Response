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


def watch_for_updates(app, engine, win) -> None:
    """Standalone app: look for a newer release in the background (at most once a day) and
    tell the operator what was found. It installs the next time the app starts, never now."""
    from .. import updater

    if not updater.is_frozen():
        return
    if updater.enabled_by_environment() and engine.settings.auto_update:
        updater.background.start()

    def drain() -> None:
        while not updater.background.messages.empty():
            msg = updater.background.messages.get()
            engine._add_log(msg)
            win.toast(msg, 9000)

    timer = QTimer(app)
    timer.timeout.connect(drain)
    timer.start(3000)
    app._update_timer = timer  # keep it alive


def update_on_close(app, engine) -> str:
    """Standalone app, once the window has closed: check for a newer release, download it
    and install it, so the next start runs it. A small window shows what it is doing; Skip
    stops the check or the download (never a half-done install). Returns what happened."""
    import threading
    import time

    from PySide6.QtWidgets import QProgressDialog

    from .. import updater

    if not updater.is_frozen() or not updater.enabled_by_environment() or not engine.settings.auto_update:
        return ""
    state: dict = {"done": 0, "total": 0, "skip": False, "installing": False, "msg": None}

    def progress(done: int, total: int) -> None:
        state["done"], state["total"] = done, total
        if state["skip"]:
            raise updater.UpdateError("skipped when closing")

    def installing() -> None:
        state["installing"] = True

    def work() -> None:
        state["msg"] = updater.update_now(progress=progress, installing=installing)

    dlg = QProgressDialog("Checking for updates …", "Skip", 0, 0)
    dlg.setWindowTitle(APP_NAME)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.show()
    t = threading.Thread(target=work, name="update at close", daemon=True)
    t.start()
    while t.is_alive():
        app.processEvents()
        if dlg.wasCanceled() and not state["installing"]:
            state["skip"] = True
            if not state["total"]:
                break  # still asking GitHub: leave it (nothing is downloaded or installed yet)
        if state["installing"]:
            dlg.setLabelText("Installing the update …")
            dlg.setCancelButton(None)
        elif state["total"]:
            dlg.setLabelText(f"Downloading the update … {100 * state['done'] // max(1, state['total'])} %")
            dlg.setRange(0, 100)
            dlg.setValue(100 * state["done"] // max(1, state["total"]))
        time.sleep(0.03)
    msg = state["msg"] or ""
    if msg.startswith("Updated to"):
        dlg.setRange(0, 1)
        dlg.setValue(1)
        dlg.setLabelText(msg)
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.03)
    dlg.close()
    if msg:
        print(msg, flush=True)
    return msg


def run(
    sim: bool = False,
    port: str | None = None,
    replay: str | None = None,
    argv: list[str] | None = None,
    exit_after: float | None = None,
    situation: str | None = None,
    speed: float | None = None,
    resume: str | None = None,
) -> int:
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
        if resume:
            try:
                engine.resume_session(read_session(resume), Path(resume).name)
                win.toast(f"Recording continues in {engine.recorder.file_name}.", 8000)
            except (OSError, ValueError) as e:
                win.toast(f"Could not continue {resume}: {e}", 8000)
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
    watch_for_updates(app, engine, win)
    if exit_after is not None:
        # an installation check: run for a moment, say what the monitor measured, and close
        def finish() -> None:
            n = engine.snapshot().numbers
            print(f"{APP_NAME}: ran {exit_after:g} s; {len(engine.breaths)} breaths, EtCO2 {n.etco2:.0f} mmHg, RR {n.rr:.1f} /min", flush=True)
            win.close()
            app.quit()

        QTimer.singleShot(int(exit_after * 1000), finish)
    code = app.exec()
    if exit_after is None:
        update_on_close(app, engine)
    return code
