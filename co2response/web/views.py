"""The browser monitor: header, alarm strip, waveforms, numbers, trend and soft keys.
Same layout and CSS as the original web app, rendered from Python."""

from __future__ import annotations

import dataclasses
import math
from typing import TYPE_CHECKING

import js

from ..engine.monitor import Snapshot
from ..processing.co2 import mmhg_to_pct
from ..processing.metrics import linear_fit
from ..settings import AlarmSettings
from .charts import ResponseChart, Sweep, TrendChart
from .dom import as_float, btn, byid, data, esc, field, number_field, seg, set_html, toast, toggle

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
        if a.peak is not None and a.id in ("co2High", "co2Low"):
            peak = f" ({'peak' if a.id == 'co2High' else 'lowest'} {a.peak:.0f} mmHg)"
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


SOURCE_HINT = {
    "etco2": "Compared once per breath, after each expiration.",
    "live": "Live CO2 alarms when the waveform itself stays past a limit, e.g. raised inspired CO2 during rebreathing.",
}


def alarm_form_html(a: AlarmSettings, p: str) -> str:
    out = field("Alarm on", seg("alarm-src", a.source, [("etco2", "EtCO2 per breath"), ("live", "Live CO2")]), SOURCE_HINT[a.source])
    out += number_field("High CO2 limit", "alarm", a.high_mm_hg, "mmHg", "Drawn as the dashed line on the capnogram and trend.", f"{p}-high", 5, 150, 1, "high_mm_hg")
    out += field("Low CO2 limit", toggle("alarm-low-on", a.low_mm_hg is not None, f"{p}-lowon"), "Optional. Helps catch apnea, a leak or a disconnected sample line.", f"{p}-lowon")
    if a.low_mm_hg is not None:
        out += number_field("Low limit", "alarm", a.low_mm_hg, "mmHg", None, f"{p}-low", 0, 100, 1, "low_mm_hg")
    if a.source == "etco2":
        out += number_field("Trigger delay", "alarm", a.delay_breaths, "breaths", "Consecutive breaths past the limit before alarming. Stops a single breath from alarming.", f"{p}-delayb", 1, 10, 1, "delay_breaths")
    else:
        out += number_field("Trigger delay", "alarm", a.delay_sec, "s", None, f"{p}-delays", 0, 60, 0.5, "delay_sec")
    out += number_field("Hysteresis", "alarm", a.hysteresis_mm_hg, "mmHg", "A high alarm clears only below limit − hysteresis.", f"{p}-hyst", 0, 10, 0.5, "hysteresis_mm_hg")
    out += number_field("Apnea alarm after", "alarm", a.apnea_sec, "s", "High-priority alarm when no breath is detected for this long. 0 turns it off.", f"{p}-apnea", 0, 120, 1, "apnea_sec")
    out += number_field("Silence duration", "alarm", a.silence_sec, "s", "Audio comes back on by itself after this. Maximum 120 s.", f"{p}-silence", 10, 120, 1, "silence_sec")
    vol = round(a.volume * 100)
    out += field(
        "Alarm volume", f'<input type="range" id="{p}-vol" min="10" max="100" step="5" value="{vol}" data-change="alarm-vol"><span class="u">{vol} %</span>', "Cannot be set to zero.", f"{p}-vol"
    )
    return f"<div>{out}</div>"


def install_alarm_actions(app: "WebApp") -> None:
    from .dom import actions

    def setp(**patch):
        e = app.engine
        e.update_settings(alarms=dataclasses.replace(e.settings.alarms, **patch))
        app.rerender()

    def on_alarm(el):
        v = as_float(el)
        name = data(el, "arg")
        if v is None:
            app.rerender(force=True)
            return
        lo, hi = float(el.min or -1e9), float(el.max or 1e9)
        v = min(hi, max(lo, v))
        setp(**{name: round(v) if name == "delay_breaths" else v})

    actions(
        alarm=on_alarm,
        **{
            "alarm-src": lambda el: setp(source=data(el, "arg")),
            "alarm-low-on": lambda el: setp(low_mm_hg=25 if el.checked else None),
            "alarm-vol": lambda el: setp(volume=float(el.value) / 100),
        },
    )


