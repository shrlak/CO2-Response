"""Settings > Data & recordings: record, import, list, export and delete sessions."""

from __future__ import annotations

import dataclasses
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...engine.monitor import MonitorEngine, Snapshot
from ...forms import TEST_FIELDS
from ...storage.library import STATUS_LABELS, SessionRecord
from ...storage.report import build_report, report_pdf
from ...storage.session_file import ParsedSession, breaths_csv, events_csv
from ..spec_form import SpecForm
from ..widgets import (
    Card,
    Form,
    button,
    duration,
    hbox,
    hms,
    label,
    set_prop,
    size_text,
    stamp,
    toggle,
)
from .common import FlowGrid, Page, downloads_dir, parse_then, save_bytes, save_text


def export_report(window, file_name: str, parsed: ParsedSession) -> None:
    """Save a session's CO2 response report as a PDF the user picks a place for."""
    base = file_name[:-4] if file_name.lower().endswith(".csv") else file_name
    data = report_pdf(build_report(parsed, file_name, app_version=__version__, generated_ms=time.time() * 1000))
    p = save_bytes(window, f"{base}_report.pdf", data)
    if p:
        window.toast(f"Saved {p.name}.")


def export_session(window, engine: MonitorEngine, rec: SessionRecord, what: str) -> None:
    """Save a whole session, its breaths, or its events to a file the user picks."""
    try:
        if what == "session":
            path, _ = QFileDialog.getSaveFileName(window, "Save session file", str(downloads_dir() / rec.file_name), "CSV files (*.csv)")
            if path:
                shutil.copyfile(engine.library.path(rec), path)
                window.toast(f"Saved {Path(path).name}.")
            return
    except OSError as e:
        window.toast(f"Export failed: {e}", 6000)
        return

    def ready(parsed: ParsedSession) -> None:
        try:
            if what == "report":
                export_report(window, rec.file_name, parsed)
                return
            base = rec.file_name[:-4] if rec.file_name.lower().endswith(".csv") else rec.file_name
            name = f"{base}_{'breaths' if what == 'breaths' else 'events'}.csv"
            p = save_text(window, name, breaths_csv(parsed) if what == "breaths" else events_csv(parsed))
            if p:
                window.toast(f"Saved {p.name}.")
        except (OSError, ValueError) as e:
            window.toast(f"Export failed: {e}", 6000)

    if rec.bytes > engine.library.BIG_SESSION_BYTES:
        window.toast(f"Reading {rec.file_name}…", 2500)
    # a long recording is read in steps, so the monitor keeps running meanwhile
    parse_then(window, engine.library, rec.id, ready, lambda e: window.toast(f"Export failed: {e}", 6000))


