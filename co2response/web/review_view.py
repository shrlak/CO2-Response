"""Review a saved session in the browser: summary, zoomable charts, exports, events."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .. import __version__
from ..storage.session_file import Range, breaths_csv, event_type, events_csv, export_session_parts, summarize
from .charts import ReviewChart, elapsed
from .dom import actions, btn, byid, css_var, data, download, esc, stat
from .views import duration, fmt, stamp

if TYPE_CHECKING:
    from .app import WebApp


class ReviewView:
    def __init__(self, app: "WebApp") -> None:
        self.app = app
        self.sid: str | None = None
        self.rec = None
        self.parsed = None
        self.error = ""
        self.zoom: Range | None = None
        self.charts: list[ReviewChart] = []

    def open(self, sid: str) -> None:
        self.sid, self.zoom, self.error, self.charts = sid, None, "", []
        lib = self.app.engine.library
        self.rec = lib.get(sid)
        try:
            if self.rec is None:
                raise ValueError("This session is no longer in the library.")
            self.parsed = lib.parse(sid)
        except (OSError, ValueError) as e:
            self.parsed = None
            self.error = str(e)

    @property
    def full(self) -> Range:
        return Range(0, max(1.0, self.parsed.duration_s))

    def html(self) -> str:
        back = btn("← Back to data", "review-back", "btn primary")
        if self.error or self.parsed is None:
            return f'<div class="settings-body">{back}<p class="err">Could not open this session: {esc(self.error)}</p></div>'
        s, rec = self.parsed, self.rec
        summ = summarize(s)
        conv = " (exports are converted to v2)" if s.version < 2 else ""
        stats = "".join(
            stat(k, v)
            for k, v in [
                ("Length", duration(summ.duration_s * 1000)),
                ("Breaths", str(summ.breaths)),
                ("EtCO2 mean (min–max)", f"{fmt(summ.etco2_mean, 1)} ({fmt(summ.etco2_min)}–{fmt(summ.etco2_max)}) mmHg"),
                ("RR mean", f"{fmt(summ.rr, 1)} /min"),
                ("VT mean", f"{fmt(summ.vt)} mL"),
                ("MV mean", f"{fmt(summ.mv, 2)} L/min"),
                ("Alarms", str(summ.alarms)),
                ("Markers", str(summ.markers)),
                ("Samples", f"{summ.samples:,} at {summ.rate_hz} Hz"),
                ("Dropped samples", str(summ.dropped)),
            ]
        )

        def legend(items):
            return '<div class="legend">' + "".join(f'<span><i style="background:var({v})"></i>{t}</span>' for t, v in items) + "</div>"

        events = "".join(
            f'<div class="{"alarm" if event_type(e.text).startswith("ALARM") else ""}">{btn(elapsed(e.time_s), "review-event", "linkish", arg=f"{e.time_s:.3f}", title="Zoom to 30 s either side")} {esc(e.text)}</div>'
            for e in s.events
        ) or "<div>No events.</div>"
        return (
            f'<div class="settings-body review"><div class="review-head">{back}<div style="min-width:0"><h2 class="mono" style="font-size:18px">{esc(rec.file_name)}</h2>'
            f'<p class="note">Study {esc(s.study_code or "—")} · started {stamp(s.started_ms)} · {esc(s.meta.get("source", "unknown source"))} · firmware {esc(s.meta.get("firmware", "?"))} · file format v{s.version}{conv}</p></div></div>'
            f'<div class="stats">{stats}</div>'
            f'<div class="row-actions" style="margin:4px 0 10px"><span class="note" id="zoom-note"></span>{btn("Show whole session", "review-whole", "btn small", id="review-whole")}</div>'
            f'<h3>EtCO2 and RR per breath</h3>{legend([("EtCO2 (left)", "--co2"), ("RR (right)", "--text")])}<div class="review-chart"><canvas id="rc-0"></canvas></div>'
            f'<h3>Tidal volume and minute ventilation</h3>{legend([("VT (left)", "--pneumo"), ("MV (right)", "--flow")])}<div class="review-chart" style="height:180px"><canvas id="rc-1"></canvas></div>'
            f'<h3 id="wave-title">Waveforms</h3>{legend([("CO2 mmHg (left)", "--co2"), ("Flow (right)", "--flow"), ("Pneumotach (right)", "--pneumo")])}<div class="review-chart" style="height:230px"><canvas id="rc-2"></canvas></div>'
            '<h3 style="margin-top:14px">Export</h3><div class="row-actions" style="margin:6px 0 14px">'
            f'{btn("Whole session (one .csv)", "export", "btn primary", arg=f"{rec.id}|session")}{btn("Breaths (.csv)", "export", arg=f"{rec.id}|breaths")}{btn("Alarms & events (.csv)", "export", arg=f"{rec.id}|events")}'
            f'{btn("Zoomed range, all data (.csv)", "review-range", arg="all", id="rr-all")}{btn("Zoomed range, breaths (.csv)", "review-range", arg="breaths", id="rr-breaths")}'
            f'{btn("Zoomed range, events (.csv)", "review-range", arg="events", id="rr-events")}{btn("Replay on the monitor", "review-replay", disabled=not s.n)}</div>'
            f'<h3>Alarms and events ({len(s.events)})</h3><div class="loglist" style="max-height:280px;margin-top:6px">{events}</div></div>'
        )

    def mount(self) -> None:
        if self.parsed is None:
            return
        s = self.parsed
        b = s.breaths
        bt = np.asarray([x.time_s for x in b], float)

        def arr(xs):
            return np.asarray(xs, float)

        self.charts = [ReviewChart(byid(f"rc-{i}"), self._on_zoom) for i in range(3)]
        c0, c1, c2 = self.charts
        c0.series = [(bt, arr([x.etco2 for x in b]), css_var("--co2"), False, True), (bt, arr([x.rr for x in b]), css_var("--text"), True, False)]
        c0.left_label, c0.right_label = "EtCO2 mmHg", "RR /min"
        c1.series = [(bt, arr([x.vt for x in b]), css_var("--pneumo"), False, True), (bt, arr([x.mv for x in b]), css_var("--flow"), True, False)]
        c1.left_label, c1.right_label = "VT mL", "MV L/min"
        c2.series = [(s.time_s, s.co2_mmhg, css_var("--co2"), False, False), (s.time_s, s.flow_lpm, css_var("--flow"), True, False), (s.time_s, s.pneumo_lpm, css_var("--pneumo"), True, False)]
        c2.left_label, c2.right_label = "CO2 mmHg", "Flow L/min"
        ev = [(e.time_s, event_type(e.text) == "ALARM_START") for e in s.events if event_type(e.text) in ("ALARM_START", "MARK")]
        for c in self.charts:
            c.events = ev
        self.set_zoom(None)

    def _on_zoom(self, a: float, b: float) -> None:
        f = self.full
        self.set_zoom(Range(max(f.t0, a), min(f.t1, b)))

    def set_zoom(self, rng: Range | None) -> None:
        self.zoom = rng
        r = rng or self.full
        for c in self.charts:
            c.draw(r.t0, r.t1)
        note = byid("zoom-note")
        if note is not None:
            note.textContent = f"Drag across a chart to zoom. Showing {elapsed(r.t0)}–{elapsed(r.t1)}." if rng else "Drag across a chart to zoom. Showing the whole session."
        for i in ("review-whole", "rr-all", "rr-breaths", "rr-events"):
            el = byid(i)
            if el is not None:
                el.disabled = rng is None
        t = byid("wave-title")
        if t is not None:
            t.textContent = "Waveforms" if rng else "Waveforms (zoom in for full detail)"

    def redraw(self) -> None:
        for c in self.charts:
            c.draw()

    def _base(self) -> str:
        n = self.rec.file_name
        tag = f"_{round(self.zoom.t0)}-{round(self.zoom.t1)}s" if self.zoom else ""
        return (n[:-4] if n.lower().endswith(".csv") else n) + tag


def install_review_actions(app: "WebApp") -> None:
    rv = app.review

    def range_export(el):
        what = data(el, "arg")
        if rv.zoom is None:
            return
        if what == "all":
            download(f"{rv._base()}.csv", list(export_session_parts(rv.parsed, rv.zoom, __version__)))
        else:
            download(f"{rv._base()}_{what}.csv", breaths_csv(rv.parsed, rv.zoom) if what == "breaths" else events_csv(rv.parsed, rv.zoom))

    def event(el):
        t = float(data(el, "arg"))
        rv.set_zoom(Range(max(0, t - 30), min(rv.full.t1, t + 30)))

    def replay(el):
        app.engine.connect("replay", session=rv.parsed, file_name=rv.rec.file_name, speed=1, use_file_calibration=True)
        app.show("monitor")

    actions(
        **{
            "open-review": lambda el: app.show("review", data(el, "arg")),
            "review-back": lambda el: (setattr(app.settings, "tab", "data"), app.show("settings")),
            "review-whole": lambda el: rv.set_zoom(None),
            "review-range": range_export,
            "review-event": event,
            "review-replay": replay,
        }
    )


