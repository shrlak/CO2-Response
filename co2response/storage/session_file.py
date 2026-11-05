"""Session file format v2: ONE self-contained CSV per recording, built for analysis.

    # CO2-Response session,v2            metadata lines start with '#'
    # <key>,<value>                       (value may contain commas, e.g. JSON)
    time_s,wall_time_iso,t_ms,seq,v0,…    header row
    <one row per sample>                  every row has the same columns

- Breath metrics (breath … peak_insp_lpm) are filled only on the sample row where that
  breath was completed; they are empty elsewhere.
- ``event`` holds alarms, markers, connections etc. on the sample row where they happened
  (several are joined with " | "). Events while no data was flowing get a row of their
  own with only time_s, wall_time_iso and event.
- time_s is seconds since the recording started, from the device clock (anchored to the
  computer clock at the first sample of each connection), so sample spacing is exact.
  wall_time_iso is the same instant on the computer clock.

pandas: ``pd.read_csv(path, comment='#')``     MATLAB: ``readtable(path, 'CommentStyle', '#')``
Free text never contains '#' (it is replaced), so comment='#' is safe on every row.

v1 files (the first format, with breaths as event rows) still import.
Full description: docs/data-format.md
"""

from __future__ import annotations

import json
import math
import re
from array import array
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import numpy as np

from ..channels import validate_channels
from ..jsfmt import iso_ms, js_json, js_num, js_round, naive_sum, parse_iso_ms, to_fixed
from ..protocol import SeqTracker
from ..settings import to_dict

NAN = float("nan")
FORMAT_VERSION = 2

COLUMNS = (
    "time_s",
    "wall_time_iso",
    "t_ms",
    "seq",
    "v0",
    "v1",
    "v2",
    "co2_mmHg",
    "co2_pct",
    "flow_lpm",
    "pneumo_pa",
    "pneumo_lpm",
    "breath",
    "etco2_mmHg",
    "fico2_mmHg",
    "rr_bpm",
    "vt_ml",
    "mv_lpm",
    "peak_insp_lpm",
    "event",
)


@dataclass
class SessionMeta:
    study_code: str
    started_ms: float
    app_version: str
    source: str
    firmware: str
    rate_hz: float
    channels: dict
    processing: Any
    #: None = not written
    alarm_settings: Any = None


@dataclass
class BreathCells:
    n: int
    etco2: float
    fico2: float
    rr: float
    vt: float
    mv: float
    peak: float


_NEG_ZERO = re.compile(r"-0(\.0*)?")


def num(v: float | None, digits: int) -> str:
    """A number cell: empty for missing or non-finite values, never "-0.0"."""
    if v is None or not math.isfinite(v):
        return ""
    s = to_fixed(v, digits)
    return s[1:] if _NEG_ZERO.fullmatch(s) else s


def csv_escape(s: str) -> str:
    return '"' + s.replace('"', '""') + '"' if any(c in s for c in '",\n\r') else s


def clean_text(s: str) -> str:
    """Free text in a cell: one line, no '#' (so comment='#' readers never cut a row)."""
    return re.sub(r"[\r\n]+", " ", s).replace("#", "no.").strip()


def format_header(m: SessionMeta) -> str:
    lines = [
        f"# CO2-Response session,v{FORMAT_VERSION}",
        "# research use only,not for clinical decision-making",
        f"# study_code,{clean_text(m.study_code)}",
        f"# started,{iso_ms(m.started_ms)}",
        f"# app_version,{m.app_version}",
        f"# source,{clean_text(m.source)}",
        f"# firmware,{clean_text(m.firmware)}",
        f"# rate_hz,{js_num(m.rate_hz)}",
        "# time_base,time_s = seconds since start from the device clock; wall_time_iso = same instant on the computer clock",
        "# units,volts V; co2 mmHg and %; flow L/min; pneumo_pa Pa; vt mL; mv L/min; rr breaths/min",
        f"# channels,{js_json(m.channels).replace('#', 'no.')}",
        f"# processing,{js_json(to_dict(m.processing))}",
    ]
    if m.alarm_settings is not None:
        lines.append(f"# alarm_settings,{js_json(to_dict(m.alarm_settings))}")
    lines.append(",".join(COLUMNS))
    return "\n".join(lines) + "\n"


