"""Small building blocks shared by the screens: labelled fields, segmented buttons,
cards, stat tiles, the toast, and number/time formatting."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# ------------------------------------------------------------------ formatting


def fmt(v: float | None, digits: int = 0) -> str:
    if v is None or not math.isfinite(v):
        return "--"
    s = f"{v:.{digits}f}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s  # no "-0"


def hms(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def duration(ms: float) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def mss(ms: float) -> str:
    s = math.ceil(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def elapsed(s: float) -> str:
    """Seconds → h:mm:ss."""
    t = max(0, round(s))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def size_text(b: float) -> str:
    return f"{b / 1e6:.1f} MB" if b >= 1e6 else f"{max(1, round(b / 1e3))} kB"


def stamp(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d %b %Y %H:%M")


# ------------------------------------------------------------------ styling helpers


def set_prop(w: QWidget, name: str, value) -> None:
    """Set a dynamic property and re-apply the stylesheet rules that select on it."""
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    if wrap:
        lab.setWordWrap(True)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def button(text: str, on_click: Callable[[], None] | None = None, kind: str | None = None, small: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))  # a single & would underline the next letter
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    if small:
        b.setProperty("size", "small")
    if tooltip:
        b.setToolTip(tooltip)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0), stretch_end: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    if stretch_end:
        lay.addStretch(1)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it) -> None:
    if it is None:
        return
    if isinstance(it, int):
        lay.addStretch(it)
    elif isinstance(it, QLayout):
        lay.addLayout(it)
    else:
        lay.addWidget(it)


def clear_layout(lay: QLayout) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


class Card(QFrame):
    def __init__(self, title: str = "", meta: str = "", accent: str | None = None) -> None:
        super().__init__()
        self.setProperty("card", True)
        if accent:
            self.setProperty("accent", accent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(10)
        self.title = label(title.upper(), "h3")
        self.meta = label(meta, "note")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.head = hbox(self.title, 1, self.meta)
        if title or meta:
            self.body.addLayout(self.head)

    def add(self, *items) -> None:
        for it in items:
            _add(self.body, it)


class Stat(QFrame):
    def __init__(self, key: str, value: str = "", inner: bool = False) -> None:
        super().__init__()
        self.setProperty("stat", "inner" if inner else True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.k = label(key.upper(), "k")
        self.v = label(value, "v")
        self.v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.k)
        lay.addWidget(self.v)

    def set(self, value: str, key: str | None = None) -> None:
        if self.v.text() != value:
            self.v.setText(value)
        if key is not None and self.k.text() != key.upper():
            self.k.setText(key.upper())


class StatGrid(QWidget):
    """A grid of key/value tiles that reflows to the available width."""

    def __init__(self, min_width: int = 180, inner: bool = False) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.min_width = min_width
        self.inner = inner
        self.tiles: dict[str, Stat] = {}
        self._cols = 0

    def set(self, key: str, value: str) -> None:
        t = self.tiles.get(key)
        if t is None:
            t = self.tiles[key] = Stat(key, value, self.inner)
            self._reflow(force=True)
        t.set(value)

    def set_all(self, items: Sequence[tuple[str, str]]) -> None:
        keys = [k for k, _ in items]
        if keys != list(self.tiles):
            for t in self.tiles.values():
                t.deleteLater()
            self.tiles = {k: Stat(k, v, self.inner) for k, v in items}
            self._reflow(force=True)
        for k, v in items:
            self.tiles[k].set(v)

    def _reflow(self, force: bool = False) -> None:
        cols = max(1, self.width() // self.min_width) if self.width() > 50 else 5
        if cols == self._cols and not force:
            return
        self._cols = cols
        for i, t in enumerate(self.tiles.values()):
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reflow()


class Seg(QWidget):
    """Segmented buttons: one choice of several."""

    changed = Signal(object)

    def __init__(self, options: Sequence[tuple[object, str]], value=None) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.values = [v for v, _ in options]
        for i, (_, text) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.idClicked.connect(lambda i: self.changed.emit(self.values[i]))
        self.set_value(value)

    def value(self):
        i = self.group.checkedId()
        return self.values[i] if i >= 0 else None

    def set_value(self, v) -> None:
        if v in self.values:
            b = self.group.button(self.values.index(v))
            if not b.isChecked():
                b.setChecked(True)


class NumberEdit(QLineEdit):
    """A number box that only commits when you press Enter or leave it, so
    half-typed values never reach the engine. Out-of-range values are clamped."""

    committed = Signal(float)
    #: emitted when a box that may be empty (``allow_blank``) is emptied
    cleared = Signal()

    def __init__(self, value: float | None = 0, lo: float = -math.inf, hi: float = math.inf, step: float = 1, width: int = 110, allow_blank: bool = False) -> None:
        super().__init__()
        self.lo, self.hi = lo, hi
        self.allow_blank = allow_blank
        self._value = value
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedWidth(width)
        self.editingFinished.connect(self._commit)
        self.set(value)

    @staticmethod
    def _text(v: float | None) -> str:
        return "" if v is None or not math.isfinite(v) else f"{v:.8g}"

    def value(self) -> float | None:
        return self._value

    def set(self, v: float | None) -> None:
        """Show a value without emitting (ignored while the user is typing)."""
        if self.hasFocus():
            return
        self._value = v
        if self.text() != self._text(v):
            self.setText(self._text(v))

    def _commit(self) -> None:
        t = self.text().strip()
        try:
            n = float(t)
        except ValueError:
            n = math.nan
        if not math.isfinite(n):
            if self.allow_blank and not t:
                if self._value is not None:
                    self._value = None
                    self.cleared.emit()
                return
            self.setText(self._text(self._value))
            return
        n = min(self.hi, max(self.lo, n))
        self.setText(self._text(n))
        if n != self._value:
            self._value = n
            self.committed.emit(n)


class Form(QWidget):
    """Rows of label · control · unit, each with an optional hint underneath."""

    def __init__(self) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(2)
        self.grid.setColumnStretch(0, 1)
        self._row = 0
        self.rows: dict[str, list[QWidget]] = {}

    def add(self, key: str, text: str, control: QWidget | QLayout, unit: str | None = None, hint: str | None = None) -> None:
        widgets: list[QWidget] = []
        lab = label(text)
        lab.setWordWrap(True)
        self.grid.addWidget(lab, self._row, 0)
        widgets.append(lab)
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(6)
        h.addStretch(1)
        _add(h, control)
        if unit is not None:
            u = label(unit, "muted")
            u.setMinimumWidth(48)
            h.addWidget(u)
        self.grid.addWidget(holder, self._row, 1)
        widgets.append(holder)
        self._row += 1
        if hint:
            hl = label(hint, "note", wrap=True)
            self.grid.addWidget(hl, self._row, 0, 1, 2)
            widgets.append(hl)
            self._row += 1
        line = QFrame()
        line.setFixedHeight(1)
        line.setProperty("rule", True)
        self.grid.addWidget(line, self._row, 0, 1, 2)
        widgets.append(line)
        self._row += 1
        self.rows[key] = widgets

    def show_row(self, key: str, on: bool) -> None:
        for w in self.rows.get(key, []):
            w.setVisible(on)


def number_field(form: Form, key: str, text: str, value: float, on_commit: Callable[[float], None], unit: str | None = None, hint: str | None = None, lo=-1e9, hi=1e9, step=1.0) -> NumberEdit:
    ne = NumberEdit(value, lo, hi, step)
    ne.committed.connect(on_commit)
    form.add(key, text, ne, unit, hint)
    return ne


def combo(options: Sequence[tuple[object, str]], value, on_change: Callable[[object], None], width: int = 220) -> QComboBox:
    c = QComboBox()
    c.setFixedWidth(width)
    for v, text in options:
        c.addItem(text, v)
    set_combo(c, value)
    c.activated.connect(lambda i: on_change(c.itemData(i)))
    return c


def set_combo(c: QComboBox, value) -> None:
    for i in range(c.count()):
        if c.itemData(i) == value:
            if c.currentIndex() != i:
                c.setCurrentIndex(i)
            return


def toggle(checked: bool, on_change: Callable[[bool], None]) -> QCheckBox:
    cb = QCheckBox()
    cb.setChecked(checked)
    cb.toggled.connect(on_change)
    return cb


def scroll_page(inner: QWidget, max_width: int | None = None) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    if max_width:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        inner.setMaximumWidth(max_width)
        lay.addWidget(inner)
        lay.addStretch(1)
        sa.setWidget(wrap)
    else:
        sa.setWidget(inner)
    return sa


class Toast(QFrame):
    """A short message at the bottom of the window that disappears by itself."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.text = label("")
        self.text.setWordWrap(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.addWidget(self.text)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()
        parent.installEventFilter(self)

    def show_message(self, msg: str, ms: int = 3500) -> None:
        self.text.setText(msg)
        self._place()
        self.show()
        self.raise_()
        self.timer.start(ms)

    def _place(self) -> None:
        p = self.parentWidget()
        self.setMaximumWidth(min(760, p.width() - 40))
        self.adjustSize()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 90)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return w
