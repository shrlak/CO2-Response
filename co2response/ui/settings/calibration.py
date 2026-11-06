"""Settings > Calibration: turn each channel's ADC voltage into a reading.

Type the numbers in by hand, or capture live readings against references (room air
and calibration gas, a reference flowmeter, a 3 L syringe). The preview updates as you
edit; nothing changes on the monitor until you press Save.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
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

from ...channels import DEFAULT_CHANNELS, clone_channels, validate_channels
from ...engine.monitor import MonitorEngine, Snapshot
from ...jsfmt import precision
from ...processing.calibration import (
    apply_calibration,
    fit_polynomial,
    pneumo_flow,
    syringe_k1,
    two_point_linear,
)
from ...processing.co2 import pct_to_mmhg
from ..widgets import (
    Card,
    Form,
    NumberEdit,
    Seg,
    StatGrid,
    button,
    clear_layout,
    fmt,
    hbox,
    label,
)
from .common import FlowGrid, Page, downloads_dir, save_text

NAMES = {"co2": "CO2 (capnograph)", "flow": "Flow sensor", "pneumo": "Pneumotachograph"}
ZERO_REF = {"co2": 0.04, "flow": 0.0, "pneumo": 0.0}
SPAN_DEFAULT = {
    "co2": ("Room air", 0.04, "Calibration gas", 5.0),
    "flow": ("No flow", 0.0, "Reference flow", 30.0),
    "pneumo": ("No flow (ΔP = 0)", 0.0, "Reference pressure", 200.0),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digits_for(unit: str) -> int:
    return 3 if unit == "%" else 1 if unit == "Pa" else 2


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
        self.setToolTip("Average the live voltage for 2 seconds")
        self.clicked.connect(self._go)

    def _go(self) -> None:
        self.setEnabled(False)
        self.setText("Capturing 2 s…")

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

        self.engine.capture_average(self.cid, 2, done)


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
        d = self.card.draft
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
                self.coeffs.setText(", ".join(f"{c:g}" for c in cal["coeffs"]))
        else:
            for i, (v, y) in enumerate(cal["points"]):
                self.pt_edits[i][0].set(v)
                self.pt_edits[i][1].set(y)
        self.seg.set_value(cal["type"])
        self.zero.set(d["zeroOffset"])
        if self.card.cid == "pneumo":
            fm = d.get("flowModel") or {"k1": 0.2, "k2": 0}
            self.k1.set(fm["k1"])
            self.k2.set(fm["k2"])

    def _cal(self, cal: dict) -> None:
        self.card.update_draft(calibration=cal)

    def _build(self) -> None:
        clear_layout(self.lay)
        card, cid = self.card, self.card.cid
        d = card.draft
        cal = d["calibration"]
        u = d.get("unit", "")
        f = Form()
        self.seg = Seg([("linear", "Linear"), ("polynomial", "Polynomial"), ("table", "Lookup table")], cal["type"])
        self.seg.changed.connect(self._switch_type)
        f.add("type", "Calibration type", self.seg)
        if cal["type"] == "linear":
            self.gain = NumberEdit(cal["gain"])
            self.gain.committed.connect(lambda v: self._cal({**card.draft["calibration"], "gain": v}))
            f.add("gain", "Gain", self.gain, f"{u}/V")
            self.offset = NumberEdit(cal["offset"])
            self.offset.committed.connect(lambda v: self._cal({**card.draft["calibration"], "offset": v}))
            f.add("offset", "Offset", self.offset, u, "value = gain × volts + offset")
        elif cal["type"] == "polynomial":
            self.coeffs = QLineEdit()
            self.coeffs.setFixedWidth(240)
            self.coeffs.editingFinished.connect(self._coeffs)
            f.add("coeffs", "Coefficients c0, c1, c2, …", self.coeffs, hint=f"value = c0 + c1·V + c2·V² + …  (in {u})")
        self.lay.addWidget(f)
        if cal["type"] == "table":
            t = table(["Volts", f"Value ({u})", ""])
            self.pt_edits = []
            for i, (v, y) in enumerate(cal["points"]):
                t.insertRow(i)
                ev, ey = NumberEdit(v, width=100), NumberEdit(y, width=100)
                ev.committed.connect(lambda x, i=i: self._pt(i, 0, x))
                ey.committed.connect(lambda x, i=i: self._pt(i, 1, x))
                t.setCellWidget(i, 0, ev)
                t.setCellWidget(i, 1, ey)
                acts = QWidget()
                acts.setLayout(
                    hbox(
                        CaptureButton(card.engine, cid, lambda volts, i=i: self._pt(i, 0, round(volts, 4)), "Capture V", card.toast),
                        button("Remove", lambda i=i: self._remove(i), small=True),
                        stretch_end=True,
                    )
                )
                acts.findChildren(QPushButton)[1].setEnabled(len(cal["points"]) > 2)
                t.setCellWidget(i, 2, acts)
                self.pt_edits.append((ev, ey))
            fit_table_height(t)
            self.lay.addWidget(t)
            self.lay.addLayout(
                hbox(button("Add point", self._add_point, small=True), label("Values between points are interpolated; outside the table the end segments are extended.", "note", wrap=True), 1)
            )
        g = Form()
        self.zero = NumberEdit(d["zeroOffset"])
        self.zero.committed.connect(lambda v: card.update_draft(zeroOffset=v))
        g.add("zero", "Zero offset", self.zero, u, "Subtracted after the calibration. Set it by hand or with the button below.")
        self.lay.addWidget(g)
        text = "Zero now on room air (0.04 %)" if cid == "co2" else "Zero now (no flow)"
        self.lay.addLayout(hbox(CaptureButton(card.engine, cid, self._zero_now, text, card.toast), stretch_end=True))
        if cid == "pneumo":
            h = Form()
            fm = d.get("flowModel") or {"k1": 0.2, "k2": 0}
            self.k1 = NumberEdit(fm["k1"])
            self.k1.committed.connect(lambda v: card.update_draft(flowModel={**card.flow_model(), "k1": v}))
            h.add("k1", "Flow constant k1", self.k1, "L/min per Pa")
            self.k2 = NumberEdit(fm["k2"])
            self.k2.committed.connect(lambda v: card.update_draft(flowModel={**card.flow_model(), "k2": v}))
            h.add("k2", "Quadratic term k2", self.k2, "L/min per Pa²", "flow = k1·ΔP + k2·ΔP·|ΔP|. Leave k2 at 0 for a linear (Fleisch-type) pneumotach.")
            self.lay.addWidget(h)

    def _switch_type(self, t: str) -> None:
        cal = self.card.draft["calibration"]
        if t == cal["type"]:
            return

        # convert so the curve stays the same where possible
        def y(v):
            return apply_calibration(cal, v)

        if t == "linear":
            new = {"type": "linear", "gain": precision(y(1) - y(0), 6), "offset": precision(y(0), 6)}
        elif t == "polynomial":
            new = {"type": "polynomial", "coeffs": [cal["offset"], cal["gain"]] if cal["type"] == "linear" else [precision(y(0), 6), precision(y(1) - y(0), 6)]}
        else:
            new = {"type": "table", "points": [[v, precision(y(v), 6)] for v in (0, 1.25, 2.5, 3.75, 5)]}
        self._cal(new)

    def _coeffs(self) -> None:
        try:
            c = [float(x) for x in self.coeffs.text().replace(",", " ").split()]
        except ValueError:
            c = []
        if c and all(math.isfinite(x) for x in c):
            self._cal({"type": "polynomial", "coeffs": c})
        else:
            self.card.toast("Enter numbers separated by commas, lowest order first.")
            self.coeffs.setText(", ".join(f"{x:g}" for x in self.card.draft["calibration"]["coeffs"]))

    def _pt(self, i: int, j: int, x: float) -> None:
        pts = copy.deepcopy(self.card.draft["calibration"]["points"])
        if i < len(pts):
            pts[i][j] = x
            self._cal({"type": "table", "points": pts})

    def _remove(self, i: int) -> None:
        pts = [p for k, p in enumerate(self.card.draft["calibration"]["points"]) if k != i]
        self._cal({"type": "table", "points": pts})

    def _add_point(self) -> None:
        pts = copy.deepcopy(self.card.draft["calibration"]["points"])
        pts.append([pts[-1][0] + 0.5, pts[-1][1]])
        self._cal({"type": "table", "points": pts})

    def _zero_now(self, volts: float) -> None:
        d = self.card.draft
        z = apply_calibration(d["calibration"], volts) - ZERO_REF[self.card.cid]
        self.card.toast(f"Zero offset set to {z:.{digits_for(d.get('unit', ''))}f} {d.get('unit', '')}. Save to apply.")
        self.card.update_draft(zeroOffset=precision(z, 6))


# ---------------------------------------------------------------- reference points (zero/span and multi-point fit)


class PointsCalibrator(QWidget):
    def __init__(self, card: "ChannelCard", mode: str) -> None:
        super().__init__()
        self.card, self.mode = card, mode
        cid = card.cid
        a, ra, b, rb = SPAN_DEFAULT[cid]
        nan = math.nan
        if mode == "zerospan":
            self.pts = [{"label": a, "ref": ra, "volts": nan}, {"label": b, "ref": rb, "volts": nan}]
        else:
            self.pts = [{"label": "Point 1", "ref": ra, "volts": nan}, {"label": "Point 2", "ref": rb / 2, "volts": nan}, {"label": "Point 3", "ref": rb, "volts": nan}]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if mode == "zerospan":
            if cid == "co2":
                steps = [
                    "Flow room air through the sample line, wait for the reading to settle, then capture point A.",
                    "Flow the calibration gas (e.g. 5 % CO2), wait for it to settle, then capture point B. Enter the gas's certified value.",
                ]
            else:
                ref = "flow (reference flowmeter)" if cid == "flow" else "pressure (manometer)"
                steps = ["With no flow through the sensor, capture point A.", f"Apply a known {ref} and capture point B. Enter the reference value."]
            steps.append("Press Compute line, check the preview at the top of this card, then Save.")
            lay.addWidget(label("<ol style='margin-left:-20px'>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>", "muted", wrap=True))
        else:
            lay.addWidget(
                label(
                    "Capture three or more readings across the range you will measure, then fit a line, a quadratic, or use the points as a lookup table. You can also type volts in by hand.",
                    "note",
                    wrap=True,
                )
            )
        self.table_holder = QVBoxLayout()
        lay.addLayout(self.table_holder)
        self.compute = button("Compute line", lambda: self._fit(1), kind="primary")
        self.fit1 = button("Fit line", lambda: self._fit(1), kind="primary")
        self.fit2 = button("Fit quadratic", lambda: self._fit(2))
        self.fit_t = button("Use as lookup table", lambda: self._fit("table"))
        if mode == "zerospan":
            lay.addLayout(hbox(self.compute, stretch_end=True))
        else:
            lay.addLayout(hbox(button("Add point", self._add, small=True), self.fit1, self.fit2, self.fit_t, stretch_end=True))
        self.result = label("", "ok", wrap=True)
        lay.addWidget(self.result)
        self._build_table()

    def _ready(self):
        return [p for p in self.pts if math.isfinite(p["volts"]) and math.isfinite(p["ref"])]

    def _sync_buttons(self) -> None:
        n = len(self._ready())
        self.compute.setEnabled(n >= 2)
        self.fit1.setEnabled(n >= 2)
        self.fit2.setEnabled(n >= 3)
        self.fit_t.setEnabled(n >= 2)

    def _build_table(self) -> None:
        clear_layout(self.table_holder)
        u = self.card.draft.get("unit", "")
        t = table(["Point", f"Reference ({u})", "Volts", ""])
        for i, p in enumerate(self.pts):
            t.insertRow(i)
            name = f"{'AB'[i]} · {p['label']}" if self.mode == "zerospan" else p["label"]
            t.setCellWidget(i, 0, label(name))
            er, ev = NumberEdit(p["ref"], width=100), NumberEdit(p["volts"], width=100)
            er.setAccessibleName(f"{p['label']} reference")
            ev.setAccessibleName(f"{p['label']} volts")
            er.committed.connect(lambda x, i=i: self._set(i, "ref", x))
            ev.committed.connect(lambda x, i=i: self._set(i, "volts", x))
            t.setCellWidget(i, 1, er)
            t.setCellWidget(i, 2, ev)
            acts = QWidget()
            items = [CaptureButton(self.card.engine, self.card.cid, lambda v, i=i, ev=ev: (self._set(i, "volts", round(v, 4)), ev.set(round(v, 4))), "Capture", self.card.toast)]
            if self.mode == "fit" and len(self.pts) > 2:
                items.append(button("Remove", lambda i=i: self._remove(i), small=True))
            acts.setLayout(hbox(*items, stretch_end=True))
            t.setCellWidget(i, 3, acts)
        fit_table_height(t)
        self.table_holder.addWidget(t)
        self._sync_buttons()

    def _set(self, i: int, k: str, v: float) -> None:
        # a capture finishes 2 s later and must not undo edits made meanwhile
        if i < len(self.pts):
            self.pts[i][k] = v
        self._sync_buttons()

    def _add(self) -> None:
        self.pts.append({"label": f"Point {len(self.pts) + 1}", "ref": math.nan, "volts": math.nan})
        self._build_table()

    def _remove(self, i: int) -> None:
        del self.pts[i]
        self._build_table()

    def _apply(self, cal: dict, how: str) -> None:
        self.card.update_draft(calibration=cal, zeroOffset=0)
        ready = self._ready()
        err = max(abs(apply_calibration(cal, p["volts"]) - p["ref"]) for p in ready)
        u = self.card.draft.get("unit", "")
        self.result.setText(f"{how}. Largest error at the reference points: {err:.{digits_for(u)}f} {u}. Review the preview above, then Save.")

    def _fit(self, deg) -> None:
        ready = self._ready()
        u = self.card.draft.get("unit", "")
        try:
            if deg == "table":
                self._apply({"type": "table", "points": sorted([[p["volts"], p["ref"]] for p in ready])}, f"Lookup table from {len(ready)} points")
            elif deg == 1 and len(ready) == 2:
                gain, offset = two_point_linear(ready[0]["volts"], ready[0]["ref"], ready[1]["volts"], ready[1]["ref"])
                self._apply({"type": "linear", "gain": precision(gain, 7), "offset": precision(offset, 7)}, f"Line through 2 points: gain {gain:.5g} {u}/V, offset {offset:.5g} {u}")
            else:
                c = [precision(x, 7) for x in fit_polynomial([(p["volts"], p["ref"]) for p in ready], deg)]
                if deg == 1:
                    self._apply({"type": "linear", "gain": c[1], "offset": c[0]}, f"Least-squares line through {len(ready)} points")
                else:
                    self._apply({"type": "polynomial", "coeffs": c}, f"Quadratic fit through {len(ready)} points")
        except ValueError as e:
            self.result.setText("")
            self.card.toast(str(e))


# ---------------------------------------------------------------- 3 L syringe


class SyringeCalibrator(QWidget):
    def __init__(self, card: "ChannelCard") -> None:
        super().__init__()
        self.card = card
        self._off = None
        self._acc = None
        self.res = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        steps = [
            "Zero the pneumotach with no flow (Enter values → Zero now), and save.",
            "Connect the calibration syringe with the plunger fully out.",
            "Press Start stroke, push the plunger all the way in with one smooth stroke (1–2 s), then press Stop.",
            "Check the measured volume. Apply the new k1, then push again to confirm it reads the syringe volume.",
        ]
        lay.addWidget(label("<ol style='margin-left:-20px'>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>", "muted", wrap=True))
        f = Form()
        self.volume = NumberEdit(3, 0.1, 10)
        self.volume.committed.connect(lambda _: self._show())
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

    def start(self) -> None:
        self.res = None
        idx = int(self.card.draft["index"])
        acc = self._acc = {"a1": 0.0, "a2": 0.0, "last_t": None, "last_p": 0.0, "t0": 0.0, "t_end": 0.0, "n": 0}

        def on(p) -> None:
            d = self.card.draft
            v = p.volts[idx] if idx < len(p.volts) else math.nan
            if not math.isfinite(v):
                return
            pa = apply_calibration(d["calibration"], v) - d["zeroOffset"]
            if acc["last_t"] is not None:
                dt = p.t_sec - acc["last_t"]
                acc["a1"] += (pa + acc["last_p"]) / 2 * dt
                acc["a2"] += (pa * abs(pa) + acc["last_p"] * abs(acc["last_p"])) / 2 * dt
            else:
                acc["t0"] = p.t_sec
            acc["last_t"] = acc["t_end"] = p.t_sec
            acc["last_p"] = pa
            acc["n"] += 1

        self._off = self.card.engine.on_sample(on)
        self._show()

    def stop(self) -> None:
        if self._off:
            self._off()
        self._off = None
        a = self._acc
        if not a or a["last_t"] is None:
            self.card.toast("No pneumotach data arrived. Connect the device first.")
        else:
            self.res = (a["a1"], a["a2"], a["t_end"] - a["t0"])
        self._show()

    def tick(self) -> None:
        if self._off and self._acc:
            self.live.setText(f"Integrating… ∫ΔP dt = {self._acc['a1']:.1f} Pa·s")

    def _new_k1(self):
        fm = self.card.flow_model()
        if not self.res:
            return math.nan, ""
        try:
            return syringe_k1(self.res[0], self.res[1], self.volume.value(), fm["k2"]), ""
        except ValueError as e:
            return math.nan, str(e)

    def _show(self) -> None:
        running = self._off is not None
        self.start_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.live.setVisible(running)
        self.stats.setVisible(self.res is not None)
        k1, err = self._new_k1()
        self.err.setText(err)
        self.err.setVisible(bool(err))
        ok = self.res is not None and math.isfinite(k1) and k1 > 0
        self.apply_btn.setVisible(ok)
        self.apply_btn.setText(f"Apply k1 = {k1:.5g}" if ok else "")
        if self.res:
            a1, a2, secs = self.res
            fm = self.card.flow_model()
            vol = self.volume.value()
            measured = abs(fm["k1"] * a1 + fm["k2"] * a2) / 60
            self.stats.set_all(
                [
                    ("∫ΔP dt", f"{a1:.1f} Pa·s"),
                    ("Stroke time", f"{secs:.2f} s"),
                    ("Volume with current k1", f"{fmt(measured, 3)} L ({fmt((measured - vol) / vol * 100, 1)} %)"),
                    ("New k1", f"{k1:.5g}" if math.isfinite(k1) else "--"),
                ]
            )

    def _apply_k1(self) -> None:
        k1, _ = self._new_k1()
        if math.isfinite(k1):
            self.card.update_draft(flowModel={**self.card.flow_model(), "k1": precision(k1, 6)})
            self.card.toast(f"k1 = {k1:.5g} applied to the draft. Save calibration to use it.")

    def detach(self) -> None:
        if self._off:
            self._off()
            self._off = None


# ---------------------------------------------------------------- per-channel card


class ChannelCard(Card):
    def __init__(self, engine: MonitorEngine, cid: str, toast) -> None:
        super().__init__(NAMES[cid], accent=cid)
        self.engine, self.cid, self.toast = engine, cid, toast
        self.saved_json = json.dumps(engine.channels["channels"][cid], sort_keys=True)
        self.draft = copy.deepcopy(engine.channels["channels"][cid])
        self.note = label("", "note", wrap=True)
        self.add(self.note)
        self.live = StatGrid(120, inner=True)
        self.add(self.live)

        tabs = hbox(spacing=14)
        self.group = QButtonGroup(self)
        methods = [("manual", "Enter values"), ("zerospan", "Zero & span" if cid == "co2" else "Two-point"), ("fit", "Multi-point fit")]
        if cid == "pneumo":
            methods.append(("syringe", "3 L syringe"))
        self.stack = QStackedWidget()
        self.manual = ManualEditor(self)
        self.zerospan = PointsCalibrator(self, "zerospan")
        self.fit = PointsCalibrator(self, "fit")
        self.syringe = SyringeCalibrator(self) if cid == "pneumo" else None
        editors = [self.manual, self.zerospan, self.fit] + ([self.syringe] if self.syringe else [])
        for i, ((_, text), ed) in enumerate(zip(methods, editors)):
            b = QPushButton(text.upper().replace("&", "&&"))
            b.setCheckable(True)
            b.setProperty("tab", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            tabs.addWidget(b)
            self.stack.addWidget(ed)
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

    def flow_model(self) -> dict:
        return self.draft.get("flowModel") or {"k1": 0.2, "k2": 0}

    @property
    def is_dirty(self) -> bool:
        return json.dumps(self.draft, sort_keys=True) != self.saved_json

    def update_draft(self, **patch) -> None:
        self.draft = {**self.draft, **copy.deepcopy(patch)}
        self.manual.load()
        self._sync()

    def _sync(self) -> None:
        dirty = self.is_dirty
        self.save_btn.setEnabled(dirty)
        self.discard_btn.setEnabled(dirty)
        self.dirty.setVisible(dirty)
        saved = json.loads(self.saved_json)
        when = saved.get("calibratedAt")
        try:
            when_s = f"saved {datetime.fromisoformat(when.replace('Z', '+00:00')).astimezone():%d %b %Y %H:%M}" if when else "default (not yet calibrated)"
        except ValueError:
            when_s = f"saved {when}"
        self.meta.setText(f"CH{int(self.draft['index'])} · {when_s}")
        self.note.setText(saved.get("note", "") if not when else "")
        self.note.setVisible(bool(saved.get("note")) and not when)
        if self.syringe:
            self.syringe._show()

    def save(self) -> None:
        try:
            nxt = clone_channels(self.engine.channels)
            nxt["channels"][self.cid] = {**self.draft, "calibratedAt": _utc_now()}
            self.engine.set_channels(nxt)
            self.toast(f"{NAMES[self.cid]} calibration saved.")
        except ValueError as e:
            self.toast(str(e))

    def discard(self) -> None:
        self.draft = json.loads(self.saved_json)
        self.manual.load()
        self._sync()

    def defaults(self) -> None:
        d = copy.deepcopy(DEFAULT_CHANNELS["channels"][self.cid])
        d["index"] = self.draft["index"]
        self.draft = d
        self.manual.load()
        self._sync()
        self.toast("Default values loaded. Save to apply.")

    def refresh(self, s: Snapshot) -> None:
        saved = s.channels["channels"][self.cid]
        sj = json.dumps(saved, sort_keys=True)
        if sj != self.saved_json:
            # saved here, imported or reset: the draft starts again from what was saved
            self.saved_json = sj
            self.draft = copy.deepcopy(saved)
            self.manual.load()
            self._sync()
        i = int(self.draft["index"])
        v = s.numbers.volts[i] if i < len(s.numbers.volts) else math.nan
        d = self.draft
        u = d.get("unit", "")
        dg = digits_for(u)
        value = apply_calibration(d["calibration"], v) - d["zeroOffset"] if math.isfinite(v) else math.nan
        if self.cid == "co2":
            third = ("As mmHg", f"{fmt(pct_to_mmhg(value, s.settings.processing.barometric_mm_hg, s.settings.processing.water_vapor), 1)} mmHg")
        elif self.cid == "pneumo":
            third = ("Flow", f"{fmt(pneumo_flow(self.flow_model(), value), 1)} L/min")
        else:
            sv = apply_calibration(saved["calibration"], v) - saved["zeroOffset"] if math.isfinite(v) else math.nan
            third = ("Saved calibration", f"{fmt(sv, dg)} {u}")
        self.live.set_all(
            [
                ("Raw", f"{v:.4f} V" if math.isfinite(v) else "-- V"),
                ("Preview (unsaved)" if self.is_dirty else "Calibrated", f"{fmt(value, dg)} {u}"),
                third,
            ]
        )
        if self.syringe:
            self.syringe.tick()


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
                "Each channel turns the ADC voltage into a reading. Type the numbers in by hand, or capture live readings against references (room air and calibration gas, "
                "a reference flowmeter, a 3 L syringe). The preview updates as you edit; nothing changes on the monitor until you press Save.",
                "lead",
                wrap=True,
            )
        )
        self.no_live = label("No live data. You can still enter calibrations by hand; capturing readings needs a connected device or the simulator.", "warn", wrap=True)
        self.file_cal = label("A replay is running with the calibration stored in its file. Changes saved here apply after the replay ends.", "warn", wrap=True)
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
        data = {**self.engine.channels, "exportedAt": _utc_now()}
        p = save_text(self, f"co2r-calibration-{datetime.now():%Y-%m-%d}.json", json.dumps(data, indent=2), "JSON files (*.json)")
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
            self.window.toast("All channels reset to the defaults.")

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.no_live.setVisible(s.status != "connected")
        self.file_cal.setVisible(s.using_file_calibration)
        for c in self.cards.values():
            c.refresh(s)