def format_row(
    time_s: float,
    wall_ms: float,
    t_ms: float | None = None,
    seq: int | None = None,
    volts: list[float] | None = None,
    co2_mmhg: float | None = None,
    co2_pct: float | None = None,
    flow_lpm: float | None = None,
    pneumo_pa: float | None = None,
    pneumo_lpm: float | None = None,
    breath: BreathCells | None = None,
    events: list[str] | None = None,
) -> str:
    v = volts or ()
    b = breath
    ev = csv_escape(" | ".join(clean_text(e) for e in events)) if events else ""
    return (
        ",".join(
            (
                to_fixed(time_s, 3),
                iso_ms(wall_ms),
                ("" if t_ms is None else str(js_round(t_ms)) if math.isfinite(t_ms) else "NaN"),
                str(seq) if seq is not None else "",
                num(v[0] if len(v) > 0 else None, 4),
                num(v[1] if len(v) > 1 else None, 4),
                num(v[2] if len(v) > 2 else None, 4),
                num(co2_mmhg, 2),
                num(co2_pct, 3),
                num(flow_lpm, 2),
                num(pneumo_pa, 1),
                num(pneumo_lpm, 2),
                js_num(b.n) if b else "",
                num(b.etco2, 1) if b else "",
                num(b.fico2, 1) if b else "",
                num(b.rr, 1) if b else "",
                num(b.vt, 0) if b else "",
                num(b.mv, 2) if b else "",
                num(b.peak, 1) if b else "",
                ev,
            )
        )
        + "\n"
    )


def split_csv_line(line: str) -> list[str]:
    """Split one CSV line, honoring double-quoted fields."""
    out: list[str] = []
    cur: list[str] = []
    q = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if q:
            if c == '"' and i + 1 < n and line[i + 1] == '"':
                cur.append('"')
                i += 1
            elif c == '"':
                q = False
            else:
                cur.append(c)
        elif c == '"':
            q = True
        elif c == ",":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


# ------------------------------------------------------------------ parsing


@dataclass
class BreathRec:
    time_s: float
    n: float
    etco2: float
    fico2: float
    rr: float
    vt: float
    mv: float
    peak: float


@dataclass
class EventRec:
    time_s: float
    text: str


@dataclass
class ParsedSession:
    version: int
    meta: dict[str, str]
    channels: dict | None
    study_code: str
    started_ms: float
    #: number of sample rows
    n: int
    time_s: np.ndarray
    t_ms: np.ndarray
    seq: np.ndarray
    v0: np.ndarray
    v1: np.ndarray
    v2: np.ndarray
    co2_mmhg: np.ndarray
    co2_pct: np.ndarray
    flow_lpm: np.ndarray
    pneumo_pa: np.ndarray
    pneumo_lpm: np.ndarray
    breaths: list[BreathRec] = field(default_factory=list)
    events: list[EventRec] = field(default_factory=list)
    duration_s: float = 0.0


def _f(s: str) -> float:
    if not s:
        return NAN
    try:
        return float(s)
    except ValueError:
        return NAN


def _i32(x: float) -> int:
    """Int32Array semantics: NaN → 0, otherwise truncate and wrap."""
    if not math.isfinite(x):
        return 0
    v = int(x) & 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


_V1_KV = re.compile(r"(\w+)=([-\d.]+|NA)")


def _parse_v1_breath(text: str, time_s: float, n: int) -> BreathRec:
    """Numbers from a v1 "BREATH etco2_mmHg=38.2 rr=15.0 …" event."""
    kv = {k: (NAN if v == "NA" else _f(v)) for k, v in _V1_KV.findall(text)}
    return BreathRec(time_s, n, kv.get("etco2_mmHg", NAN), NAN, kv.get("rr", NAN), kv.get("vt_ml", NAN), kv.get("mv_lpm", NAN), NAN)


_F64 = ("time_s", "t_ms", "v0", "v1", "v2")
_F32 = ("co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm")


