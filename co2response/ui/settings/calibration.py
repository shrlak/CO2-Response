"""Settings > Calibration: turn each channel's ADC voltage into a reading.

Type the numbers in by hand, or capture live readings against references (room air
and calibration gas, a reference flowmeter, a 3 L syringe). The preview updates as you
edit; nothing changes on the monitor until you press Save.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ... import calibration_draft as cd
from ...calibration_draft import CalDraft
from ...channels import validate_channels
from ...engine.monitor import MonitorEngine, Snapshot
from ..widgets import (
    Card,
    Form,
    NumberEdit,
    Seg,
    StatGrid,
    button,
    clear_layout,
    hbox,
    label,
)
from .common import FlowGrid, Page, downloads_dir, save_text


def steps_html(steps: list[str]) -> str:
    return "<ol style='margin-left:-20px'>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>"


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
    return t


def fit_table_height(t: QTableWidget) -> None:
    t.resizeColumnsToContents()
    h = t.horizontalHeader().height() + sum(t.rowHeight(r) for r in range(t.rowCount())) + 4
    t.setFixedHeight(h)


class CaptureButton(QPushButton):
    """Averages the channel's live voltage for 2 s."""

    def __init__(self, engine: MonitorEngine, cid: str, on_volts: Callable[[float], None], text: str = "Capture", toast=None) -> None:
        super().__init__(text)
        self.engine, self.cid, self.on_volts, self.text0, self.toast = engine, cid, on_volts, text, toast
        self.setProperty("size", "small")
        self.setToolTip(cd.CAPTURE_TIP)
        self.clicked.connect(self._go)

    def _go(self) -> None:
        self.setEnabled(False)
        self.setText(cd.CAPTURING)

        def done(r, err) -> None:
            try:
                self.setEnabled(True)
                self.setText(self.text0)
            except RuntimeError:
                return  # the button was rebuilt meanwhile
            if err:
                if self.toast:
                    self.toast(err)
            else:
                self.on_volts(r.volts)

        self.engine.capture_average(self.cid, cd.CAPTURE_SEC, done)


# ---------------------------------------------------------------- manual entry


class ManualEditor(QWidget):
    def __init__(self, card: "ChannelCard") -> None:
        super().__init__()
        self.card = card
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self._shape = None
        self.load()

    def _shape_of(self, d: dict):
        cal = d["calibration"]
        return (cal["type"], len(cal.get("points", [])))

    def load(self) -> None:
        """Show the draft. Rebuilds the fields only when their layout has to change."""
        m = self.card.model
        d = m.draft
        shape = self._shape_of(d)
        if shape != self._shape:
            self._shape = shape
            self._build()
        cal = d["calibration"]
        if cal["type"] == "linear":
            self.gain.set(cal["gain"])
            self.offset.set(cal["offset"])
        elif cal["type"] == "polynomial":
            if not self.coeffs.hasFocus():
                self.coeffs.setText(m.coeffs_text())
        else:
            for i, (v, y) in enumerate(cal["points"]):
                self.pt_edits[i][0].set(v)
                self.pt_edits[i][1].set(y)
        self.seg.set_value(cal["type"])
        self.zero.set(d["zeroOffset"])
        if self.card.cid == "pneumo":
            fm = m.flow_model()
            self.k1.set(fm["k1"])
            self.k2.set(fm["k2"])

    def _build(self) -> None:
        clear_layout(self.lay)
        card, cid = self.card, self.card.cid
        m = card.model
        edit = card.edit
        d = m.draft
        cal = d["calibration"]
        u = m.unit
        f = Form()
        self.seg = Seg(list(cd.TYPES), cal["type"])
        self.seg.changed.connect(lambda t: edit(m.switch_type, t))
        f.add("type", "Calibration type", self.seg)
        if cal["type"] == "linear":
            self.gain = NumberEdit(cal["gain"])
            self.gain.committed.connect(lambda v: edit(m.set_number, "gain", v))
            f.add("gain", "Gain", self.gain, f"{u}/V")
            self.offset = NumberEdit(cal["offset"])
            self.offset.committed.connect(lambda v: edit(m.set_number, "offset", v))
            f.add("offset", "Offset", self.offset, u, cd.LINEAR_HINT)
        elif cal["type"] == "polynomial":
            self.coeffs = QLineEdit()
            self.coeffs.setFixedWidth(240)
            self.coeffs.editingFinished.connect(self._coeffs)
            f.add("coeffs", cd.COEFFS_LABEL, self.coeffs, hint=f"value = c0 + c1·V + c2·V² + …  (in {u})")
        self.lay.addWidget(f)
        if cal["type"] == "table":
            t = table(["Volts", f"Value ({u})", ""])
            self.pt_edits = []
            for i, (v, y) in enumerate(cal["points"]):
                t.insertRow(i)
                ev, ey = NumberEdit(v, width=100), NumberEdit(y, width=100)
                ev.committed.connect(lambda x, i=i: edit(m.set_point, i, 0, x))
                ey.committed.connect(lambda x, i=i: edit(m.set_point, i, 1, x))
                t.setCellWidget(i, 0, ev)
                t.setCellWidget(i, 1, ey)
                acts = QWidget()
                remove = button("Remove", lambda i=i: edit(m.remove_point, i), small=True)
                remove.setEnabled(m.can_remove_point())
                acts.setLayout(hbox(CaptureButton(card.engine, cid, lambda volts, i=i: edit(m.capture_point, i, volts), "Capture V", card.toast), remove, stretch_end=True))
                t.setCellWidget(i, 2, acts)
                self.pt_edits.append((ev, ey))
            fit_table_height(t)
            self.lay.addWidget(t)
            self.lay.addLayout(hbox(button("Add point", lambda: edit(m.add_point), small=True), label(cd.TABLE_NOTE, "note", wrap=True), 1))
        g = Form()
        self.zero = NumberEdit(d["zeroOffset"])
        self.zero.committed.connect(lambda v: edit(m.set_zero, v))
        g.add("zero", "Zero offset", self.zero, u, cd.ZERO_HINT)
        self.lay.addWidget(g)
        self.lay.addLayout(hbox(CaptureButton(card.engine, cid, lambda volts: edit(m.zero_now, volts), cd.zero_button(cid), card.toast), stretch_end=True))
        if cid == "pneumo":
            h = Form()
            fm = m.flow_model()
            self.k1 = NumberEdit(fm["k1"])
            self.k1.committed.connect(lambda v: edit(m.set_flow, "k1", v))
            h.add("k1", "Flow constant k1", self.k1, "L/min per Pa")
            self.k2 = NumberEdit(fm["k2"])
            self.k2.committed.connect(lambda v: edit(m.set_flow, "k2", v))
            h.add("k2", "Quadratic term k2", self.k2, "L/min per Pa²", cd.K2_HINT)
            self.lay.addWidget(h)

    def _coeffs(self) -> None:
        self.card.edit(self.card.model.set_coeffs, self.coeffs.text())
        self.coeffs.setText(self.card.model.coeffs_text())  # what was typed, tidied; or the old ones back


