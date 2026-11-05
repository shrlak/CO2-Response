"""Settings > Diagnostics: the hardware check, link statistics, raw lines and the log."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...engine.link_test import overall, report_text
from ...engine.monitor import HardwareCheckJob, MonitorEngine, Snapshot
from .. import theme
from ..widgets import Card, Seg, StatGrid, button, fmt, hbox, hms, label, set_prop
from .common import FlowGrid, Page, save_text

CHIP = {"pass": "PASS", "warn": "CHECK", "fail": "FAIL", "info": "NOTE"}
VERDICT = {"pass": "All checks passed.", "warn": "Usable, but check the items marked CHECK.", "fail": "Fix the items marked FAIL before using this setup."}


class HardwareCheck(Card):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__("Hardware check", "USB link, ADC and each sensor's wiring")
        self.engine, self.window = engine, window
        self.job: HardwareCheckJob | None = None
        self.shown_results = None
        self.add(
            label(
                "Run this after wiring or moving the setup, and before each study. It watches the live data and checks the handshake, sample rate, dropped and corrupt lines, "
                "and whether each channel is stuck, clipping or noisy. Leave the sensors connected and breathing through them (or the simulator running). "
                "10 minutes is the acceptance test for a new setup (target: under 0.1 % dropped).",
                "note",
                wrap=True,
            )
        )
        self.length = Seg([(10, "10 s"), (60, "1 min"), (600, "10 min")], 10)
        self.run_btn = button("Run hardware check", self.run, kind="primary")
        self.stop_btn = button("Stop early", self.stop, kind="danger")
        self.need = label("Connect the Arduino or start the simulator first.", "note")
        self.i2c = label("", "warn")
        self.add(hbox(self.length, self.run_btn, self.stop_btn, self.need, self.i2c, stretch_end=True))
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.add(self.progress)
        self.verdict = label("")
        self.add(self.verdict)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Check", "Result", "What to do"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add(self.table)
        self.save_btn = button("Save report (.txt)", self.save, small=True)
        self.add(hbox(self.save_btn, stretch_end=True))
        self._show(None)

    def run(self) -> None:
        try:
            self.job = self.engine.run_hardware_check(self.length.value())
        except RuntimeError as e:
            self.window.toast(str(e))
            return
        self.shown_results = None
        self._show(None)

    def stop(self) -> None:
        if self.job:
            self.job.cancel()

    def save(self) -> None:
        if not self.shown_results:
            return
        p = save_text(self, f"co2r_hardware-check_{datetime.now():%Y-%m-%d-%H-%M-%S}.txt", report_text(self.shown_results[0], self.shown_results[1]), "Text files (*.txt)")
        if p:
            self.window.toast(f"Saved {p.name}.")

    def _show(self, results) -> None:
        running = self.job is not None and not self.job.done
        self.run_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.progress.setVisible(running)
        has = results is not None
        self.verdict.setVisible(has)
        self.table.setVisible(has)
        self.save_btn.setVisible(has)
        if not has:
            return
        p = theme.current
        v = overall(results)
        self.verdict.setText(VERDICT[v])
        set_prop(self.verdict, "role", {"pass": "ok", "warn": "warn", "fail": "err"}[v])
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            chip = label(CHIP[r.status])
            chip.setProperty("chip", r.status)
            holder = QWidget()
            holder.setLayout(hbox(chip, margins=(4, 4, 4, 4), stretch_end=True))
            self.table.setCellWidget(i, 0, holder)
            self.table.setItem(i, 1, QTableWidgetItem(r.label))
            self.table.setItem(i, 2, QTableWidgetItem(r.result))
            fix = QTableWidgetItem(r.fix or "")
            fix.setForeground(QColor(p.muted))
            self.table.setItem(i, 3, fix)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(2, 330)
        self.table.resizeRowsToContents()
        self.table.setFixedHeight(self.table.horizontalHeader().height() + sum(self.table.rowHeight(i) for i in range(len(results))) + 6)

    def refresh(self, s: Snapshot) -> None:
        connected = s.status == "connected"
        self.run_btn.setEnabled(connected)
        self.need.setVisible(not connected and not (self.job and not self.job.done))
        self.i2c.setText(f"Device I2C scan: {s.i2c_scan}" if s.i2c_scan else "")
        self.i2c.setVisible(bool(s.i2c_scan))
        job = self.job
        if job is None:
            return
        if not job.done:
            self.progress.setValue(int(job.progress * 1000))
            self.stop_btn.setText(f"Stop early ({round(job.progress * 100)} %)")
            self._show(None)
        elif self.shown_results is None or self.shown_results[0] is not job.results:
            self.shown_results = (job.results, job.seconds)
            self._show(job.results)


class DiagnosticsPage(Page):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(label("Diagnostics", "h2"))
        lay.addWidget(label("Link quality and the raw stream, for checking wiring and firmware.", "lead", wrap=True))
        self.check = HardwareCheck(engine, window)
        lay.addWidget(self.check)
        self.stats = StatGrid(190)
        lay.addWidget(self.stats)
        grid = FlowGrid(430)
        raw = Card("Last raw lines")
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(theme.mono_font(12))
        self.raw.setMinimumHeight(320)
        raw.add(self.raw)
        grid.add(raw)
        log = Card("Session log")
        self.log = QListWidget()
        self.log.setMinimumHeight(320)
        log.add(self.log)
        grid.add(log)
        lay.addWidget(grid)
        lay.addStretch(1)
        self._log_n = -1

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.check.refresh(s)
        self.stats.set_all(
            [
                ("Samples received", str(s.received)),
                ("Dropped (sequence gaps)", f"{s.dropped} ({fmt(s.drop_rate * 100, 3)} %)"),
                ("Bad lines (checksum/format)", str(s.bad_lines)),
                ("Measured rate", f"{fmt(s.measured_rate_hz, 1)} Hz"),
                ("Device rate", f"{s.rate_hz:g} Hz"),
                ("CO2 / flow / pneumo", " / ".join("stale" if s.stale[c] else "ok" for c in ("co2", "flow", "pneumo"))),
            ]
        )
        lines = list(self.engine.recent_lines)[-25:]
        text = "\n".join(lines) if lines else "Nothing received yet."
        if self.raw.toPlainText() != text:
            self.raw.setPlainText(text)
        log = self.engine.log
        if len(log) != self._log_n or force:
            self._log_n = len(log)
            p = theme.current
            self.log.clear()
            for line in reversed(log[-200:]):
                it = QListWidgetItem(f"{hms(line.t)} · {line.text}")
                if line.level != "info":
                    it.setForeground(QColor(p.alarm_med if line.level == "alarm" else p.alarm_high))
                self.log.addItem(it)
