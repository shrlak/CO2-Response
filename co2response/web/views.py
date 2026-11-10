"""The browser monitor: header, alarm strip, waveforms, numbers, trend and soft keys.
Same layout and CSS as the original web app, rendered from Python."""

from __future__ import annotations

import dataclasses
import math
from typing import TYPE_CHECKING

import js

from ..engine.monitor import Snapshot
from ..forms import ALARM_FIELDS
from ..processing.co2 import mmhg_to_pct
from ..processing.metrics import co2_response_summary
from ..settings import AlarmSettings
from .charts import ResponseChart, Sweep, TrendChart
from .dom import btn, byid, data, esc, field, set_html, toast
from .sim_view import SimPanel
from .spec_form import install_spec_actions, spec_form_html

if TYPE_CHECKING:
    from .app import WebApp

PRI_LABEL = {"high": "HIGH", "medium": "MED", "advisory": "INFO"}


# ------------------------------------------------------------------ formatting (local time via the browser)


def fmt(v, d: int = 0) -> str:
    if v is None or not math.isfinite(v):
        return "--"
    s = f"{v:.{d}f}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s


def hms(ms: float) -> str:
    t = js.Date.new(ms)
    return f"{t.getHours():02d}:{t.getMinutes():02d}:{t.getSeconds():02d}"