# ---------------------------------------------------------------- reference points (zero/span and multi-point fit)


class PointsCalibrator(QWidget):
    def __init__(self, card: "ChannelCard", mode: str) -> None:
        super().__init__()
        self.card, self.mode = card, mode
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if mode == "zerospan":
            lay.addWidget(label(steps_html(cd.zerospan_steps(card.cid)), "muted", wrap=True))
        else:
            lay.addWidget(label(cd.FIT_INTRO, "note", wrap=True))
        self.table_holder = QVBoxLayout()
        lay.addLayout(self.table_holder)
        self.fit_buttons = []
        for k, (deg, text, _) in enumerate(card.model.fits(mode)):
            self.fit_buttons.append(button(text, lambda deg=deg: self._fit(deg), kind="primary" if k == 0 else ""))
        self.compute = self.fit_buttons[0]  # "Compute line" (zero and span) or "Fit line"
        add = [button("Add point", self._add, small=True)] if mode == "fit" else []
        lay.addLayout(hbox(*add, *self.fit_buttons, stretch_end=True))
        self.result = label("", "ok", wrap=True)
        lay.addWidget(self.result)
        self._build_table()

    @property
    def pts(self) -> list[dict]:
        return self.card.model.pts[self.mode]

    def _sync_buttons(self) -> None:
        for b, (_, _, ok) in zip(self.fit_buttons, self.card.model.fits(self.mode)):
            b.setEnabled(ok)

    def _build_table(self) -> None:
        clear_layout(self.table_holder)
        m = self.card.model
        t = table(["Point", f"Reference ({m.unit})", "Volts", ""])
        for i, p in enumerate(self.pts):
            t.insertRow(i)
            t.setCellWidget(i, 0, label(m.point_name(self.mode, i)))
            er, ev = NumberEdit(p["ref"], width=100), NumberEdit(p["volts"], width=100)
            er.setAccessibleName(f"{p['label']} reference")
            ev.setAccessibleName(f"{p['label']} volts")
            er.committed.connect(lambda x, i=i: self._set(i, "ref", x))
            ev.committed.connect(lambda x, i=i: self._set(i, "volts", x))
            t.setCellWidget(i, 1, er)
            t.setCellWidget(i, 2, ev)
            acts = QWidget()
            items = [CaptureButton(self.card.engine, self.card.cid, lambda v, i=i, ev=ev: self._captured(i, v, ev), "Capture", self.card.toast)]
            if m.can_remove_ref(self.mode):
                items.append(button("Remove", lambda i=i: self._remove(i), small=True))
            acts.setLayout(hbox(*items, stretch_end=True))
            t.setCellWidget(i, 3, acts)
        fit_table_height(t)
        self.table_holder.addWidget(t)
        self._sync_buttons()

    def _set(self, i: int, k: str, v: float) -> None:
        self.card.model.set_ref(self.mode, i, k, v)
        self._sync_buttons()

    def _captured(self, i: int, volts: float, edit: NumberEdit) -> None:
        v = self.card.model.capture_ref(self.mode, i, volts)
        try:
            edit.set(v)
        except RuntimeError:
            pass  # the table was rebuilt during the 2 s
        self._sync_buttons()

    def _add(self) -> None:
        self.card.model.add_ref()
        self._build_table()

    def _remove(self, i: int) -> None:
        self.card.model.remove_ref(i)
        self._build_table()

    def _fit(self, deg) -> None:
        self.card.edit(self.card.model.fit, self.mode, deg, quiet=True)
        self.result.setText(self.card.model.result[self.mode])


