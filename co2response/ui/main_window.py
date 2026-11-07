"""The main window: banner, header, alarm strip, and the monitor / settings / review screens.

Timers drive everything: the engine reads the source every 20 ms, runs its alarm checks
4 times a second, and the waveforms are redrawn about 30 times a second.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..engine.monitor import MonitorEngine
from . import theme
from .header import AlarmStrip, Banner, Header
from .monitor_screen import MonitorScreen
from .port_dialog import PortDialog
from .widgets import Toast


class MainWindow(QMainWindow):
    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle(f"{APP_NAME} Monitor")
        self.setMinimumSize(1100, 700)
        self.resize(1366, 800)

        root = QWidget()
        root.setObjectName("root")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        self.banner = Banner()
        self.header = Header()
        self.strip = AlarmStrip()
        self.strip.acknowledge.connect(engine.acknowledge)
        self.strip.arm_audio.connect(lambda: engine.arm_audio())
        self.stack = QStackedWidget()
        for w in (self.banner, self.header, self.strip):
            lay.addWidget(w)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.monitor = MonitorScreen(engine)
        self.monitor.open_settings.connect(self.open_settings)
        self.monitor.toast.connect(self.toast)
        self.monitor.toggle_recording.connect(self.toggle_recording)
        self.monitor.choose_port_requested.connect(self.choose_port)
        self.stack.addWidget(self.monitor)
        self.settings_screen = None
        self.review = None
        self.toaster = Toast(root)

        QShortcut(QKeySequence("M"), self, activated=self._hotkey_mark)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)
        engine.high_alarm_listeners.append(lambda msg: QApplication.alert(self, 0))

        self._theme_pref = None
        self._title_flip = False
        self.poll_timer = QTimer(self, timeout=engine.poll)
        self.poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.poll_timer.start(20)
        self.tick_timer = QTimer(self, timeout=self._tick)
        self.tick_timer.start(250)
        self.frame_timer = QTimer(self, timeout=self.monitor.frame)
        self.frame_timer.start(33)
        self.apply_theme()
        self._tick()

    # ---------- screens

    def _settings(self):
        if self.settings_screen is None:
            from .settings.screen import SettingsScreen

            self.settings_screen = SettingsScreen(self.engine, self)
            self.stack.addWidget(self.settings_screen)
        return self.settings_screen

    def open_settings(self, tab: str = "") -> None:
        s = self._settings()
        if tab:
            s.select(tab)
        self.stack.setCurrentWidget(s)
        s.refresh(self.engine.snapshot(), force=True)

    def show_monitor(self) -> None:
        self.stack.setCurrentWidget(self.monitor)

    def open_simulation(self) -> None:
        """The monitor with the simulation panel open."""
        self.show_monitor()
        self.monitor.show_sim(True)

    def open_review(self, sid: str) -> None:
        from .review import ReviewScreen

        if self.review is not None:
            self.stack.removeWidget(self.review)
            self.review.deleteLater()
        self.review = ReviewScreen(self.engine, sid, self)
        self.stack.addWidget(self.review)
        self.stack.setCurrentWidget(self.review)

    def close_review(self) -> None:
        self.open_settings("data")

    # ---------- actions

    def toast(self, msg: str, ms: int = 4000) -> None:
        self.toaster.show_message(msg, ms)

    def toggle_recording(self) -> None:
        e = self.engine
        if e.recorder.active:
            r = e.stop_recording()
            if r.path is not None and r.error is None:
                self.toast(f"Saved {r.file_name} in {r.path.parent}. It is also listed in Settings > Data & recordings.", 6000)
            elif r.path is not None:
                self.toast(f"The sessions folder could not be written ({r.error}); saved {r.path} instead.", 9000)
            else:
                self.toast(f"Could not save {r.file_name}: {r.error}", 9000)
        else:
            rec = e.start_recording()
            where = "written to disk every second" if rec else "kept in memory: the sessions folder cannot be written"
            self.toast(f"Recording {e.recorder.file_name} ({where}).")

    def choose_port(self) -> None:
        dlg = PortDialog(self, self.engine.port_lister, self.engine.settings.last_port)
        if dlg.exec() and dlg.device:
            if not self.engine.connect("serial", port=dlg.device):
                self.toast(self.engine.snapshot().status_detail, 7000)

    def _hotkey_mark(self) -> None:
        if self.stack.currentWidget() is self.monitor and not self.monitor.drawer.isVisible():
            self.monitor.mark()

    def _escape(self) -> None:
        self.monitor.drawer.hide()
        self.monitor.popover.hide()
        self.monitor.set_focus(None)

    # ---------- refresh

    def apply_theme(self) -> None:
        pref = self.engine.settings.theme
        pal = theme.resolve(pref)
        self._theme_pref = pref
        if pal is theme.current and self.property("themed"):
            return
        theme.current = pal
        self.setProperty("themed", True)
        QApplication.instance().setStyleSheet(theme.stylesheet(pal))
        self.banner.apply_theme()
        self.monitor.trend.apply_theme()
        if self.review is not None:
            self.review.apply_theme()
        for w in self.findChildren(QWidget):
            w.update()

    def _tick(self) -> None:
        e = self.engine
        e.tick()
        if e.settings.theme != self._theme_pref or (e.settings.theme == "system" and theme.resolve("system") is not theme.current):
            self.apply_theme()
        s = e.snapshot()
        t = time.monotonic()
        blink_fast = (t % 0.5) >= 0.25
        blink_slow = (t % 2.0) >= 1.0
        self.header.update_from(s)
        self.strip.update_from(s, blink_fast, blink_slow)
        cur = self.stack.currentWidget()
        if cur is self.monitor:
            self.monitor.refresh(s, blink_fast)
        elif cur is self.settings_screen:
            self.settings_screen.refresh(s)
        # the title flashes during a high-priority alarm so it shows in the taskbar
        if e.alarms.active_priority() == "high":
            self._title_flip = not self._title_flip
            self.setWindowTitle(f"▲ ALARM · {APP_NAME}" if self._title_flip else f"{APP_NAME} Monitor")
        elif self.windowTitle() != f"{APP_NAME} Monitor":
            self.setWindowTitle(f"{APP_NAME} Monitor")

    def closeEvent(self, ev) -> None:  # noqa: N802
        e = self.engine
        if e.recorder.active:
            ans = QMessageBox.question(self, "Stop recording?", f"{e.recorder.file_name} is still recording. Stop it, save the file and quit?")
            if ans != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
        e.shutdown()
        ev.accept()
