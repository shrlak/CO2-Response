"""The settings screen: a navigation column and one page per section.
Alarms keep running while it is open."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...engine.monitor import MonitorEngine, Snapshot
from ..widgets import button, label, scroll_page
from .calibration import CalibrationPage
from .connection import ConnectionPage
from .data import DataPage
from .diagnostics import DiagnosticsPage
from .display import AlarmsPage, DisplayPage

TABS = [
    ("connection", "Connection"),
    ("calibration", "Calibration"),
    ("alarms", "Alarms"),
    ("display", "Display & units"),
    ("data", "Data & recordings"),
    ("diagnostics", "Diagnostics"),
]


class SettingsScreen(QWidget):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        nav = QFrame()
        nav.setProperty("panel", True)
        nav.setFixedWidth(230)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(10, 10, 10, 10)
        nl.setSpacing(4)
        nl.addWidget(button("← Back to monitor", window.show_monitor, kind="primary"))
        nl.addSpacing(10)
        self.group = QButtonGroup(self)
        for i, (_, text) in enumerate(TABS):
            b = QPushButton(text.replace("&", "&&"))
            b.setCheckable(True)
            b.setProperty("nav", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            nl.addWidget(b)
        nl.addStretch(1)
        nl.addWidget(label("Alarms keep running while you are on this screen.", "note", wrap=True))
        lay.addWidget(nav)

        body = QFrame()
        body.setProperty("panel", True)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 18, 12, 18)
        self.stack = QStackedWidget()
        bl.addWidget(self.stack)
        lay.addWidget(body, 1)

        self.pages = {
            "connection": ConnectionPage(engine, window),
            "calibration": CalibrationPage(engine, window),
            "alarms": AlarmsPage(engine, window),
            "display": DisplayPage(engine),
            "data": DataPage(engine, window),
            "diagnostics": DiagnosticsPage(engine, window),
        }
        widths = {"alarms": 760, "display": 800}
        for key, _ in TABS:
            self.stack.addWidget(scroll_page(self.pages[key], widths.get(key)))
        self.group.idClicked.connect(self._select_index)
        self.select("connection")

    def _select_index(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.group.button(i).setChecked(True)
        self.current().refresh(self.engine.snapshot(), force=True)

    def select(self, key: str) -> None:
        i = [k for k, _ in TABS].index(key)
        self._select_index(i)

    def current(self):
        return self.pages[TABS[self.stack.currentIndex()][0]]

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.current().refresh(s, force)
