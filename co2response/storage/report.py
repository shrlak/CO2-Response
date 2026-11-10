"""The test report: a session's CO2 response as numbers, a plot and a per-breath table.

    from co2response.storage.report import build_report, report_pdf
    r = build_report(read_session("co2r_STUDY-01_….csv"), "co2r_STUDY-01_….csv")
    open("report.pdf", "wb").write(report_pdf(r))

The response is worked out the same way the live monitor does it
(``processing.metrics.co2_response``): baseline breaths averaged, the line fitted to
each rebreathing breath's own minute ventilation against its EtCO2, without the
first seconds of rebreathing, the breaths after the end of the test, the breaths the
noise screen rejected and the ones taken out by hand. No user-interface code, so the
desktop app, the browser and the command line all make the same report.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from ..jsfmt import iso_ms
from ..processing.metrics import CO2Response, TestWindow, co2_response, normalized_slopes, test_window
from .pdf import LETTER, Pdf, text_width
from .session_file import ParsedSession, SessionSummary, session_markers, summarize

NAN = float("nan")


def _json(s: ParsedSession, key: str) -> dict:
    try:
        v = json.loads(s.meta.get(key, "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def session_response(s: ParsedSession, skip_sec: float | None = None) -> CO2Response:
    """The CO2 response of a recorded session. ``skip_sec`` defaults to what the
    recording's header says (0 for files from before it was recorded)."""
    if skip_sec is None:
        v = _json(s, "test").get("responseSkipSec")
        skip_sec = float(v) if isinstance(v, (int, float)) else 0.0
    out = {b.n for b in s.breaths if b.quality == "excluded"}
    pts = ((b.time_s, b.etco2, b.mv_breath, b.n) for b in s.breaths if b.quality in ("ok", "", "excluded"))
    return co2_response(pts, test_window(session_markers(s.events)), skip_sec, lambda k: k in out)


def spo2_stats(summary: SessionSummary) -> list[tuple[str, str]]:
    """The lowest SpO2 for a review screen's summary (nothing without an oximeter)."""
    return [("SpO2 lowest", f"{summary.spo2_min:.0f} %")] if math.isfinite(summary.spo2_min) else []


def response_stats(res: CO2Response) -> list[tuple[str, str]]:
    """The response as (label, value) pairs for a review screen's summary."""
    if not res.split:
        return [("CO2 response", f"slope {res.slope:.2f} L/min/mmHg, r {res.r:.2f} (n {len(res.rebreathing)}, no Rebreathing marker)" if res.fit else "no Rebreathing marker")]
    fit = f"slope {res.slope:.2f} L/min/mmHg, r {res.r:.2f}, x-int {_num(res.x_intercept)} mmHg (n {len(res.rebreathing)})" if res.fit else f"not fitted ({len(res.rebreathing)} breaths)"
    base = f"EtCO2 {_num(res.baseline_etco2)} mmHg, MV {_num(res.baseline_mv, 2)} L/min (n {len(res.baseline)})" if res.baseline else "no baseline breaths"
    return [("CO2 response", fit), ("Baseline", base)]


@dataclass
class BreathRow:
    n: float
    time_s: float
    etco2: float
    vt: float
    rr: float
    mv: float
    #: baseline, fitted, not fitted, excluded, rejected (with why), or "-" (before the baseline)
    used: str


@dataclass
class Report:
    file_name: str
    study_code: str
    started_ms: float
    meta: dict
    summary: SessionSummary
    response: CO2Response
    window: TestWindow
    markers: list[tuple[float, str]]
    rows: list[BreathRow]
    #: height, weight, age as recorded (None where not entered)
    subject: dict
    normalized: dict
    #: (label, value) lines about how the session was measured
    setup: list[tuple[str, str]] = field(default_factory=list)
    app_version: str = ""
    generated_ms: float = 0.0


def _num(v: float, d: int = 1, unit: str = "") -> str:
    return f"{v:.{d}f}{unit}" if v is not None and math.isfinite(v) else "--"


def _clock(sec: float) -> str:
    t = max(0, round(sec))
    return f"{t // 60}:{t % 60:02d}"