# ------------------------------------------------------------------ the monitor screen


TILE_IDS = ("etco2", "rr", "fico2", "flow", "peak", "vt", "mv")


def tile_html(tid: str, label: str, unit: str, value: str, big=False, stale=False, alarm=None, flash=False, limits=None, qualifier="", raw="") -> str:
    cls = f"tile{' big' if big else ''}{' stale' if stale else ''}{f' alarm-{alarm}' if alarm else ''}"
    lim = f'<span class="limits"><span class="hi">{esc(limits[0])}</span><span>{esc(limits[1])}</span></span>' if limits else ""
    qual = f'<div class="qual"><span>{esc(qualifier)}</span>{f"<span class=raw>{esc(raw)}</span>" if raw else ""}</div>' if (qualifier or raw) else ""
    return f'<div class="{cls}" data-testid="tile-{tid}"><div class="tlabel"><span><b>{label}</b><span class="unit">{esc(unit)}</span></span>{lim}</div><div class="val{" flash" if flash else ""}">{esc(value)}</div>{qual}</div>'


def wave_html(cid: str, label: str, unit: str) -> str:
    tag = '<span class="wtag" id="wtag" hidden></span>' if cid == "co2" else ""
    return f'<div class="wave" id="wave-{cid}"><div class="wlabel">{label}<span id="unit-{cid}">{unit}</span></div>{tag}<canvas id="bg-{cid}"></canvas><canvas id="fg-{cid}"></canvas></div>'


