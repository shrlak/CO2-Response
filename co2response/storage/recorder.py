"""Writes one session file while recording.

Rows are buffered and appended to the file in the session library about once a second
(call ``poll`` regularly), so the recording survives a crash. If the folder cannot be
written, the file is kept in memory and handed back on ``stop``.
"""

from __future__ import annotations

import dataclasses
import math
import time
from datetime import datetime
from typing import Callable

from .library import SessionLibrary, SessionRecord
from .session_file import (
    BreathCells,
    SessionMeta,
    format_header,
    format_row,
    session_file_name,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class SessionRecorder:
    def __init__(self, library: SessionLibrary, clock: Callable[[], float] = _now_ms) -> None:
        self.library = library
        self.clock = clock
        self.active = False
        self.started_at = 0.0
        self.file_name = ""
        self.rows = self.breaths = self.alarms = self.markers = 0
        self.rec: SessionRecord | None = None
        self.write_error: str | None = None
        self._memory: list[str] | None = None
        self._buf: list[str] = []
        self._buf_bytes = 0
        self._last_flush = 0.0
        self._anchor: tuple[float, float] | None = None
        self._last_t = -math.inf
        self._pending_breath: BreathCells | None = None
        self._pending_events: list[str] = []
        self._pending_since = 0.0

    @property
    def bytes(self) -> int:
        mem = sum(len(s) for s in self._memory) if self._memory else 0
        return (self.rec.bytes if self.rec else 0) + self._buf_bytes + mem

    def start(self, meta: SessionMeta, started_ms: float | None = None) -> SessionRecord | None:
        """Open a new session file. ``meta.started_ms`` is replaced by ``started_ms`` (default now)."""
        if self.active:
            return self.rec
        started_ms = self.clock() if started_ms is None else started_ms
        self.started_at = started_ms
        self.file_name = session_file_name(meta.study_code, datetime.fromtimestamp(started_ms / 1000))
        self.rows = self.breaths = self.alarms = self.markers = 0
        self._anchor = None
        self._last_t = -math.inf
        self._pending_breath = None
        self._pending_events = []
        self._buf = []
        self._buf_bytes = 0
        self.write_error = None
        self._memory = None
        header = format_header(dataclasses.replace(meta, started_ms=started_ms))
        try:
            self.rec = self.library.create(self.file_name, meta.study_code, started_ms, meta.source)
            self.file_name = self.rec.file_name
            self.library.append(self.rec, header)
        except OSError as e:
            # folder missing or read-only: keep the file in memory instead
            self.write_error = str(e)
            self.rec = None
            self._memory = [header]
        self.active = True
        self._last_flush = self.clock()
        return self.rec

    def _add(self, s: str) -> None:
        self._buf.append(s)
        self._buf_bytes += len(s)

    def breath(self, etco2: float, fico2: float, rr: float, vt: float, mv: float, peak: float, quality: str = "ok") -> int | None:
        """A breath completed on the sample about to be recorded. Returns its number in
        the file (None when not recording)."""
        if not self.active:
            return None
        self.breaths += 1
        self._pending_breath = BreathCells(self.breaths, etco2, fico2, rr, vt, mv, peak, quality)
        return self.breaths

    def event(self, wall_ms: float, text: str) -> None:
        if not self.active:
            return
        if text.startswith("ALARM_START") and " test " not in text:
            self.alarms += 1
        if text.startswith("MARK "):
            self.markers += 1
        if not self._pending_events:
            self._pending_since = wall_ms
        self._pending_events.append(text)

    def sample(self, wall_ms: float, t_ms: float, seq: int, volts, co2_mmhg: float, co2_pct: float, flow_lpm: float, pneumo_pa: float, pneumo_lpm: float) -> None:
        if not self.active:
            return
        if self._anchor is None or t_ms < self._last_t:
            self._anchor = (t_ms, wall_ms)  # device clock ↔ computer clock, per connection
        self._last_t = t_ms
        wall = self._anchor[1] + (t_ms - self._anchor[0])
        self._add(
            format_row(
                (wall - self.started_at) / 1000,
                wall,
                t_ms,
                seq,
                volts,
                co2_mmhg,
                co2_pct,
                flow_lpm,
                pneumo_pa,
                pneumo_lpm,
                self._pending_breath,
                self._pending_events,
            )
        )
        self.rows += 1
        self._pending_breath = None
        self._pending_events = []

    def _flush_lone_events(self, force: bool) -> None:
        """Events with no sample to ride on (e.g. while disconnected) get their own row."""
        if self._pending_events and (force or self.clock() - self._pending_since > 1000):
            t = self._pending_since
            self._add(format_row((t - self.started_at) / 1000, t, events=self._pending_events))
            self._pending_events = []

    def poll(self, now: float | None = None) -> None:
        """Write buffered rows to disk once a second."""
        now = self.clock() if now is None else now
        if self.active and now - self._last_flush >= 1000:
            self._last_flush = now
            self.flush()

    def flush(self, force: bool = False) -> None:
        self._flush_lone_events(force)
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        self._buf_bytes = 0
        if self._memory is not None:
            self._memory.append(text)
            return
        try:
            self.library.append(self.rec, text)
        except OSError as e:
            # disk full or the folder went away: keep everything from here on in memory
            self.write_error = str(e)
            self._memory = [text]

    def stop(self) -> tuple[SessionRecord | None, str | None]:
        """Finish the file. Returns the library record and, if writing to disk failed
        part-way, the whole file as text so it can be saved somewhere else."""
        if not self.active:
            return self.rec, None
        self.flush(force=True)
        self.active = False
        rec = self.rec
        if rec is not None:
            rec.status = "complete"
            rec.ended_at = self.clock()
            rec.stats = {
                "duration_s": (rec.ended_at - self.started_at) / 1000,
                "samples": self.rows,
                "breaths": self.breaths,
                "alarms": self.alarms,
                "markers": self.markers,
            }
            try:
                self.library.put(rec)
            except OSError:
                pass
        if self._memory is not None:
            head = ""
            if rec is not None:
                try:
                    head = self.library.text(rec.id)
                except OSError:
                    head = ""
            text = head + "".join(self._memory)
            self._memory = None
            return rec, text
        return rec, None