def build_report(s: ParsedSession, file_name: str = "", skip_sec: float | None = None, app_version: str = "", generated_ms: float = 0.0) -> Report:
    res = session_response(s, skip_sec)
    w = res.window
    role = {}
    for name, pts in (("baseline", res.baseline), ("fitted", res.rebreathing), ("not fitted", res.skipped), ("excluded", res.excluded)):
        for p in pts:
            role[p.key] = name
    rows = []
    for b in s.breaths:
        q = b.quality or "ok"
        used = role.get(b.n, "-") if q in ("ok", "excluded") else f"rejected ({q})"
        rr = 60 / b.period_s if math.isfinite(b.period_s) and b.period_s > 0 else NAN
        rows.append(BreathRow(b.n, b.time_s, b.etco2, b.vt, rr, b.mv_breath, used))

    test = _json(s, "test")
    subject = {k: test.get(k) for k in ("heightCm", "weightKg", "ageYears")}
    proc = _json(s, "processing")
    alarms = _json(s, "alarm_settings")
    setup = [
        ("Source", s.meta.get("source", "unknown")),
        ("Firmware", s.meta.get("firmware", "unknown")),
        ("Sample rate", f"{s.meta.get('rate_hz', '?')} Hz"),
        ("Barometric pressure", f"{proc.get('barometricMmHg', '?')} mmHg"),
    ]
    if proc.get("co2DelayMs"):
        setup.append(("CO2 delay removed", f"{proc['co2DelayMs']:g} ms"))
    if proc.get("volumeBtps"):
        setup.append(("Volumes", f"BTPS (room at {proc.get('ambientTempC', '?')} °C)"))
    if proc.get("rebreathingGasFactor", 1) != 1:
        setup.append(("Bag gas factor", f"x{proc['rebreathingGasFactor']:g} on flow while rebreathing"))
    if alarms:
        stop = alarms.get("stopMmHg")
        setup.append(("EtCO2 limits", f"warning {alarms.get('highMmHg', '?')} mmHg" + (f", stop {stop:g} mmHg" if isinstance(stop, (int, float)) else "")))
    for cid, ch in ((s.channels or {}).get("channels") or {}).items():
        at = ch.get("calibratedAt")
        setup.append((f"Calibrated: {ch.get('label') or cid}", str(at)[:16].replace("T", " ") + " UTC" if at else "never (default values)"))

    return Report(
        file_name=file_name,
        study_code=s.study_code,
        started_ms=s.started_ms,
        meta=dict(s.meta),
        summary=summarize(s),
        response=res,
        window=w,
        markers=session_markers(s.events),
        rows=rows,
        subject=subject,
        normalized=normalized_slopes(res.slope, subject.get("heightCm"), subject.get("weightKg")),
        setup=setup,
        app_version=app_version,
        generated_ms=generated_ms,
    )


def result_lines(r: Report) -> list[tuple[str, str]]:
    """The results as (label, value) pairs: what the report prints and the tests read."""
    res, w = r.response, r.window
    out = []
    if not res.split:
        out.append(("Test phases", "no Rebreathing marker: every clean breath is fitted"))
    else:
        phases = []
        if w.baseline is not None:
            phases.append(f"baseline from {_clock(w.baseline)}")
        if w.start is not None:
            phases.append(f"rebreathing from {_clock(w.start)}")
        start, end = w.start, w.end
        if end is not None and start is not None:
            phases.append(f"ended at {_clock(end)} ({_clock(end - start)} of rebreathing)")
        out.append(("Test phases", "; ".join(phases) or "--"))
        out.append(("Baseline", f"EtCO2 {_num(res.baseline_etco2)} mmHg, MV {_num(res.baseline_mv, 2)} L/min (mean of {len(res.baseline)} breaths)"))
    if res.fit:
        out.append(("Response slope", f"{res.slope:.3f} L/min per mmHg"))
        out.append(("Correlation r", f"{res.r:.3f}  (n = {len(res.rebreathing)} breaths)"))
        out.append(("x-intercept", f"{_num(res.x_intercept)} mmHg (EtCO2 where the line reaches MV = 0)"))
        ets = [p.etco2 for p in res.rebreathing]
        out.append(("Fitted EtCO2 range", f"{min(ets):.1f} to {max(ets):.1f} mmHg"))
    else:
        out.append(("Response slope", f"not fitted: {len(res.rebreathing)} breaths (at least 3 are needed)"))
    n = r.normalized
    if math.isfinite(n["per_kg_ml"]):
        per_m2 = f"; {n['per_m2']:.3f} L/min/mmHg per m2 (BSA {n['bsa_m2']:.2f} m2)" if math.isfinite(n["per_m2"]) else ""
        out.append(("Normalized slope", f"{n['per_kg_ml']:.1f} mL/min/mmHg per kg{per_m2}"))
    sm = r.summary
    left = [f"{sm.rejected} rejected by the noise screen"]
    if res.skipped:
        why = ([f"first {res.skip_sec:g} s of rebreathing"] if res.skip_sec else []) + (["after the end of the test"] if w.end is not None else [])
        left.append(f"{len(res.skipped)} not fitted ({' and '.join(why)})")
    if res.excluded:
        left.append(f"{len(res.excluded)} excluded by hand")
    out.append(("Breaths", f"{sm.breaths} recorded; " + ", ".join(left)))
    out.append(("EtCO2", f"mean {_num(sm.etco2_mean)} mmHg (min {_num(sm.etco2_min)}, max {_num(sm.etco2_max)})"))
    if math.isfinite(sm.spo2_min):
        out.append(("SpO2", f"lowest {sm.spo2_min:.0f} %"))
    out.append(("Alarms", f"{sm.alarms} alarm{'s' if sm.alarms != 1 else ''}, {sm.dropped} dropped samples"))
    return out