class MonitorView:
    def __init__(self, app: "WebApp") -> None:
        self.app = app
        self.sweeps: dict[str, Sweep] = {}
        self.trend: TrendChart | None = None
        self.resp: ResponseChart | None = None
        self.tab = "trend"
        self.drawer = False
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
            '<span class="trend-meta"><span id="trend-meta"></span><label>Window <select id="trend-window" data-change="trend-window" aria-label="Trend window">'
            '<option value="15">15 min</option><option value="30">30 min</option><option value="120">2 h</option><option value="0">All</option></select></label></span></div>'
            '<div class="trend-plot"><canvas id="trend-canvas"></canvas></div></section>'
        )
        return f'<div class="monitor-screen"><main class="monitor">{rows}<div id="overlay"></div></main>{trend}<nav class="softkeys" id="softkeys"></nav><div id="drawer"></div></div>'

    def mount(self) -> None:
        for cid, var in (("co2", "--co2"), ("flow", "--flow"), ("pneumo", "--pneumo")):
            self.sweeps[cid] = Sweep(byid(f"fg-{cid}"), byid(f"bg-{cid}"), var)
        self.trend = TrendChart(byid("trend-canvas"))
        self.resp = ResponseChart(byid("trend-canvas"))
        self._epoch = -1
        self._trend_key = None
        for i in TILE_IDS:
            set_html(f"t-{i}", "", force=True)

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

        co2_alarm = next((a for a in s.alarms if a.id in ("co2High", "co2Low") and a.state == "active"), None)
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
                limits=(lim(st.alarms.high_mm_hg), lim(st.alarms.low_mm_hg)),
                qualifier="no CO2 signal" if sc["co2"] else f"last breath left out ({n.rejected.split(':')[0]})" if n.rejected else f"median 3 br: {fmt(disp(n.etco2_median), digits)}",
                raw=raw("co2"),
            ),
            "rr": tile_html("rr", "RR", "/min", fmt(n.rr), alarm="high" if apnea else None, flash=bool(apnea and not apnea.acknowledged), qualifier="no breath detected" if n.rr != n.rr and s.status == "connected" else ""),
            "fico2": tile_html("fico2", "FiCO2", unit, sv(sc["co2"], disp(n.fico2), digits), stale=sc["co2"]),
            "flow": tile_html("flow", "Flow", "L/min", sv(sc["flow"], n.flow), stale=sc["flow"], raw=raw("flow")),
            "peak": tile_html("peak", "Peak insp", "L/min", sv(sc["flow"], n.peak_flow), stale=sc["flow"]),
            "vt": tile_html("vt", "VT", "mL", sv(sc["pneumo"], n.vt), stale=sc["pneumo"], raw=raw("pneumo"), qualifier="" if sc["pneumo"] else f"flow {fmt(n.pneumo_flow)} L/min"),
            "mv": tile_html("mv", "MV", "L/min", sv(sc["pneumo"], n.mv, 1), stale=sc["pneumo"]),
        }
        for k, h in tiles.items():
            set_html(f"t-{k}", h)

        set_html("overlay", self._overlay(s) if s.status in ("idle", "error") else "")
        self._softkeys(s, disp, digits)
        self._trend(s, disp, pct)
        if self.drawer:
            from .dom import editing_inside

            if not editing_inside("drawer"):
                set_html("drawer", self._drawer(st.alarms))
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
            + key("Alarm limits", f"high {fmt(disp(a.high_mm_hg), digits)}{low}", "drawer-open")
            + key("Settings", "calibration, connection", "open-settings")
        )
        if set_html("softkeys", html) and self.mark:
            el = byid("mark-label")
            if el is not None:
                el.focus()
                el.select()

    def _drawer(self, a: AlarmSettings) -> str:
        return (
            '<div class="drawer-backdrop" data-act="drawer-close"></div><aside class="drawer" aria-label="Alarm limits"><h2>Alarm limits</h2>'
            '<p class="note">Changes apply immediately and are saved in this browser.</p>'
            f'{alarm_form_html(a, "drawer")}<div class="row-actions" style="margin-top:auto">{btn("Test alarm", "test-alarm")}{btn("Done", "drawer-close", "btn primary")}</div></aside>'
        )

    def _trend(self, s: Snapshot, disp, pct: bool) -> None:
        st = s.settings
        e = self.app.engine
        sel = byid("trend-window")
        if str(sel.value) != f"{st.trend_minutes:g}":
            sel.value = f"{st.trend_minutes:g}"
        byid("tab-trend").setAttribute("aria-selected", "true" if self.tab == "trend" else "false")
        byid("tab-response").setAttribute("aria-selected", "true" if self.tab == "response" else "false")
        key = (int(s.now // 1000), s.trend_version, self.tab, st.trend_minutes, st.co2_unit, st.alarms.high_mm_hg, st.alarms.low_mm_hg, st.co2_scale_max_mm_hg, self.app.theme)
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
            # continuous CO2, without the stretches of breaths the noise screen left out
            tt, vv, join = e.trend_trace(t0)
            fin = [(q.t, disp(q.etco2)) for q in pts if math.isfinite(q.etco2)]
            self.trend.draw(
                t0,
                now_s,
                disp(st.co2_scale_max_mm_hg),
                disp(st.alarms.high_mm_hg),
                None if st.alarms.low_mm_hg is None else disp(st.alarms.low_mm_hg),
                [a for a, _ in fin],
                [b for _, b in fin],
                tt,
                [disp(v) for v in vv],
                join,
                [(m.t / 1000, m.label) for m in e.markers if m.t / 1000 >= t0],
                [(a / 1000, p) for a, p in e.alarm_starts if a / 1000 >= t0],
                pct,
            )
            span = f"{minutes:g} min" if minutes > 0 else "whole session"
            byid("trend-meta").textContent = f"{span} · one point per breath · {len(pts)} breaths{removed}"
        else:
            xy = sorted(((q.etco2, q.mv) for q in pts if math.isfinite(q.etco2) and math.isfinite(q.mv)), key=lambda a: a[0])
            xs, ys = [a for a, _ in xy], [b for _, b in xy]
            fit = linear_fit(xs, ys)
            self.resp.draw(xs, ys, fit)
            byid("trend-meta").textContent = f"MV (L/min) vs EtCO2 (mmHg) · slope {fmt(fit.slope, 2)} L/min/mmHg · r² {fmt(fit.r2, 2)} · n {len(xs)}{removed}" if fit else "MV vs EtCO2 · needs at least 3 breaths"


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
        },
    )
    app.hotkey_mark = mark
