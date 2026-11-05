"""Session library: a folder of session CSV files plus a small index (index.json).

Every recording is appended to its file about once a second while it runs (so a crash
loses at most a second), and imported files are copied into the same folder. The index
keeps each session's status and summary; the CSV files are the data and can be opened
directly in Excel, pandas, MATLAB or R.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .session_file import ParsedSession, SessionParser, summarize

STATUS_LABELS = {"recording": "Recording", "complete": "Recorded", "interrupted": "Recovered", "imported": "Imported"}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return f"{now_ms():x}-{secrets.token_hex(3)}"


@dataclass
class SessionRecord:
    id: str
    file_name: str
    study_code: str
    started_at: float
    ended_at: float | None
    #: 'recording' | 'complete' | 'interrupted' | 'imported'
    status: str
    source: str
    bytes: int = 0
    last_write_at: float = 0
    stats: dict = field(default_factory=dict)


class SessionLibrary:
    INDEX = "index.json"

    def __init__(self, folder: Path, clock=now_ms) -> None:
        self.folder = Path(folder)
        self.clock = clock
        self._recs: dict[str, SessionRecord] | None = None

    # ---------- index ----------

    def _load(self) -> dict[str, SessionRecord]:
        if self._recs is None:
            self._recs = {}
            try:
                with open(self.folder / self.INDEX, encoding="utf-8") as f:
                    for d in json.load(f):
                        try:
                            r = SessionRecord(**d)
                            self._recs[r.id] = r
                        except TypeError:
                            continue
            except (OSError, ValueError):
                pass
        return self._recs

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / (self.INDEX + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in self._load().values()], f, indent=1)
        os.replace(tmp, self.folder / self.INDEX)

    def path(self, rec: SessionRecord) -> Path:
        return self.folder / rec.file_name

    def list(self) -> list[SessionRecord]:
        """Sessions whose files still exist, newest first."""
        out = [r for r in self._load().values() if r.status == "recording" or self.path(r).exists()]
        return sorted(out, key=lambda r: r.started_at, reverse=True)

    def get(self, sid: str) -> SessionRecord | None:
        return self._load().get(sid)

    def put(self, rec: SessionRecord) -> None:
        self._load()[rec.id] = rec
        self._save()

    def _unique_name(self, file_name: str) -> str:
        p = Path(file_name)
        name, k = p.name, 2
        taken = {r.file_name for r in self._load().values()}
        while name in taken or (self.folder / name).exists():
            name = f"{p.stem}-{k}{p.suffix}"
            k += 1
        return name

    # ---------- writing ----------

    def create(self, file_name: str, study_code: str, started_at: float, source: str, status: str = "recording") -> SessionRecord:
        """Reserve a file for a new session. Raises OSError if the folder is not writable."""
        self.folder.mkdir(parents=True, exist_ok=True)
        rec = SessionRecord(new_id(), self._unique_name(file_name), study_code, started_at, None, status, source, 0, self.clock(), {})
        self.path(rec).touch()
        self.put(rec)
        return rec

    def append(self, rec: SessionRecord, text: str) -> None:
        """Append text to a session's file, flushed to disk, and update the index."""
        with open(self.path(rec), "a", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        rec.bytes += len(text.encode("utf-8"))
        rec.last_write_at = self.clock()
        self.put(rec)

    # ---------- reading ----------

    def read_chunks(self, sid: str, size: int = 1 << 20):
        rec = self.get(sid)
        if rec is None:
            return
        with open(self.path(rec), encoding="utf-8", newline="") as f:
            while chunk := f.read(size):
                yield chunk

    def text(self, sid: str) -> str:
        return "".join(self.read_chunks(sid))

    def parse(self, sid: str) -> ParsedSession:
        """Parse a stored session for review or replay."""
        p = SessionParser()
        for chunk in self.read_chunks(sid):
            p.push(chunk)
        return p.finish()

    def delete(self, sid: str) -> None:
        rec = self._load().pop(sid, None)
        if rec is not None:
            try:
                self.path(rec).unlink()
            except OSError:
                pass
            self._save()

    def import_file(self, src, file_name: str | None = None) -> tuple[SessionRecord, ParsedSession]:
        """Copy a session file (v1 or v2) into the library. It is validated while it is
        read; a file that is not a session is rejected and nothing is kept."""
        src = Path(src)
        self.folder.mkdir(parents=True, exist_ok=True)
        in_place = src.resolve().parent == self.folder.resolve() and not any(r.file_name == src.name for r in self._load().values())
        name = src.name if in_place else self._unique_name(file_name or src.name)
        rec = SessionRecord(new_id(), name, "", self.clock(), None, "imported", "Imported file", 0, self.clock(), {})
        parser = SessionParser()
        dest = self.path(rec)
        try:
            with open(src, encoding="utf-8", newline="") as fin:
                if in_place:
                    while chunk := fin.read(1 << 20):
                        parser.push(chunk)
                else:
                    with open(dest, "w", encoding="utf-8", newline="") as fout:
                        while chunk := fin.read(1 << 20):
                            parser.push(chunk)
                            fout.write(chunk)
            parsed = parser.finish()
        except (OSError, UnicodeDecodeError, ValueError) as e:
            if not in_place:
                try:
                    dest.unlink()
                except OSError:
                    pass
            if isinstance(e, UnicodeDecodeError):
                raise ValueError("This is not a text file. Is it a CO2-Response session file?") from e
            raise
        summ = summarize(parsed)
        rec.study_code = parsed.study_code
        rec.started_at = parsed.started_ms or rec.started_at
        rec.ended_at = rec.started_at + parsed.duration_s * 1000
        rec.source = f"Imported · {parsed.meta.get('source', 'unknown source')}"
        rec.bytes = dest.stat().st_size
        rec.stats = summ.as_stats()
        self.put(rec)
        return rec, parsed

    def recover_interrupted(self, now: float | None = None) -> list[SessionRecord]:
        """Recordings left in 'recording' state by an app that closed or crashed.
        Anything not written to for 15 s is marked interrupted; its data is kept."""
        now = self.clock() if now is None else now
        out = []
        for r in list(self._load().values()):
            if r.status == "recording" and now - r.last_write_at > 15000:
                r.status = "interrupted"
                r.ended_at = r.last_write_at
                out.append(r)
        if out:
            self._save()
        return out

    def usage(self) -> tuple[int, int] | None:
        """(bytes used by session files, bytes free on that disk)."""
        try:
            used = sum(self.path(r).stat().st_size for r in self.list() if self.path(r).exists())
            free = shutil.disk_usage(self.folder if self.folder.exists() else self.folder.parent).free
            return used, free
        except OSError:
            return None
