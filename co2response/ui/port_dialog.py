"""Choose the Arduino's serial port."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..sources.serial_source import PortInfo, list_ports
from .widgets import button, hbox, label


class PortDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, lister=list_ports, current: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect Arduino (USB)")
        self.setMinimumWidth(520)
        self.lister = lister
        self.current = current
        self.device: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Choose the Arduino's port", "h2"))
        lay.addWidget(
            label(
                "Arduinos are listed first. On Windows it is a COMx port; on a Mac /dev/cu.usbmodem…; on Linux /dev/ttyACM0 or /dev/ttyUSB0. "
                "If it is not here, check the USB cable (some cables only carry power) and press Refresh.",
                "note",
                wrap=True,
            )
        )
        self.list = QListWidget()
        self.list.setMinimumHeight(180)
        self.list.itemDoubleClicked.connect(lambda _: self._ok())
        self.list.currentRowChanged.connect(lambda _: self._sync())
        lay.addWidget(self.list)
        self.empty = label("No serial ports found.", "warn")
        lay.addWidget(self.empty)
        self.connect_btn = button("Connect", self._ok, kind="primary")
        lay.addLayout(hbox(button("Refresh", self.refresh), 1, button("Cancel", self.reject), self.connect_btn))
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        ports: list[PortInfo] = self.lister()
        for p in ports:
            tag = "  ·  likely Arduino" if p.likely_arduino else ""
            it = QListWidgetItem(f"{p.label}{tag}")
            it.setData(Qt.ItemDataRole.UserRole, p.device)
            self.list.addItem(it)
        self.empty.setVisible(not ports)
        pick = next((i for i, p in enumerate(ports) if p.device == self.current), None)
        if pick is None:
            pick = next((i for i, p in enumerate(ports) if p.likely_arduino), 0 if ports else None)
        if pick is not None:
            self.list.setCurrentRow(pick)
        self._sync()

    def _sync(self) -> None:
        self.connect_btn.setEnabled(self.list.currentItem() is not None)

    def _ok(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        self.device = it.data(Qt.ItemDataRole.UserRole)
        self.accept()