def report_text(r: Report) -> str:
    """The report's numbers as plain text (for a terminal, or a quick look)."""
    lines = [f"CO2 response report: {r.file_name or r.study_code}", f"Study {r.study_code or '--'} · started {iso_ms(r.started_ms)} · length {_clock(r.summary.duration_s)}"]
    lines += [f"{k}: {v}" for k, v in result_lines(r)]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the PDF

_GREY = (0.45, 0.45, 0.45)
_LIGHT = (0.82, 0.82, 0.82)
_GREEN = (0.10, 0.50, 0.24)
_BLUE = (0.10, 0.36, 0.72)
_RED = (0.78, 0.12, 0.10)
_AMBER = (0.75, 0.45, 0.0)
M = 48  # page margin, points


def _ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    t = math.ceil(lo / step - 1e-9) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def _wrap(text: str, size: float, font: str, width: float) -> list[str]:
    """Break ``text`` into lines no wider than ``width`` points."""
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}" if cur else word
        if cur and text_width(trial, size, font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur or not lines else lines


class _Axes:
    """A plot area: maps data to the page and draws the frame, grid and tick labels."""

    def __init__(self, pdf: Pdf, x: float, y: float, w: float, h: float, x0: float, x1: float, y0: float, y1: float) -> None:
        self.pdf, self.x, self.y, self.w, self.h = pdf, x, y, w, h
        self.x0, self.x1, self.y0, self.y1 = x0, x1 if x1 > x0 else x0 + 1, y0, y1 if y1 > y0 else y0 + 1

    def px(self, v: float) -> float:
        return self.x + (v - self.x0) / (self.x1 - self.x0) * self.w

    def py(self, v: float, y0: float | None = None, y1: float | None = None) -> float:
        a, b = (self.y0, self.y1) if y0 is None or y1 is None else (y0, y1)
        return self.y + self.h - (v - a) / (b - a) * self.h

    def frame(self, xlabel: str, ylabel: str, xfmt=lambda v: f"{v:g}", yfmt=lambda v: f"{v:g}", xticks: list[float] | None = None) -> None:
        p = self.pdf
        for t in xticks if xticks is not None else _ticks(self.x0, self.x1, 8):
            X = self.px(t)
            p.line(X, self.y, X, self.y + self.h, 0.4, _LIGHT)
            p.text(X, self.y + self.h + 11, xfmt(t), 8, color=_GREY, align="center")
        for t in _ticks(self.y0, self.y1, 6):
            Y = self.py(t)
            p.line(self.x, Y, self.x + self.w, Y, 0.4, _LIGHT)
            p.text(self.x - 5, Y + 3, yfmt(t), 8, color=_GREY, align="right")
        p.rect(self.x, self.y, self.w, self.h, stroke=_GREY, width=0.8)
        p.text(self.x + self.w / 2, self.y + self.h + 24, xlabel, 9, color=_GREY, align="center")
        p.text(self.x, self.y - 6, ylabel, 9, color=_GREY)


def _response_plot(pdf: Pdf, r: Report, x: float, y: float, w: float, h: float) -> None:
    res = r.response
    every = res.baseline + res.rebreathing + res.skipped + res.excluded
    xs = [p.etco2 for p in every] or [30.0, 50.0]
    ys = [p.mv for p in every] or [0.0, 10.0]
    ax = _Axes(pdf, x, y, w, h, math.floor(min(xs) - 2), math.ceil(max(xs) + 2), 0, max(10.0, math.ceil(max(ys) * 1.15)))
    ax.frame("EtCO2 (mmHg)", "MV of each breath (L/min)")
    for p in res.skipped:
        pdf.circle(ax.px(p.etco2), ax.py(p.mv), 2.2, stroke=_LIGHT, width=0.8)
    for p in res.baseline:
        pdf.circle(ax.px(p.etco2), ax.py(p.mv), 3, stroke=_GREY, width=1.1)
    for p in res.rebreathing:
        pdf.circle(ax.px(p.etco2), ax.py(p.mv), 2.8, stroke=None, fill=_GREEN)
    for p in res.excluded:
        pdf.cross(ax.px(p.etco2), ax.py(p.mv), 3, _RED, 1.1)
    if res.fit:
        a, b = min(p.etco2 for p in res.rebreathing), max(p.etco2 for p in res.rebreathing)
        pdf.line(ax.px(a), ax.py(res.fit.slope * a + res.fit.intercept), ax.px(b), ax.py(res.fit.slope * b + res.fit.intercept), 1.6)
    # legend
    lx, ly = x + 8, y + 12
    items = [("fitted", "dot")] + ([("baseline (averaged)", "ring")] if res.baseline else []) + ([("not fitted", "faint")] if res.skipped else []) + ([("excluded by hand", "cross")] if res.excluded else [])
    for text, kind in items:
        if kind == "dot":
            pdf.circle(lx, ly - 3, 2.8, stroke=None, fill=_GREEN)
        elif kind == "ring":
            pdf.circle(lx, ly - 3, 3, stroke=_GREY, width=1.1)
        elif kind == "faint":
            pdf.circle(lx, ly - 3, 2.2, stroke=_LIGHT, width=0.8)
        else:
            pdf.cross(lx, ly - 3, 3, _RED, 1.1)
        pdf.text(lx + 8, ly, text, 8, color=_GREY)
        lx += 16 + text_width(text, 8)


def _time_plot(pdf: Pdf, r: Report, x: float, y: float, w: float, h: float) -> None:
    rows = [q for q in r.rows if not q.used.startswith("rejected")]
    t1 = max(r.summary.duration_s, 1.0)
    et = [q.etco2 for q in rows if math.isfinite(q.etco2)] or [0.0, 60.0]
    mv = [q.mv for q in rows if math.isfinite(q.mv)] or [0.0, 10.0]
    ax = _Axes(pdf, x, y, w, h, 0, t1, math.floor(min(et) / 10) * 10, math.ceil(max(et) / 10) * 10 + 1e-9)
    step = next(s for s in (30, 60, 120, 300, 600, 1800, 3600) if t1 / s <= 10) if t1 <= 36000 else 7200
    ax.frame("time since the recording started (min:s)", "EtCO2 (mmHg), filled", xfmt=_clock, xticks=[i * step for i in range(int(t1 // step) + 1)])
    m0, m1 = 0.0, max(10.0, math.ceil(max(mv) * 1.15))
    pdf.text(x + w, y - 6, "MV of each breath (L/min), rings", 9, color=_BLUE, align="right")
    for t in _ticks(m0, m1, 5):
        pdf.text(x + w + 5, ax.py(t, m0, m1) + 3, f"{t:g}", 8, color=_BLUE)
    for t, label in r.markers:
        X = ax.px(t)
        pdf.line(X, y, X, y + h, 0.7, _GREY, (3, 2))
        fits = X + 4 + text_width(label[:28], 7) < x + w
        pdf.text(X + 2 if fits else X - 2, y + 9, label[:28], 7, color=_GREY, align="left" if fits else "right")
    for q in rows:
        if math.isfinite(q.mv):
            pdf.circle(ax.px(q.time_s), ax.py(q.mv, m0, m1), 1.8, stroke=_BLUE, width=0.7)
    for q in rows:
        if math.isfinite(q.etco2):
            pdf.circle(ax.px(q.time_s), ax.py(q.etco2), 1.7, stroke=None, fill=_AMBER)


def _footer(pdf: Pdf, r: Report, page: int, pages: int) -> None:
    w, h = LETTER
    pdf.line(M, h - 38, w - M, h - 38, 0.5, _LIGHT)
    made = f"generated {iso_ms(r.generated_ms)[:16].replace('T', ' ')} UTC" if r.generated_ms else ""
    pdf.text(M, h - 26, f"{r.file_name or r.study_code} · CO2-Response {r.app_version} · {made}".rstrip(" ·"), 7.5, color=_GREY)
    pdf.text(w - M, h - 26, f"Research use only · page {page} of {pages}", 7.5, color=_GREY, align="right")


TABLE_ROWS = 62


def report_pdf(r: Report) -> bytes:
    """The report as a PDF: results and plots on the first page, then every breath."""
    w, h = LETTER
    pdf = Pdf(LETTER, f"CO2 response report {r.study_code}", f"CO2-Response {r.app_version}".strip())
    pdf.page()
    y: float = M + 6
    pdf.text(M, y, "Ventilatory response to CO2", 17, "bold")
    pdf.text(w - M, y, "RESEARCH USE ONLY", 9, "bold", _RED, "right")
    y += 14
    pdf.text(M, y, "Read's rebreathing method. Not a medical device; not for clinical decision-making.", 8.5, color=_GREY)
    y += 18
    sub = r.subject
    size = ", ".join(f"{label} {sub[k]:g} {unit}" for k, label, unit in (("heightCm", "height", "cm"), ("weightKg", "weight", "kg"), ("ageYears", "age", "y")) if isinstance(sub.get(k), (int, float)))
    head = [("Study code", r.study_code or "--"), ("Started", iso_ms(r.started_ms)[:19].replace("T", " ") + " UTC"), ("Length", _clock(r.summary.duration_s) + " min:s"), ("Subject", size or "size not entered")]
    for i, (k, v) in enumerate(head):
        cx = M + (i % 2) * (w - 2 * M) / 2
        pdf.text(cx, y, k, 8.5, color=_GREY)
        pdf.text(cx + 62, y, v, 9.5, "bold")
        y += 13 if i % 2 else 0
    y += 6
    pdf.line(M, y, w - M, y, 0.5, _LIGHT)
    y += 15
    pdf.text(M, y, "Results", 11, "bold")
    y += 14
    for k, v in result_lines(r):
        pdf.text(M, y, k, 8.5, color=_GREY)
        font = "bold" if k in ("Response slope", "Baseline", "Normalized slope") else "regular"
        for part in _wrap(v, 9.5, font, w - 2 * M - 104):
            pdf.text(M + 104, y, part, 9.5, font)
            y += 12.5
    y += 20
    plot_h = 190
    _response_plot(pdf, r, M + 30, y, w - 2 * M - 40, plot_h)
    y += plot_h + 52
    _time_plot(pdf, r, M + 30, y, w - 2 * M - 60, 105)
    y += 105 + 44
    pdf.text(M, y, "How it was measured", 9.5, "bold")
    y += 12
    col = (w - 2 * M) / 2
    for i, (k, v) in enumerate(r.setup):
        cx = M + (i % 2) * col
        pdf.text(cx, y, k, 7.5, color=_GREY)
        pdf.text(cx + 104, y, str(v)[:44], 7.5)
        y += 10 if i % 2 else 0

    # the per-breath table
    head_line = f"{'breath':>6} {'time':>7} {'EtCO2':>7} {'VT':>6} {'RR':>6} {'MV':>7}  used as"
    units = f"{'':>6} {'min:s':>7} {'mmHg':>7} {'mL':>6} {'/min':>6} {'L/min':>7}"
    for start in range(0, len(r.rows), TABLE_ROWS):
        pdf.page()
        y = M + 6
        pdf.text(M, y, "Every breath", 13, "bold")
        pdf.text(w - M, y, "MV and RR are each breath's own (VT x 60 / its length)", 8, color=_GREY, align="right")
        y += 18
        pdf.text(M, y, head_line, 8.5, "mono-bold")
        y += 10
        pdf.text(M, y, units, 8, "mono", _GREY)
        y += 4
        pdf.line(M, y, w - M, y, 0.5, _GREY)
        y += 10
        for q in r.rows[start : start + TABLE_ROWS]:
            line = f"{q.n:>6.0f} {_clock(q.time_s):>7} {_num(q.etco2):>7} {_num(q.vt, 0):>6} {_num(q.rr):>6} {_num(q.mv, 2):>7}  {q.used}"
            pdf.text(M, y, line, 8.5, "mono", _GREY if q.used in ("-", "not fitted") or q.used.startswith(("rejected", "excluded")) else (0, 0, 0))
            y += 10.4
    for i in range(pdf.pages):
        pdf.select(i)
        _footer(pdf, r, i + 1, pdf.pages)
    return pdf.bytes()