class DataPage(Page):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        self.window = window
        self.setAcceptDrops(True)
        s = engine.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(label("Data & recordings", "h2"))
        lay.addWidget(
            label(
                "Each recording is one CSV file with every sample, every breath, every alarm and every marker, ready for Excel, MATLAB, Python or R. "
                "It is written to disk every second while it records, so a crash loses at most a second. Never put a name, MRN or date of birth in the study code.",
                "lead",
                wrap=True,
            )
        )
        top = FlowGrid(430)
        rec = Card("Recording")
        f = Form()
        self.study = QLineEdit(s.study_code)
        self.study.setMaxLength(40)
        self.study.setFixedWidth(220)
        self.study.editingFinished.connect(lambda: engine.update_settings(study_code=self.study.text().strip()))
        f.add("study", "Study code", self.study, hint="Goes in the file name and header. Letters, numbers and dashes work best.")
        self.autorec = toggle(s.auto_record, lambda v: engine.update_settings(auto_record=v))
        f.add("auto", "Start recording when the Arduino connects", self.autorec, hint="Useful for overnight runs: connecting (or reconnecting after an unplug) starts a recording if none is running.")
        rec.add(f)
        self.test_form = SpecForm(TEST_FIELDS, lambda: engine.settings.test, lambda patch: engine.update_settings(test=dataclasses.replace(engine.settings.test, **patch)))
        rec.add(label("TEST AND SUBJECT", "h3"))
        rec.add(self.test_form)
        self.rec_text = label("", "muted", wrap=True)
        rec.add(self.rec_text)
        self.rec_btn = button("Start recording", window.toggle_recording, kind="primary")
        rec.add(hbox(self.rec_btn, stretch_end=True))
        top.add(rec)

        imp = Card("Import", ".csv session files, any version")
        imp.add(
            label(
                "Bring a session back to review it, replay it through the monitor, or export parts of it. Choose files or drop them on this page. "
                "Files made by older versions of this app are converted when you export them.",
                "note",
                wrap=True,
            )
        )
        imp.add(hbox(button("Import session files…", self._import_dialog, kind="primary"), stretch_end=True))
        top.add(imp)
        lay.addWidget(top)

        lst = Card("Saved sessions")
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["File", "Study", "Started", "Length", "Size", "Breaths", "Alarms", "Status", ""])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lst.add(self.table)
        self.empty = label("No sessions yet. Record one, or import a file.", "note")
        lst.add(self.empty)
        self.folder = label("", "note", wrap=True, selectable=True)
        lst.add(self.folder)
        lst.add(hbox(button("Open folder", self._open_folder, small=True), button("Change folder…", self._change_folder, small=True), stretch_end=True))
        lay.addWidget(lst)
        self.list_card = lst

        bottom = FlowGrid(430)
        live = Card("This session, without recording")
        live.add(label("The monitor keeps the breath trend since the app was opened, even when nothing is being recorded.", "note", wrap=True))
        self.trend_btn = button("Save breath trend (.csv)", self._save_trend)
        live.add(hbox(self.trend_btn, button("Save session log (.txt)", self._save_log), stretch_end=True))
        bottom.add(live)
        mk = Card("Event markers", "press M on the monitor to add one")
        self.markers = QTableWidget(0, 2)
        self.markers.setHorizontalHeaderLabels(["Time", "Label"])
        self.markers.verticalHeader().hide()
        self.markers.setShowGrid(False)
        self.markers.horizontalHeader().setStretchLastSection(True)
        self.markers.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        mk.add(self.markers)
        self.no_markers = label("No markers yet.", "note")
        mk.add(self.no_markers)
        bottom.add(mk)
        lay.addWidget(bottom)
        lay.addStretch(1)
        self._key = None
        self._marker_key = None
        self._confirm: str | None = None

    # ---------- actions

    def _import_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import session files", str(downloads_dir()), "Session files (*.csv);;All files (*)")
        self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        for p in paths:
            try:
                rec, _ = self.engine.library.import_file(p)
                self.window.toast(f"Imported {p.name}: {rec.stats.get('breaths', 0)} breaths, {duration(rec.stats.get('duration_s', 0) * 1000)}.", 5000)
            except (OSError, ValueError) as e:
                self.window.toast(f"Could not import {p.name}: {e}", 7000)
        if paths:
            self.engine.library_changed()

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        self.import_files([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])

    def _open_folder(self) -> None:
        folder = self.engine.library.folder
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _change_folder(self) -> None:
        if self.engine.recorder.active:
            self.window.toast("Stop the recording before changing the sessions folder.")
            return
        d = QFileDialog.getExistingDirectory(self, "Folder for session files", str(self.engine.library.folder))
        if d:
            self.engine.update_settings(sessions_dir=d)
            self.window.toast(f"Sessions are now saved in {d}.")

    def _save_trend(self) -> None:
        p = save_text(self, self.engine.next_file_name().replace(".csv", "_live-breaths.csv"), self.engine.trend_csv())
        if p:
            self.window.toast(f"Saved {p.name}.")

    def _save_log(self) -> None:
        p = save_text(self, self.engine.next_file_name().replace(".csv", "_log.txt"), self.engine.log_text(), "Text files (*.txt)")
        if p:
            self.window.toast(f"Saved {p.name}.")

    def _delete(self, rec: SessionRecord) -> None:
        if self._confirm != rec.id:
            self._confirm = rec.id
            self._key = None
            return
        self.engine.library.delete(rec.id)
        self._confirm = None
        self.engine.library_changed()
        self.window.toast(f"Deleted {rec.file_name}.")

    def _export_menu(self, rec: SessionRecord, anchor: QWidget) -> None:
        m = QMenu(self)
        m.addAction("Whole session (one .csv)", lambda: export_session(self.window, self.engine, rec, "session"))
        m.addAction("Breaths only (.csv)", lambda: export_session(self.window, self.engine, rec, "breaths"))
        m.addAction("Alarms & events only (.csv)", lambda: export_session(self.window, self.engine, rec, "events"))
        m.addAction("CO2 response report (.pdf)", lambda: export_session(self.window, self.engine, rec, "report"))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---------- refresh

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        r = s.recording
        if not self.study.hasFocus() and self.study.text() != s.settings.study_code:
            self.study.setText(s.settings.study_code)
        self.study.setEnabled(not r.active)
        self.test_form.refresh(s.settings.test, s)
        self.autorec.blockSignals(True)
        self.autorec.setChecked(s.settings.auto_record)
        self.autorec.blockSignals(False)
        self.list_card.meta.setText("")
        if r.active:
            err = f" Writing to disk failed ({r.write_error}); the rest is kept in memory and saved when you stop." if r.write_error else ""
            self.rec_text.setText(f"Recording <b>{r.file_name}</b> for <b>{duration(s.now - r.started_at)}</b>, {size_text(r.bytes)} so far{', written to disk every second' if r.stored else ''}.{err}")
        else:
            self.rec_text.setText(f"Next file: {self.engine.next_file_name()}. Press Record on the monitor or Start here; stopping finishes the file in the sessions folder.")
        self.rec_btn.setText("Stop and save file" if r.active else "Start recording")
        set_prop(self.rec_btn, "kind", "danger" if r.active else "primary")
        self.trend_btn.setEnabled(bool(self.engine.trend))
        self.folder.setText(f"Session files are kept in {r.folder}")

        key = (r.library_version, int(s.now // 2000) if r.active else 0, self._confirm)
        if key != self._key or force:
            self._key = key
            self._fill_sessions(s)
        mk = (s.trend_version, len(self.engine.markers))
        if mk != self._marker_key or force:
            self._marker_key = mk
            self._fill_markers()

    def _fill_sessions(self, s: Snapshot) -> None:
        lib = self.engine.library
        try:
            recs = lib.list()
        except OSError:
            recs = []
        u = lib.usage()
        self.list_card.meta.setText(f"{size_text(u[0])} used · {size_text(u[1])} free on this disk" if u else "")
        self.table.setRowCount(len(recs))
        self.table.setVisible(bool(recs))
        self.empty.setVisible(not recs)
        for i, rec in enumerate(recs):
            recording = rec.status == "recording"
            if recording and s.recording.active:
                length = s.now - rec.started_at
            else:
                d = rec.stats.get("duration_s")
                length = d * 1000 if d is not None else (rec.ended_at or rec.last_write_at) - rec.started_at
            cells = [rec.file_name, rec.study_code or "—", stamp(rec.started_at), duration(length), size_text(rec.bytes), str(rec.stats.get("breaths", "—")), str(rec.stats.get("alarms", "—"))]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(text))
            chip = label(STATUS_LABELS.get(rec.status, rec.status))
            chip.setProperty("chip", rec.status)
            holder = QWidget()
            holder.setLayout(hbox(chip, stretch_end=True, margins=(4, 0, 4, 0)))
            self.table.setCellWidget(i, 7, holder)
            acts = QWidget()
            exp = button("Export ▾", small=True)
            exp.clicked.connect(lambda _=False, rec=rec, b=exp: self._export_menu(rec, b))
            items = [button("Open", lambda rec=rec: self.window.open_review(rec.id), small=True), exp]
            if self._confirm == rec.id:
                items += [button("Confirm delete", lambda rec=rec: self._delete(rec), kind="danger", small=True), button("Keep", self._keep, small=True)]
            else:
                d = button("Delete", lambda rec=rec: self._delete(rec), small=True)
                d.setEnabled(not (recording and s.recording.active))
                items.append(d)
            acts.setLayout(hbox(*items, spacing=6, margins=(4, 2, 4, 2), stretch_end=True))
            self.table.setCellWidget(i, 8, acts)
            self.table.setRowHeight(i, 38)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(7, max(self.table.columnWidth(7), 110))
        h = self.table.horizontalHeader().height() + 38 * len(recs) + 6
        self.table.setFixedHeight(min(h, 420))

    def _keep(self) -> None:
        self._confirm = None
        self._key = None

    def _fill_markers(self) -> None:
        ms = self.engine.markers
        self.markers.setRowCount(len(ms))
        self.markers.setVisible(bool(ms))
        self.no_markers.setVisible(not ms)
        for i, m in enumerate(ms):
            self.markers.setItem(i, 0, QTableWidgetItem(hms(m.t)))
            ed = QLineEdit(m.label)
            ed.setAccessibleName(f"Marker {m.id} label")
            ed.editingFinished.connect(lambda m=m, ed=ed: self.engine.rename_marker(m.id, ed.text()))
            self.markers.setCellWidget(i, 1, ed)
        self.markers.setFixedHeight(self.markers.horizontalHeader().height() + 34 * len(ms) + 6)