# ---------------------------------------------------------------- 3 L syringe


class SyringeCalibrator(QWidget):
    def __init__(self, card: "ChannelCard") -> None:
        super().__init__()
        self.card = card
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label(steps_html(cd.SYRINGE_STEPS), "muted", wrap=True))
        f = Form()
        self.volume = NumberEdit(card.model.volume, 0.1, 10)
        self.volume.committed.connect(self._volume)
        f.add("vol", "Syringe volume", self.volume, "L")
        lay.addWidget(f)
        self.start_btn = button("Start stroke", self.start, kind="primary")
        self.stop_btn = button("Stop", self.stop, kind="danger")
        self.live = label("", "note")
        lay.addLayout(hbox(self.start_btn, self.stop_btn, self.live, stretch_end=True))
        self.stats = StatGrid(150, inner=True)
        lay.addWidget(self.stats)
        self.err = label("", "err", wrap=True)
        lay.addWidget(self.err)
        self.apply_btn = button("", self._apply_k1, kind="primary")
        lay.addLayout(hbox(self.apply_btn, label("Then Save calibration.", "note"), stretch_end=True))
        self._show()

    @property
    def res(self):
        return self.card.model.stroke

    def _volume(self, v: float) -> None:
        self.card.model.set_volume(v)
        self._show()

    def start(self) -> None:
        self.card.model.stroke_start(self.card.engine.on_sample)
        self._show()

    def stop(self) -> None:
        if not self.card.model.stroke_stop():
            self.card.toast(cd.NO_STROKE)
        self._show()

    def tick(self) -> None:
        text = self.card.model.stroke_live()
        if text:
            self.live.setText(text)

    def _show(self) -> None:
        m = self.card.model
        running = m.stroke_running
        self.start_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.live.setVisible(running)
        self.stats.setVisible(m.stroke is not None)
        _, err = m.new_k1()
        self.err.setText(err)
        self.err.setVisible(bool(err))
        self.apply_btn.setVisible(m.can_apply_k1())
        self.apply_btn.setText(m.apply_k1_text())
        if m.stroke:
            self.stats.set_all(m.stroke_stats())

    def _apply_k1(self) -> None:
        self.card.edit(self.card.model.apply_k1)

    def detach(self) -> None:
        self.card.model.stroke_detach()


# ---------------------------------------------------------------- per-channel card