class SessionParser:
    """Streaming parser: push text in any size pieces (lines may be split across
    pieces), then call finish(). Handles v1 and v2 session files."""

    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self._rest = ""
        self._col: dict[str, int] | None = None
        self._version = 0
        self._started_ms = NAN
        self._cols = {k: array("d") for k in _F64}
        self._cols.update({k: array("f") for k in _F32})
        self._seq = array("i")
        self._breaths: list[BreathRec] = []
        self._events: list[EventRec] = []
        self._last_time = 0.0

    def push(self, text: str) -> None:
        lines = (self._rest + text).split("\n")
        self._rest = lines.pop()
        for raw in lines:
            self._line(raw[:-1] if raw.endswith("\r") else raw)

    def _header(self, raw: str) -> None:
        names = [x.strip() for x in split_csv_line(raw)]
        col = {nm: i for i, nm in enumerate(names)}
        if "t_ms" not in col or "v0" not in col:
            raise ValueError("This is not a CO2-Response session file (no t_ms / v0 columns).")
        self._col = col
        self._version = 2 if "time_s" in col else 1
        m = re.search(r"v(\d+)", self.meta.get("CO2-Response session", ""))
        if m:
            self._version = int(m.group(1))
        self._started_ms = parse_iso_ms(self.meta.get("started", ""))
        # column positions; a missing column points at an empty cell appended to every row
        self._ix = {k: col.get(k, -1) for k in COLUMNS}
        self._ncol = len(names)

    def _line(self, raw: str) -> None:
        if not raw:
            return
        if raw[0] == "#":
            body = raw[1:]
            if body[:1].isspace():
                body = body[1:]
            comma = body.find(",")
            if comma > 0:
                self.meta[body[:comma]] = body[comma + 1 :]
            return
        if self._col is None:
            self._header(raw)
            return
        ix = self._ix
        f = split_csv_line(raw) if '"' in raw else raw.split(",")
        if len(f) < self._ncol:
            f.extend([""] * (self._ncol - len(f)))
        f.append("")
        if self._version >= 2:
            t = _f(f[ix["time_s"]])
        else:
            w = parse_iso_ms(f[ix["wall_time_iso"]])
            if not math.isfinite(self._started_ms):
                self._started_ms = w
            t = (w - self._started_ms) / 1000
        if not math.isfinite(t):
            t = self._last_time
        self._last_time = t

        if f[ix["t_ms"]] != "":
            c = self._cols
            c["time_s"].append(t)
            c["t_ms"].append(_f(f[ix["t_ms"]]))
            self._seq.append(_i32(_f(f[ix["seq"]])))
            for k in ("v0", "v1", "v2", "co2_mmHg", "co2_pct", "flow_lpm", "pneumo_pa", "pneumo_lpm"):
                c[k].append(_f(f[ix[k]]))
            if f[ix["breath"]] != "":
                self._breaths.append(
                    BreathRec(
                        t,
                        _f(f[ix["breath"]]),
                        _f(f[ix["etco2_mmHg"]]),
                        _f(f[ix["fico2_mmHg"]]),
                        _f(f[ix["rr_bpm"]]),
                        _f(f[ix["vt_ml"]]),
                        _f(f[ix["mv_lpm"]]),
                        _f(f[ix["peak_insp_lpm"]]),
                    )
                )
        ev = f[ix["event"]]
        if ev:
            for text in ev.split(" | "):
                if self._version < 2 and text.startswith("BREATH "):
                    self._breaths.append(_parse_v1_breath(text, t, len(self._breaths) + 1))
                else:
                    self._events.append(EventRec(t, text))

    def finish(self) -> ParsedSession:
        if self._rest:
            self._line(self._rest)
            self._rest = ""
        if self._col is None:
            raise ValueError("This file has no data header. Is it a CO2-Response session file?")
        channels = None
        if self.meta.get("channels"):
            try:
                channels = validate_channels(json.loads(self.meta["channels"]))
            except ValueError:
                channels = None
        c = self._cols
        time_s = np.frombuffer(c["time_s"], dtype=np.float64).copy()
        last_event = self._events[-1].time_s if self._events else 0
        return ParsedSession(
            version=self._version,
            meta=self.meta,
            channels=channels,
            study_code=self.meta.get("study_code", ""),
            started_ms=self._started_ms if math.isfinite(self._started_ms) else 0,
            n=len(time_s),
            time_s=time_s,
            t_ms=np.frombuffer(c["t_ms"], dtype=np.float64).copy(),
            seq=np.frombuffer(self._seq, dtype=np.int32).copy(),
            v0=np.frombuffer(c["v0"], dtype=np.float64).copy(),
            v1=np.frombuffer(c["v1"], dtype=np.float64).copy(),
            v2=np.frombuffer(c["v2"], dtype=np.float64).copy(),
            co2_mmhg=np.frombuffer(c["co2_mmHg"], dtype=np.float32).copy(),
            co2_pct=np.frombuffer(c["co2_pct"], dtype=np.float32).copy(),
            flow_lpm=np.frombuffer(c["flow_lpm"], dtype=np.float32).copy(),
            pneumo_pa=np.frombuffer(c["pneumo_pa"], dtype=np.float32).copy(),
            pneumo_lpm=np.frombuffer(c["pneumo_lpm"], dtype=np.float32).copy(),
            breaths=self._breaths,
            events=self._events,
            duration_s=max(float(time_s[-1]) if len(time_s) else 0.0, last_event),
        )