def duration(ms: float) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def mss(ms: float) -> str:
    s = math.ceil(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def stamp(ms: float) -> str:
    from .dom import obj

    return str(js.Date.new(ms).toLocaleString(js.undefined, obj({"dateStyle": "medium", "timeStyle": "short"})))


def size_text(b: float) -> str:
    return f"{b / 1e6:.1f} MB" if b >= 1e6 else f"{max(1, round(b / 1e3))} kB"


# ------------------------------------------------------------------ header and alarm strip


def header_html(s: Snapshot) -> str:
    connected = s.status == "connected"
    any_active = any(a.state == "active" and a.priority != "advisory" for a in s.alarms)
    if s.silence_ms > 0:
        pill = ("silenced", f"SILENCED {mss(s.silence_ms)}")
    elif any_active:
        pill = ("alarm", "ALARM")
    elif not s.audio_armed:
        pill = ("audio-off", "AUDIO OFF")
    elif not connected:
        pill = ("", "DISARMED")
    else:
        pill = ("armed", "ARMED")
    status = {"connected": esc(s.source_label), "connecting": f"Connecting… {esc(s.source_label)}", "error": "Connection lost"}.get(s.status, "Not connected")
    extra = ""
    if connected:
        extra = f" · {fmt(s.measured_rate_hz)} Hz · <b>{s.dropped}</b> dropped"
        if s.bad_lines:
            extra += f" · <b>{s.bad_lines}</b> bad lines"
    if s.using_file_calibration:
        extra += " · file calibration"
    if s.replay_progress is not None:
        extra += f" · {round(s.replay_progress * 100)} %"
    rec = f'<span class="rec on">● REC {duration(s.now - s.recording.started_at)}</span>' if s.recording.active else '<span class="rec">Not recording</span>'
    return (
        '<span class="ruo-tag">RESEARCH USE ONLY</span>'
        f'<span class="hgroup">Study <b>{esc(s.settings.study_code or "—")}</b></span>'
        f'<span class="hgroup" title="{esc(s.status_detail)}"><span class="dot {s.status}"></span><b>{status}</b>{extra}</span>'
        f'<span class="hspacer"></span>{rec}<span class="pill {pill[0]}">{pill[1]}</span><span class="clock">{hms(s.now)}</span>'
    )


def describe(a) -> str:
    if a.state == "resolved":
        peak = ""
        if a.peak is not None and a.id in ("co2High", "co2Low", "co2Stop"):
            peak = f" ({'lowest' if a.id == 'co2Low' else 'peak'} {a.peak:.0f} mmHg)"
        return f"{a.message}{peak}: resolved at {hms(a.resolved_at or a.since)}"
    return a.message


def alarm_slot_html(s: Snapshot, open_list: bool) -> str:
    lst = s.alarms
    if not lst:
        msg = "No active alarms" if s.status == "connected" else "Not connected. Alarms work only while a source is connected."
        return f'<div class="alarm-slot none" role="status"><span class="msg">{msg}</span></div>'
    top = lst[0]
    if top.state == "resolved":
        cls = f"resolved r-{top.priority}"
    else:
        flash = "flash" if top.priority == "high" and not top.acknowledged else "flash-slow" if top.priority == "medium" and not top.acknowledged else ""
        cls = f"{top.priority} {flash}"
    msg = esc(describe(top))
    if top.state == "active" and top.priority != "advisory":
        msg += f'<span class="since"> · since {hms(top.since)}</span>'
    if s.silence_ms > 0 and top.priority != "advisory":
        msg += f'<span class="since"> · audio silenced {mss(s.silence_ms)}</span>'
    more = btn(f"+{len(lst) - 1} more", "alarm-more", "more") if len(lst) > 1 else ""
    if top.id == "audioOff":
        action = btn("ARM AUDIO", "arm-audio", "slot-btn")
    elif top.priority != "advisory":
        action = btn("CLEAR" if top.state == "resolved" else "ACKNOWLEDGE", "ack", "slot-btn", arg=top.id)
    else:
        action = ""
    out = f'<div style="position:relative"><div class="alarm-slot {cls}" role="alert"><span class="pri">{"RESOLVED" if top.state == "resolved" else PRI_LABEL[top.priority]}</span><span class="msg">{msg}</span>{more}{action}</div>'
    if open_list and len(lst) > 1:
        items = "".join(
            f'<li><span class="prichip {a.priority}">{"RESOLVED" if a.state == "resolved" else PRI_LABEL[a.priority]}</span><span style="flex:1">{esc(describe(a))}</span>'
            f'<span class="note">{hms(a.since)}</span>{btn("Clear" if a.state == "resolved" else "Acknowledge", "ack", "btn small", arg=a.id) if a.priority != "advisory" else ""}</li>'
            for a in lst
        )
        out += f'<div class="alarm-list"><ul>{items}</ul></div>'
    return out + "</div>"


# ------------------------------------------------------------------ alarm form (drawer and settings)


def alarm_form_html(a: AlarmSettings, p: str, snapshot=None) -> str:
    """The alarm rows come from ``co2response.forms.ALARM_FIELDS`` (shared with the
    desktop window); only the volume slider is built here."""
    out = spec_form_html(ALARM_FIELDS, a, p, "alarm", snapshot)
    vol = round(a.volume * 100)
    out += field(
        "Alarm volume", f'<input type="range" id="{p}-vol" min="10" max="100" step="5" value="{vol}" data-change="alarm-vol"><span class="u">{vol} %</span>', "Cannot be set to zero.", f"{p}-vol"
    )
    return f"<div>{out}</div>"


def install_alarm_actions(app: "WebApp") -> None:
    from .dom import actions

    e = app.engine

    def setp(patch: dict) -> None:
        e.update_settings(alarms=dataclasses.replace(e.settings.alarms, **patch))

    install_spec_actions("alarm", ALARM_FIELDS, lambda: e.settings.alarms, setp, lambda: app.rerender(force=True))
    actions(**{"alarm-vol": lambda el: (setp({"volume": float(el.value) / 100}), app.rerender())})


# ------------------------------------------------------------------ the monitor screen


TILE_IDS = ("etco2", "rr", "fico2", "flow", "peak", "vt", "mv")
#: what the trend tab can plot against time: setting value → (tab text, caption)
SERIES = {"etco2": ("EtCO2 trend", "EtCO2"), "mv": ("MV trend", "MV (L/min, 60 s average)"), "rr": ("RR trend", "RR (/min)")}


def tile_html(tid: str, label: str, unit: str, value: str, big=False, stale=False, alarm=None, flash=False, limits=None, qualifier="", raw="") -> str:
    cls = f"tile{' big' if big else ''}{' stale' if stale else ''}{f' alarm-{alarm}' if alarm else ''}"
    lim = f'<span class="limits"><span class="hi">{esc(limits[0])}</span><span>{esc(limits[1])}</span></span>' if limits else ""
    qual = f'<div class="qual"><span>{esc(qualifier)}</span>{f"<span class=raw>{esc(raw)}</span>" if raw else ""}</div>' if (qualifier or raw) else ""
    return f'<div class="{cls}" data-testid="tile-{tid}"><div class="tlabel"><span><b>{label}</b><span class="unit">{esc(unit)}</span></span>{lim}</div><div class="val{" flash" if flash else ""}">{esc(value)}</div>{qual}</div>'


def expand_btn(target: str, what: str) -> str:
    return (
        f'<button type="button" class="expand" data-act="focus" data-arg="{target}" id="expand-{target}" '
        f'title="Enlarge the {what} graph to fill the screen (click again, or Esc, to go back)" aria-label="Enlarge the {what} graph" aria-pressed="false">⤢</button>'
    )


def wave_html(cid: str, label: str, unit: str) -> str:
    tag = '<span class="wtag" id="wtag" hidden></span>' if cid == "co2" else ""
    return (
        f'<div class="wave" id="wave-{cid}"><div class="wlabel">{label}<span id="unit-{cid}">{unit}</span>{expand_btn(cid, label)}</div>{tag}'
        f'<canvas id="bg-{cid}"></canvas><canvas id="fg-{cid}"></canvas></div>'
    )


class MonitorView:
    def __init__(self, app: "WebApp") -> None:
        self.app = app
        self.sweeps: dict[str, Sweep] = {}
        self.trend: TrendChart | None = None
        self.resp: ResponseChart | None = None
        self.tab = "trend"
        self.drawer = False
        #: one graph enlarged to fill the monitor ("co2", "flow", "pneumo", "trend"), or None
        self.focus: str | None = None
        self.sim = SimPanel(app)
        self.mark: tuple[int, str] | None = None
        self._epoch = -1
        self._trend_key = None

    def html(self) -> str:
        nums = {
            "co2": '<div class="nums"><div id="t-etco2" style="display:contents"></div><div id="t-rr" style="display:contents"></div><div id="t-fico2" style="display:contents"></div></div>',
            "flow": '<div class="nums"><div id="t-flow" style="display:contents"></div><div id="t-peak" style="display:contents"></div></div>',
            "pneumo": '<div class="nums"><div id="t-vt" style="display:contents"></div><div id="t-mv" style="display:contents"></div></div>',
        }
        rows = "".join(
            f'<section class="row {cid}" id="row-{cid}">{wave_html(cid, label, unit)}{nums[cid]}</section>'
            for cid, label, unit in (("co2", "CO2", "mmHg"), ("flow", "Flow", "L/min"), ("pneumo", "Pneumotach", "L/min"))
        )
        trend = (
            '<section class="trend"><div class="trend-head" role="tablist">'
            '<button class="tab" role="tab" data-act="trend-tab" data-arg="trend" id="tab-trend">EtCO2 trend</button>'
            '<button class="tab" role="tab" data-act="trend-tab" data-arg="response" id="tab-response">Ventilatory response</button>'
            '<span class="trend-meta"><span id="trend-meta"></span>'
            '<select id="trend-series" data-change="trend-series" aria-label="Trend of" title="What the trend tab plots against time">'
            '<option value="etco2">EtCO2</option><option value="mv">MV</option><option value="rr">RR</option></select>'
            '<label>Window <select id="trend-window" data-change="trend-window" aria-label="Trend window">'
            '<option value="5">5 min</option><option value="15">15 min</option><option value="30">30 min</option><option value="120">2 h</option><option value="0">All</option></select></label>'
            f"{expand_btn('trend', 'trend')}</span></div>"
            # the rebreathing test: three keys and where the test stands
            '<div class="testbar" id="testbar"></div>'
            '<div class="trend-plot"><canvas id="trend-canvas" data-act="resp-click"></canvas></div></section>'
        )
        return (
            f'<div class="monitor-screen" id="monscreen"><main class="monitor">{rows}<div id="overlay"></div></main>{trend}'
            '<aside class="simpanel" id="simpanel" aria-label="Simulation" hidden></aside>'
            '<nav class="softkeys" id="softkeys"></nav><div id="drawer"></div></div>'
        )

    def set_focus(self, target: str | None) -> None:
        """Enlarge one graph to fill the monitor, or (None) go back to the full layout."""
        self.focus = target
        self._trend_key = None
        self._apply_focus()

    def _apply_focus(self) -> None:
        el = byid("monscreen")
        if el is None:
            return
        want = self.focus or ""
        if (el.getAttribute("data-focus") or "") != want:
            el.setAttribute("data-focus", want)
            for t in ("co2", "flow", "pneumo", "trend"):
                b = byid(f"expand-{t}")
                if b is not None:
                    on = t == self.focus
                    b.setAttribute("aria-pressed", "true" if on else "false")
                    b.textContent = "\u2921" if on else "\u2922"
                    b.title = "Back to all graphs (Esc)" if on else "Enlarge this graph to fill the screen (Esc to go back)"

    def mount(self) -> None:
        for cid, var in (("co2", "--co2"), ("flow", "--flow"), ("pneumo", "--pneumo")):
            self.sweeps[cid] = Sweep(byid(f"fg-{cid}"), byid(f"bg-{cid}"), var)
        self.trend = TrendChart(byid("trend-canvas"))
        self.resp = ResponseChart(byid("trend-canvas"))
        self._epoch = -1
        self._trend_key = None
        self.sim._key = None
        for i in TILE_IDS:
            set_html(f"t-{i}", "", force=True)
        self._apply_focus()

    def frame(self) -> None:
        e = self.app.engine
        if e.wave_epoch != self._epoch:
            self._epoch = e.wave_epoch
            for sw in self.sweeps.values():
                sw.clear()
        w = e.take_waves()
        for cid, sw in self.sweeps.items():
            sw.push(w[cid], e.frozen)
            sw.draw()

    # ---------- refresh at 4 Hz

    def refresh(self, s: Snapshot) -> None:
        st = s.settings
        n = s.numbers
        pb, wv = st.processing.barometric_mm_hg, st.processing.water_vapor
        pct = st.co2_unit == "%"

        def disp(mm):
            return mmhg_to_pct(mm, pb, wv) if pct else mm

        digits = 1 if pct else 0
        unit = "%" if pct else "mmHg"
        co2_max = disp(st.co2_scale_max_mm_hg)
        ticks = [t for t in (0, 2, 4, 6, 8, 10, 12) if t <= co2_max + 0.01] if pct else [t for t in (0, 20, 40, 60, 80, 100) if t <= co2_max]
        fs = st.flow_scale_lpm
        theme = self.app.theme
        self.sweeps["co2"].set_options(0, co2_max, ticks, False, disp(st.alarms.high_mm_hg), st.sweep_sec, s.rate_hz, theme)
        for cid in ("flow", "pneumo"):
            self.sweeps[cid].set_options(-fs, fs, [-fs, 0, fs], True, None, st.sweep_sec, s.rate_hz, theme)
        byid("unit-co2").textContent = unit

        # the list is in priority order, so the stop alarm comes before the warning
        co2_alarm = next((a for a in s.alarms if a.id in ("co2Stop", "co2High", "co2Low") and a.state == "active"), None)
        apnea = next((a for a in s.alarms if a.id == "apnea" and a.state == "active"), None)
        byid("row-co2").className = "row co2" + (" in-alarm" if (co2_alarm and co2_alarm.priority == "high") or apnea else "")
        tag = "FROZEN" if s.frozen else "SIMULATED" if s.source_kind == "simulator" else "REPLAY" if s.source_kind == "replay" else None
        wt = byid("wtag")
        wt.hidden = tag is None
        wt.textContent = tag or ""

        def lim(v):
            return "off" if v is None else fmt(disp(v), digits)

        idx = {cid: int(s.channels["channels"][cid]["index"]) for cid in ("co2", "flow", "pneumo")}

        def raw(cid):
            i = idx[cid]
            v = n.volts[i] if i < len(n.volts) else math.nan
            return f"{v:.4f} V" if st.show_raw_volts and math.isfinite(v) else ""

        def sv(stale, v, d=0):
            return "---" if stale else fmt(v, d)

        sc = s.stale
        tiles = {
            "etco2": tile_html(
                "etco2",
                "EtCO2",
                unit,
                sv(sc["co2"], disp(n.etco2), digits),
                big=True,
                stale=sc["co2"],
                alarm=("high" if co2_alarm.priority == "high" else "medium") if co2_alarm else None,
                flash=bool(co2_alarm and not co2_alarm.acknowledged),
                limits=(lim(st.alarms.high_mm_hg) + (f" / {lim(st.alarms.stop_mm_hg)}" if st.alarms.stop_mm_hg is not None else ""), lim(st.alarms.low_mm_hg)),
                qualifier="no CO2 signal" if sc["co2"] else f"last breath left out ({n.rejected.split(':')[0]})" if n.rejected else f"median 3 br: {fmt(disp(n.etco2_median), digits)}",
                raw=raw("co2"),
            ),
            "rr": tile_html("rr", "RR", "/min", fmt(n.rr), alarm="high" if apnea else None, flash=bool(apnea and not apnea.acknowledged), qualifier="no breath detected" if n.rr != n.rr and s.status == "connected" else ""),
            "fico2": tile_html("fico2", "FiCO2", unit, sv(sc["co2"], disp(n.fico2), digits), stale=sc["co2"]),
            "flow": tile_html("flow", "Flow", "L/min", sv(sc["flow"], n.flow), stale=sc["flow"], raw=raw("flow")),
            "peak": tile_html("peak", "Peak insp", "L/min", sv(sc["flow"], n.peak_flow), stale=sc["flow"]),
            "vt": tile_html(
                "vt", "VT", "mL", sv(sc["pneumo"], n.vt), stale=sc["pneumo"], raw=raw("pneumo"),
                qualifier="" if sc["pneumo"] else (f"exp {fmt(n.vte)} mL · " if math.isfinite(n.vte) else "") + f"flow {fmt(n.pneumo_flow)} L/min",
            ),  # fmt: skip
            "mv": tile_html("mv", "MV", "L/min", sv(sc["pneumo"], n.mv, 1), stale=sc["pneumo"], qualifier="" if sc["pneumo"] else "60 s average"),
        }
        for k, h in tiles.items():
            set_html(f"t-{k}", h)

        set_html("overlay", self._overlay(s) if s.status in ("idle", "error") else "")
        if self.focus == "trend" and s.status in ("idle", "error"):
            self.set_focus(None)  # the connect card lives over the waveforms
        self._apply_focus()
        self.sim.refresh(s)
        self._softkeys(s, disp, digits)
        self._trend(s, disp, pct)
        if self.drawer:
            from .dom import editing_inside

            if not editing_inside("drawer"):
                set_html("drawer", self._drawer(st.alarms, s))
        else:
            set_html("drawer", "")

    def _overlay(self, s: Snapshot) -> str:
        from .platform import web_serial_supported

        app = self.app
        title = "Connection lost" if s.status == "error" else "No signal source"
        detail = f'<p class="err">{esc(s.status_detail)}</p>' if s.status_detail else ""
        waiting = '<p class="warn">Waiting for the Arduino to be plugged back in…</p>' if s.awaiting_reconnect else ""
        ws = web_serial_supported()
        known = app.known_label
        buttons = (btn(f"Reconnect {esc(known)}", "reconnect", "btn primary") if known else "") + btn(
            "Choose another port…" if known else "Connect Arduino (USB)", "connect-serial", "btn" if known else "btn primary", disabled=not ws
        )
        buttons += btn("Start simulator", "start-sim") + btn("Replay the sample session", "replay-sample") + btn("More sources…", "open-settings", arg="connection")
        note = "" if ws else '<p class="note">This browser has no Web Serial, so it cannot read the Arduino directly. Use desktop Chrome or Edge, Python acquisition (co2r-daq, in Settings > Connection), or the desktop app (python -m co2response).</p>'
        return (
            f'<div class="connect-overlay"><div class="connect-card"><h2>{title}</h2>{detail}{waiting}'
            "<p>Connect the Arduino over USB, or start the simulator to try the monitor without hardware. Waveforms and alarms start as soon as data arrives.</p>"
            f'<div class="actions">{buttons}</div>{note}</div></div>'
        )

    def _softkeys(self, s: Snapshot, disp, digits) -> None:
        a = s.settings.alarms
        silenced = s.silence_ms > 0

        def key(main, sub, act, cls="", arg=None):
            return f'<button class="key {cls}" data-act="{act}"{f" data-arg={arg}" if arg else ""}>{main}<span class="sub">{sub}</span></button>'

        low = f" · low {fmt(disp(a.low_mm_hg), digits)}" if a.low_mm_hg is not None else ""
        stop = f" · stop {fmt(disp(a.stop_mm_hg), digits)}" if a.stop_mm_hg is not None else ""
        mark = ""
        if self.mark:
            mid, label = self.mark
            mark = (
                f'<div class="popover" style="left:0"><label for="mark-label" class="note">Label for the marker just placed</label>'
                f'<input id="mark-label" type="text" value="{esc(label)}" data-change="mark-save" data-arg="{mid}">'
                f'<div class="row-actions">{btn("Save label", "mark-save-btn", "btn primary small", arg=mid)}{btn(f"Keep “{esc(label)}”", "mark-keep", "btn small")}</div></div>'
            )
        html = (
            key("Cancel silence" if silenced else "Silence", f"{mss(s.silence_ms)} left" if silenced else f"{a.silence_sec:g} s", "silence", "active" if silenced else "")
            + key("Audio armed" if s.audio_armed else "Arm audio", "tones will sound" if s.audio_armed else "click once per session", "arm-audio", "armed" if s.audio_armed else "need")
            + f'<div style="position:relative;display:grid">{key("Mark event", "hotkey M", "mark")}{mark}</div>'
            + key("■ Stop rec" if s.recording.active else "● Record", "saves one CSV on stop" if s.recording.active else "one CSV per session", "record", "rec-on" if s.recording.active else "")
            + key("Unfreeze" if s.frozen else "Freeze", "waveforms only", "freeze", "active" if s.frozen else "")
            + key("Alarm limits", f"{'warn' if stop else 'high'} {fmt(disp(a.high_mm_hg), digits)}{stop}{low}", "drawer-open")
            + key(
                "Simulation",
                "hide the controls" if self.sim.open else "situations and values" if s.source_kind == "simulator" else "try situations, no hardware",
                "sim-toggle",
                "active" if self.sim.open else "",
            )
            + key("Settings", "calibration, connection", "open-settings")
        )
        if set_html("softkeys", html) and self.mark:
            el = byid("mark-label")
            if el is not None:
                el.focus()
                el.select()

    def _drawer(self, a: AlarmSettings, s: Snapshot | None = None) -> str:
        return (
            '<div class="drawer-backdrop" data-act="drawer-close"></div><aside class="drawer" aria-label="Alarm limits"><h2>Alarm limits</h2>'
            '<p class="note">Changes apply immediately and are saved in this browser.</p>'
            f'<div class="drawer-scroll">{alarm_form_html(a, "drawer", s)}</div><div class="row-actions" style="margin-top:auto">{btn("Test alarm", "test-alarm")}{btn("Done", "drawer-close", "btn primary")}</div></aside>'
        )

    def _testbar(self, s: Snapshot) -> None:
        ts = s.test

        def key(text, act, on, active, title):
            return btn(text, act, "btn small primary" if active else "btn small", disabled=not on, title=title)

        set_html(
            "testbar",
            key("Start baseline", "test-baseline", ts.can_baseline, ts.phase == "baseline", "Breaths from now until rebreathing starts are the baseline")
            + key("Start rebreathing", "test-start", ts.can_start, ts.phase == "rebreathing", "Press as the valve is turned to the bag")
            + key("End test", "test-end", ts.can_end, False, "Press as the valve is turned back to room air")
            + f'<span class="test-text" id="test-text" title="{esc(ts.text)}">{esc(ts.text)}</span>',
        )

    def _trend(self, s: Snapshot, disp, pct: bool) -> None:
        st = s.settings
        e = self.app.engine
        self._testbar(s)
        sel = byid("trend-window")
        if str(sel.value) != f"{st.trend_minutes:g}":
            sel.value = f"{st.trend_minutes:g}"
        series = st.trend_series if st.trend_series in SERIES else "etco2"
        ser = byid("trend-series")
        if str(ser.value) != series:
            ser.value = series
        ser.hidden = self.tab != "trend"
        tab = byid("tab-trend")
        if tab.textContent != SERIES[series][0]:
            tab.textContent = SERIES[series][0]
        tab.setAttribute("aria-selected", "true" if self.tab == "trend" else "false")
        byid("tab-response").setAttribute("aria-selected", "true" if self.tab == "response" else "false")
        key = (
            int(s.now // 1000), s.trend_version, self.tab, st.trend_minutes, st.co2_unit, st.alarms.high_mm_hg, st.alarms.low_mm_hg,
            st.alarms.stop_mm_hg, st.co2_scale_max_mm_hg, series, self.app.theme,
        )  # fmt: skip
        if key == self._trend_key:
            return
        self._trend_key = key
        now_s = s.now / 1000
        minutes = st.trend_minutes
        t0 = now_s - minutes * 60 if minutes > 0 else (e.trend[0].t if e.trend else now_s - 60)
        window = [q for q in e.trend if q.t >= t0]
        # noise removal: breaths left out as noisy, artifact or outlier are not drawn
        pts = [q for q in window if q.quality == "ok"]
        left_out = len(window) - len(pts)
        removed = f" · {left_out} noisy breath{'s' if left_out != 1 else ''} left out" if left_out else ""
        if self.tab == "trend":
            markers = [(m.t / 1000, m.label) for m in e.markers if m.t / 1000 >= t0]
            alarms = [(a / 1000, p) for a, p in e.alarm_starts if a / 1000 >= t0]
            if series == "etco2":
                # continuous CO2, without the stretches of breaths the noise screen left out
                tt, vv, join = e.trend_trace(t0)
                fin = [(q.t, disp(q.etco2)) for q in pts if math.isfinite(q.etco2)]
                stop = None if st.alarms.stop_mm_hg is None else disp(st.alarms.stop_mm_hg)
                ymax = disp(st.co2_scale_max_mm_hg)
                if stop is not None:
                    ymax = max(ymax, stop * 1.06)  # the stop limit is always on the plot
                self.trend.draw(
                    t0, now_s, ymax, disp(st.alarms.high_mm_hg), None if st.alarms.low_mm_hg is None else disp(st.alarms.low_mm_hg),
                    [a for a, _ in fin], [b for _, b in fin], tt, [disp(v) for v in vv], join, markers, alarms, pct, stop=stop,
                )  # fmt: skip
            else:
                fin = [(q.t, q.mv if series == "mv" else q.rr) for q in pts if math.isfinite(q.mv if series == "mv" else q.rr)]
                ymax = max(10.0, math.ceil(max((v for _, v in fin), default=10) * 1.2))
                self.trend.draw(
                    t0, now_s, ymax, None, None, [a for a, _ in fin], [b for _, b in fin], [], [], [], markers, alarms, False,
                    color="--pneumo" if series == "mv" else "--text", joined=True,
                )  # fmt: skip
            span = f"{minutes:g} min" if minutes > 0 else "whole session"
            meta = f"{SERIES[series][1]} · {span} · one point per breath · {len(fin)} breaths{removed}"
        else:
            # Read's method: baseline breaths averaged, the line fitted to rebreathing only
            res = e.co2_response(t0)
            self.resp.draw(res)
            hint = " · click a point to leave it out" if res.baseline or res.rebreathing else ""
            meta = co2_response_summary(res, fmt) + (removed if res.fit else "") + hint
        el = byid("trend-meta")
        el.textContent = meta
        el.title = meta  # the full text when it is cut short

    def response_click(self, client_x: float, client_y: float) -> bool:
        """A click on a breath in the response plot takes it out of the fit, or puts it back."""
        if self.tab != "response" or self.resp is None:
            return False
        key = self.resp.hit(client_x, client_y)
        if key is None or not self.app.engine.toggle_breath(int(key)):
            return False
        self._trend_key = None
        return True


def install_monitor_actions(app: "WebApp") -> None:
    from .dom import actions

    mv = app.monitor
    e = app.engine

    def mark(_el=None):
        m = e.add_marker()
        mv.mark = (m.id, m.label)
        app.refresh()

    def mark_save(el):
        mid = int(data(el, "arg"))
        inp = byid("mark-label")
        e.rename_marker(mid, inp.value if inp is not None else "")
        mv.mark = None
        app.refresh()

    def test_alarm(_el):
        if not e.audio.armed:
            toast("Arm audio first to hear the test tones.")
        e.test_alarm()
        app.refresh()

    def window(el):
        e.update_settings(trend_minutes=float(el.value))
        app.refresh()

    def tab(el):
        mv.tab = data(el, "arg")
        mv._trend_key = None
        app.refresh()

    def series(el):
        e.update_settings(trend_series=str(el.value))
        app.refresh()

    def resp_click(el):
        from . import dom

        if mv.response_click(dom.last_click[0], dom.last_click[1]):
            app.refresh()

    actions(
        silence=lambda el: (e.toggle_silence(), app.refresh()),
        freeze=lambda el: (e.set_frozen(not e.frozen), app.refresh()),
        mark=mark,
        test_alarm=test_alarm,
        **{
            "mark-save": mark_save,
            "mark-save-btn": mark_save,
            "mark-keep": lambda el: (setattr(mv, "mark", None), app.refresh()),
            "drawer-open": lambda el: (setattr(mv, "drawer", True), app.refresh()),
            "drawer-close": lambda el: (setattr(mv, "drawer", False), app.refresh()),
            "test-alarm": test_alarm,
            "trend-window": window,
            "trend-tab": tab,
            "trend-series": series,
            "resp-click": resp_click,
            "test-baseline": lambda el: (e.start_baseline(), app.refresh()),
            "test-start": lambda el: (e.start_rebreathing(), app.refresh()),
            "test-end": lambda el: (e.end_test(), app.refresh()),
            "focus": lambda el: (mv.set_focus(None if mv.focus == data(el, "arg") else data(el, "arg")), app.refresh()),
        },
    )
    app.hotkey_mark = mark