class ChannelCard(Card):
    """One channel's card. What it does is ``CalDraft`` (shared with the browser
    page); this draws it."""

    def __init__(self, engine: MonitorEngine, cid: str, toast) -> None:
        super().__init__(cd.NAMES[cid], accent=cid)
        self.engine, self.cid, self.toast = engine, cid, toast
        self.model = CalDraft(cid, engine.channels["channels"][cid])
        self.note = label("", "note", wrap=True)
        self.add(self.note)
        self.live = StatGrid(120, inner=True)
        self.add(self.live)

        tabs = hbox(spacing=14)
        self.group = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.manual = ManualEditor(self)
        self.zerospan = PointsCalibrator(self, "zerospan")
        self.fit = PointsCalibrator(self, "fit")
        self.syringe = SyringeCalibrator(self) if cid == "pneumo" else None
        editors = {"manual": self.manual, "zerospan": self.zerospan, "fit": self.fit, "syringe": self.syringe}
        for i, (mid, text) in enumerate(cd.methods(cid)):
            b = QPushButton(text.upper().replace("&", "&&"))
            b.setCheckable(True)
            b.setProperty("tab", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            tabs.addWidget(b)
            self.stack.addWidget(editors[mid])
        tabs.addStretch(1)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self._method)
        self.add(tabs)
        self.add(self.stack)
        self._fit_stack()

        self.save_btn = button("Save calibration", self.save, kind="primary")
        self.discard_btn = button("Discard changes", self.discard)
        self.dirty = label("Unsaved changes", "dirty")
        rule = QWidget()
        rule.setFixedHeight(1)
        self.add(rule)
        self.add(hbox(self.save_btn, self.discard_btn, button("Load defaults", self.defaults), self.dirty, stretch_end=True))
        self._sync()

    def _method(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._fit_stack()

    def _fit_stack(self) -> None:
        # the stack takes the height of the page that is showing, not the tallest one
        for i in range(self.stack.count()):
            v = QSizePolicy.Policy.Preferred if i == self.stack.currentIndex() else QSizePolicy.Policy.Ignored
            self.stack.widget(i).setSizePolicy(QSizePolicy.Policy.Preferred, v)
        self.stack.adjustSize()

    @property
    def draft(self) -> dict:
        return self.model.draft

    @property
    def is_dirty(self) -> bool:
        return self.model.dirty

    def edit(self, change: Callable, *args, quiet: bool = False) -> None:
        """Make a change to the draft and show it. What the change has to say (a
        message it returns, or the reason it refuses) goes to the operator;
        ``quiet`` keeps a returned message off the screen (it is shown elsewhere)."""
        try:
            said = change(*args)
        except ValueError as e:
            said, quiet = str(e), False
        if isinstance(said, str) and said and not quiet:
            self.toast(said)
        self.manual.load()
        self._sync()

    def _sync(self) -> None:
        dirty = self.is_dirty
        self.save_btn.setEnabled(dirty)
        self.discard_btn.setEnabled(dirty)
        self.dirty.setVisible(dirty)
        self.meta.setText(self.model.meta(_when))
        note = self.model.note()
        self.note.setText(note)
        self.note.setVisible(bool(note))
        if self.syringe:
            self.syringe._show()

    def save(self) -> None:
        self.edit(self.model.save, self.engine)

    def discard(self) -> None:
        self.edit(self.model.discard)

    def defaults(self) -> None:
        self.edit(self.model.defaults)

    def refresh(self, s: Snapshot) -> None:
        if self.model.follow(s.channels["channels"][self.cid]):
            self.manual.load()
            self._sync()
        self.live.set_all(self.model.live(s))
        if self.syringe:
            self.syringe.tick()


def _when(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone():%d %b %Y %H:%M}"
    except ValueError:
        return iso


class CalibrationPage(Page):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        self.window = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Calibration", "h2"))
        lay.addWidget(
            label(
                cd.LEAD,
                "lead",
                wrap=True,
            )
        )
        self.no_live = label(cd.NO_LIVE, "warn", wrap=True)
        self.file_cal = label(cd.FILE_CAL, "warn", wrap=True)
        lay.addWidget(self.no_live)
        lay.addWidget(self.file_cal)
        lay.addLayout(
            hbox(
                button("Export calibration (JSON)", self._export),
                button("Import calibration…", self._import),
                button("Reset all to defaults", self._reset, kind="danger"),
                label("Saved on this computer. Export a copy for your lab notebook.", "note"),
                stretch_end=True,
            )
        )
        grid = FlowGrid(470)
        self.cards = {cid: ChannelCard(engine, cid, window.toast) for cid in ("co2", "flow", "pneumo")}
        for c in self.cards.values():
            grid.add(c)
        lay.addWidget(grid)
        lay.addStretch(1)

    def _export(self) -> None:
        p = save_text(self, cd.export_name(), cd.export_text(self.engine.channels), "JSON files (*.json)")
        if p:
            self.window.toast(f"Saved {p}.")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import calibration", str(downloads_dir()), "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            self.engine.set_channels(validate_channels(json.loads(Path(path).read_text(encoding="utf-8"))))
            self.window.toast(f"Imported {Path(path).name}.")
        except (OSError, ValueError) as e:
            self.window.toast(f"Could not import {Path(path).name}: {e}", 6000)

    def _reset(self) -> None:
        ans = QMessageBox.question(self, "Reset calibration?", "Replace every channel's calibration with the defaults from co2response/config/channels.json?")
        if ans == QMessageBox.StandardButton.Yes:
            self.engine.reset_channels()
            self.window.toast(cd.RESET_DONE)

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.no_live.setVisible(s.status != "connected")
        self.file_cal.setVisible(s.using_file_calibration)
        for c in self.cards.values():
            c.refresh(s)