def parse_session_text(text: str) -> ParsedSession:
    p = SessionParser()
    p.push(text)
    return p.finish()


def read_session(path, chunk_size: int = 1 << 20) -> ParsedSession:
    """Parse a session file from disk."""
    p = SessionParser()
    with open(path, encoding="utf-8", newline="") as f:
        while chunk := f.read(chunk_size):
            p.push(chunk)
    return p.finish()


# ------------------------------------------------------------------ exports


@dataclass
class Range:
    t0: float
    t1: float


def _in_range(t: float, r: Range | None) -> bool:
    return r is None or r.t0 <= t <= r.t1


def _safe_json(s: str | None):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return s


def export_session_parts(s: ParsedSession, rng: Range | None = None, app_version: str = "unknown") -> Iterable[str]:
    """Rebuild a v2 session file from parsed data (optionally only a time range).
    Also converts v1 files to v2. Yields the file in pieces."""

    def wall(t: float) -> float:
        return s.started_ms + t * 1000

    rate = _f(s.meta.get("rate_hz", ""))
    head = format_header(
        SessionMeta(
            study_code=s.study_code,
            started_ms=s.started_ms,
            app_version=app_version,
            source=s.meta.get("source", ""),
            firmware=s.meta.get("firmware", ""),
            rate_hz=rate if math.isfinite(rate) and rate else 100,
            channels=s.channels if s.channels is not None else {},
            processing=_safe_json(s.meta.get("processing")),
            alarm_settings=_safe_json(s.meta["alarm_settings"]) if s.meta.get("alarm_settings") else None,
        )
    )
    if rng is not None:
        head = re.sub(r"\n(?=time_s,)", f"\n# exported_range_s,{to_fixed(rng.t0, 3)}-{to_fixed(rng.t1, 3)}\n", head, count=1)
    if s.version < 2:
        head = re.sub(r"\n(?=time_s,)", f"\n# converted_from,v{s.version}\n", head, count=1)
    yield head

    events, breaths = s.events, s.breaths
    ei = bi = 0
    buf: list[str] = []

    def flush_events(up_to: float):
        # events before the next sample that will not attach to it get their own row
        nonlocal ei
        while ei < len(events) and events[ei].time_s < up_to:
            e = events[ei]
            ei += 1
            if _in_range(e.time_s, rng):
                buf.append(format_row(e.time_s, wall(e.time_s), events=[e.text]))

    cols = [a.tolist() for a in (s.time_s, s.t_ms, s.seq, s.v0, s.v1, s.v2, s.co2_mmhg, s.co2_pct, s.flow_lpm, s.pneumo_pa, s.pneumo_lpm)]
    for t, tm, sq, a0, a1, a2, cm, cp, fl, pp, pl in zip(*cols):
        flush_events(t - 1e-9)
        evs = []
        while ei < len(events) and abs(events[ei].time_s - t) < 1e-9:
            evs.append(events[ei].text)
            ei += 1
        breath = None
        while bi < len(breaths) and breaths[bi].time_s <= t + 1e-9:
            b = breaths[bi]
            bi += 1
            breath = BreathCells(b.n, b.etco2, b.fico2, b.rr, b.vt, b.mv, b.peak)
        if not _in_range(t, rng):
            continue
        buf.append(format_row(t, wall(t), tm, sq, [a0, a1, a2], cm, cp, fl, pp, pl, breath, evs))
        if len(buf) >= 20000:
            yield "".join(buf)
            buf.clear()
    flush_events(math.inf)
    yield "".join(buf)


