"""Helpers shared by the settings pages."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QGridLayout, QWidget

from ...engine.monitor import Snapshot


def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


def save_text(parent: QWidget, name: str, text: str, kind: str = "CSV files (*.csv)") -> Path | None:
    """Ask where to save, then write. Returns the path, or None if cancelled."""
    path, _ = QFileDialog.getSaveFileName(parent, "Save as", str(downloads_dir() / name), f"{kind};;All files (*)")
    if not path:
        return None
    Path(path).write_text(text, encoding="utf-8", newline="")
    return Path(path)


def save_bytes(parent: QWidget, name: str, data: bytes, kind: str = "PDF files (*.pdf)") -> Path | None:
    """Ask where to save a binary file, then write it. None if cancelled."""
    path, _ = QFileDialog.getSaveFileName(parent, "Save as", str(downloads_dir() / name), f"{kind};;All files (*)")
    if not path:
        return None
    Path(path).write_bytes(data)
    return Path(path)


class FlowGrid(QWidget):
    """Cards in as many columns as fit (each at least ``min_width`` wide)."""

    def __init__(self, min_width: int = 420) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.min_width = min_width
        self.items: list[QWidget] = []
        self._cols = 0

    def add(self, w: QWidget) -> None:
        self.items.append(w)
        self._reflow(force=True)

    def _reflow(self, force: bool = False) -> None:
        cols = max(1, (self.width() + 12) // (self.min_width + 12)) if self.width() > 50 else 2
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols, alignment=Qt.AlignmentFlag.AlignTop)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        rows = (len(self.items) + cols - 1) // cols
        self.grid.setRowStretch(rows, 1)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._reflow()


class Page(QWidget):
    """A settings page; ``refresh`` is called 4 times a second while it is shown."""

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        pass
