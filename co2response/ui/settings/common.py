"""Helpers shared by the settings pages."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QGridLayout, QWidget

from ...engine.monitor import Snapshot
from ...storage.session_file import ParsedSession


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


def parse_then(
    parent: QWidget,
    library,
    sid: str,
    done: Callable[[ParsedSession], None],
    fail: Callable[[Exception], None],
    progress: Callable[[float], None] | None = None,
    budget_sec: float = 0.012,
) -> None:
    """Parse a stored session and call ``done(parsed)``, or ``fail(error)``.

    A small file is parsed at once. A long recording is parsed in steps of about
    12 ms on a timer, with ``progress(fraction)`` in between, so the monitor keeps
    polling the device and checking alarms while the file opens."""
    rec = library.get(sid)
    try:
        if rec is None:
            raise ValueError("This session is no longer in the library.")
        if rec.bytes <= library.BIG_SESSION_BYTES:
            parsed = library.parse(sid)
        else:
            parsed = None
    except (OSError, ValueError) as e:
        fail(e)
        return
    if parsed is not None:
        done(parsed)
        return
    steps = library.parse_steps(sid)
    timer = QTimer(parent)
    timer.setInterval(0)

    def step() -> None:
        end = time.perf_counter() + budget_sec
        try:
            while time.perf_counter() < end:
                r = next(steps)
                if isinstance(r, ParsedSession):
                    timer.stop()
                    timer.deleteLater()
                    done(r)
                    return
                if progress:
                    progress(r)
        except (OSError, ValueError, StopIteration) as e:
            timer.stop()
            timer.deleteLater()
            fail(e)

    timer.timeout.connect(step)
    timer.start()


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