def write_session(s: ParsedSession, path, rng: Range | None = None, app_version: str = "unknown") -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        for part in export_session_parts(s, rng, app_version):
            f.write(part)


def breaths_csv(s: ParsedSession, rng: Range | None = None) -> str:
    """One row per breath: the table most analyses start from."""
    rows = ["breath,time_s,wall_time_iso,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm"]
    for b in s.breaths:
        if not _in_range(b.time_s, rng):
            continue
        rows.append(
            ",".join(
                [js_num(b.n), to_fixed(b.time_s, 3), iso_ms(s.started_ms + b.time_s * 1000), num(b.etco2, 1), num(b.fico2, 1), num(b.rr, 1), num(b.vt, 0), num(b.mv, 2), num(b.peak, 1)]
            )
        )
    return "\n".join(rows) + "\n"


def event_type(text: str) -> str:
    m = re.match(r"[A-Z_]+", text)
    return m.group(0) if m else "NOTE"


def events_csv(s: ParsedSession, rng: Range | None = None) -> str:
    """Alarms, markers, connections and calibration changes."""
    rows = ["time_s,wall_time_iso,type,event"]
    for e in s.events:
        if _in_range(e.time_s, rng):
            rows.append(",".join([to_fixed(e.time_s, 3), iso_ms(s.started_ms + e.time_s * 1000), event_type(e.text), csv_escape(e.text)]))
    return "\n".join(rows) + "\n"


@dataclass
class SessionSummary:
    duration_s: float
    samples: int
    breaths: int
    alarms: int
    markers: int
    dropped: int
    etco2_mean: float
    etco2_min: float
    etco2_max: float
    rr: float
    vt: float
    mv: float
    rate_hz: int

    def as_stats(self) -> dict:
        return dict(self.__dict__)


def _mean(xs: Iterable[float]) -> float:
    v = [x for x in xs if math.isfinite(x)]
    return naive_sum(v) / len(v) if v else NAN


def summarize(s: ParsedSession) -> SessionSummary:
    seq = SeqTracker()
    for q, t in zip(s.seq.tolist(), s.t_ms.tolist()):
        seq.push(q, t)
    et = [b.etco2 for b in s.breaths if math.isfinite(b.etco2)]
    span = float(s.t_ms[-1] - s.t_ms[0]) if s.n > 1 else 0.0
    return SessionSummary(
        duration_s=s.duration_s,
        samples=s.n,
        breaths=len(s.breaths),
        alarms=sum(1 for e in s.events if e.text.startswith("ALARM_START") and " test " not in e.text),
        markers=sum(1 for e in s.events if e.text.startswith("MARK ")),
        dropped=seq.dropped,
        etco2_mean=_mean(et),
        etco2_min=min(et) if et else NAN,
        etco2_max=max(et) if et else NAN,
        rr=_mean(b.rr for b in s.breaths),
        vt=_mean(b.vt for b in s.breaths),
        mv=_mean(b.mv for b in s.breaths),
        rate_hz=js_round(((s.n - 1) * 1000) / span) if span > 0 else 0,
    )


def session_file_name(study_code: str, d: datetime | None = None) -> str:
    """``co2r_<study>_<YYYYMMDD-HHMMSS>.csv``; the study code is reduced to safe characters."""
    d = d or datetime.now()
    code = re.sub(r"[^A-Za-z0-9_-]+", "-", study_code).strip("-") or "session"
    return f"co2r_{code}_{d:%Y%m%d-%H%M%S}.csv"
