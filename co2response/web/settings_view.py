"""Settings in the browser: Connection, Calibration, Alarms, Display & units,
Data & recordings, Diagnostics. Same screens and CSS as the original web app."""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime
from typing import TYPE_CHECKING

from .. import __version__
from .. import calibration_draft as cd
from .. import connection_form as cf
from ..calibration_draft import CalDraft
from ..channels import CHANNEL_IDS, validate_channels
from ..engine.link_test import overall, report_text
from ..engine.monitor import Snapshot
from ..forms import PROCESSING_FIELDS, SPO2_FIELDS, TEST_FIELDS
from ..sim.controls import SITUATION_BY_ID
from ..storage.library import STATUS_LABELS
from ..storage.report import build_report, report_pdf
from ..storage.session_file import breaths_csv, events_csv, parse_session_text
from .dom import actions, as_float, btn, byid, data, download, editing_inside, esc, field, num, number_field, read_files, seg, select, set_html, stat, toast, toggle
from .spec_form import install_spec_actions, spec_form_html
from .views import alarm_form_html, duration, fmt, hms, size_text, stamp

if TYPE_CHECKING:
    from .app import WebApp

TABS = [
    ("connection", "Connection"),
    ("calibration", "Calibration"),
    ("alarms", "Alarms"),
    ("display", "Display & units"),
    ("data", "Data & recordings"),
    ("diagnostics", "Diagnostics"),
]
LABEL = {"pass": "PASS", "warn": "CHECK", "fail": "FAIL", "info": "NOTE"}
#: the page ids of the simulator's quick values (see ``connection_form.SIM_FIELDS``)
SIM_IDS = {"rr": "sim-rr", "etco2": "sim-et", "vt_ml": "sim-vt"}


class CalCard(CalDraft):
    """One channel's calibration card. What it does is ``CalDraft`` (shared with the
    desktop window); this adds what only the page needs to remember."""

    def __init__(self, cid: str, saved: dict) -> None:
        super().__init__(cid, saved)
        self.method = "manual"
        self.capturing: set[str] = set()


class SettingsView:
    def __init__(self, app: "WebApp") -> None:
        self.app = app
        self.tab = "connection"
        self.version = 0
        self._key = None
        self.cards = {cid: CalCard(cid, app.engine.channels["channels"][cid]) for cid in CHANNEL_IDS}
        self.replay = {"id": None, "speed": 1, "use_cal": True}
        self.rate = 100
        self.check = {"seconds": 10, "job": None, "results": None, "ran": 0}
        self.confirm_del: str | None = None
        self.usage: tuple[float, float] | None = None
        self.persisted = False

    def bump(self) -> None:
        self.version += 1
        self.app.refresh()

    # ---------- shell

    def html(self) -> str:
        nav = "".join(f'<button class="navbtn" data-act="settings-tab" data-arg="{k}" id="nav-{k}">{esc(t)}</button>' for k, t in TABS)
        return (
            '<div class="settings-screen"><nav class="settings-nav" aria-label="Settings sections">'
            f'{btn("← Back to monitor", "show-monitor", "btn primary back")}{nav}'
            '<p class="note" style="margin-top:auto">Alarms keep running while you are on this screen.</p></nav>'
            '<div class="settings-body" id="settings-body"></div></div>'
        )

    def mount(self) -> None:
        self._key = None

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        for k, _ in TABS:
            el = byid(f"nav-{k}")
            if el is not None:
                if k == self.tab:
                    el.setAttribute("aria-current", "page")
                else:
                    el.removeAttribute("aria-current")
        page = getattr(self, f"_{self.tab}")
        key = (self.tab, self.version, page(s, key_only=True))
        if (key != self._key or force) and not editing_inside("settings-body"):
            self._key = key
            set_html("settings-body", page(s), force=True)
        live = getattr(self, f"_{self.tab}_live", None)
        if live:
            live(s)

    # ---------- connection

    def _connection(self, s: Snapshot, key_only: bool = False):
        from .platform import web_serial_supported

        st = s.settings
        sessions = cf.replayable(self.app.engine.library)
        if key_only:
            return (s.status, s.status_detail, s.source_kind, json.dumps(s.info), self.app.known_label, json.dumps(dataclasses.asdict(st.sim)), st.baud_rate, st.auto_reconnect, st.bridge_url, s.recording.library_version)
        out = f'<div><h2>Connection</h2><p class="lead">Status: <b>{esc(cf.status_text(s))}</b></p>'
        if s.status == "connected":
            stats = "".join(stat(k, v) for k, v in cf.device_info(s))
            rate = ""
            if cf.takes_rate(s):
                rate = field(
                    cf.RATE_LABEL,
                    num("rate", self.rate, "rate", 1, cf.RATE_MIN, cf.RATE_MAX) + '<span class="u">Hz</span>' + btn("Send", "rate-send", "btn small") + btn("Ask INFO", "ask-info", "btn small"),
                    cf.RATE_HINT,
                )
            out += f'<section class="card" style="margin-bottom:12px"><div class="card-head"><h3>Connected device</h3>{btn("Disconnect", "disconnect", "btn danger")}</div><div class="stats">{stats}</div>{rate}</section>'
        ws = web_serial_supported()
        known = self.app.known_label
        usb_note = (
            '<p class="note">Uses Web Serial. Works in desktop Chrome or Edge on https:// pages or localhost. The browser asks which port to use.</p>'
            if ws
            else '<p class="warn">This browser has no Web Serial. Use desktop Chrome or Edge, Python acquisition below, or the desktop app (python -m co2response).</p>'
        )
        usb = (
            f'<section class="card"><div class="card-head"><h3>Arduino over USB</h3></div>{usb_note}'
            + field("Baud rate", select("baud", st.baud_rate, [(b, str(b)) for b in cf.BAUDS], "baud"), cf.BAUD_HINT, "baud")
            + field(cf.RECONNECT_LABEL, toggle("autoreconnect", st.auto_reconnect, "autoreconnect"), cf.RECONNECT_HINT, "autoreconnect")
            + '<div class="row-actions">'
            + (btn(f"Reconnect {esc(known)}", "reconnect", "btn primary") if known else "")
            + btn("Choose another port…" if known else "Connect Arduino", "connect-serial", "btn" if known else "btn primary", disabled=not ws)
            + "</div></section>"
        )
        sim = st.sim
        simc = (
            '<section class="card"><div class="card-head"><h3>Simulator</h3><span class="meta">no hardware needed</span></div>'
            f'<p class="note">{esc(cf.SIM_NOTE)}'
            "The <b>Simulation</b> key on the monitor opens a panel that sets every value the monitor shows.</p>"
            + field(
                "Situation",
                select("sim-situation-pick", sim.preset if sim.preset in SITUATION_BY_ID else "normal", cf.situation_options(), "sim-situation-pick"),
                esc(cf.situation_hint(sim)),
                "sim-situation-pick",
            )
            + "".join(number_field(text, "sim", getattr(sim, key), unit, None, SIM_IDS[key], lo, hi, step, key) for key, text, unit, lo, hi, step in cf.SIM_FIELDS)
            + f'<div class="row-actions">{btn(cf.sim_button(s), "start-sim", "btn primary")}'
            f'{btn("Open the simulation panel", "sim-open-panel")}</div></section>'
        )
        chosen = self.replay["id"] if any(r.id == self.replay["id"] for r in sessions) else (sessions[0].id if sessions else None)
        self.replay["id"] = chosen
        rep = '<section class="card"><div class="card-head"><h3>Replay a saved session</h3></div>'
        if sessions:
            rep += field("Session", select("replay-pick", chosen, [(r.id, r.file_name) for r in sessions], "replay-pick"), None, "replay-pick")
        else:
            rep += '<p class="note">No saved sessions yet. Record one, import a file in Data & recordings, or replay a file directly.</p>'
        rep += field("Speed", seg("replay-speed", self.replay["speed"], cf.REPLAY_SPEEDS))
        rep += field(cf.FILE_CAL_LABEL, toggle("replay-cal", self.replay["use_cal"], "replay-cal"), cf.FILE_CAL_HINT, "replay-cal")
        rep += (
            f'<div class="row-actions">{btn("Start replay", "replay-start", "btn primary", disabled=not sessions)}'
            '<label class="btn" style="display:inline-flex;align-items:center">Replay a file…<input type="file" accept=".csv,text/csv" hidden data-change="replay-file"></label>'
            f'{btn("Replay the sample session", "replay-sample")}</div></section>'
        )
        bridge = (
            '<section class="card"><div class="card-head"><h3>Python acquisition</h3><span class="meta">other DAQs, Safari/Firefox</span></div>'
            '<p class="note">Python reads the hardware and sends the same data here: the Arduino, an NI or LabJack DAQ, or a simulated DAQ. '
            'Start it on this computer, for example <span class="mono">co2r-daq serve --driver nidaq --device Dev1</span> or '
            '<span class="mono">co2r-daq serve --driver arduino --port COM3</span>. See docs/python-daq.md. '
            'On an https:// page (GitHub Pages) the browser allows only a server on this computer; for one on another computer use the desktop app.</p>'
            + field(cf.BRIDGE_LABEL, f'<input id="bridge-url" type="url" class="wide" value="{esc(st.bridge_url)}" data-change="bridge-url">', "ws://localhost:8765 unless you changed --ws-port.", "bridge-url")
            + f'<div class="row-actions">{btn("Connect to Python", "connect-bridge", "btn primary")}</div></section>'
        )
        return out + f'<div class="cards">{usb}{simc}{rep}{bridge}</div></div>'

    # ---------- calibration

    def _calibration(self, s: Snapshot, key_only: bool = False):
        for cid, c in self.cards.items():
            c.follow(s.channels["channels"][cid])
        if key_only:
            return (s.status == "connected", s.using_file_calibration, tuple(c.saved_json for c in self.cards.values()))
        out = f'<div><h2>Calibration</h2><p class="lead">{esc(cd.LEAD)}</p>'
        if s.status != "connected":
            out += f'<p class="warn">{esc(cd.NO_LIVE)}</p>'
        if s.using_file_calibration:
            out += f'<p class="warn">{esc(cd.FILE_CAL)}</p>'
        out += (
            f'<div class="row-actions" style="margin-bottom:12px">{btn("Export calibration (JSON)", "cal-export")}'
            '<label class="btn" style="display:inline-flex;align-items:center">Import calibration…<input type="file" accept=".json,application/json" hidden data-change="cal-import"></label>'
            f'{btn("Reset all to defaults", "cal-reset", "btn danger")}<span class="note">Saved in this browser. Export a copy for your lab notebook.</span></div>'
        )
        return out + '<div class="cards">' + "".join(self._card(c, s) for c in self.cards.values()) + "</div></div>"

    def _card(self, c: CalCard, s: Snapshot) -> str:
        cid = c.cid
        meta = c.meta(lambda when: stamp(_iso_ms(when)))
        note = f'<p class="note">{esc(c.note())}</p>' if c.note() else ""
        live = f'<div class="live">{stat("Raw", "-- V", f"cal-raw-{cid}")}{stat("Preview (unsaved)" if c.dirty else "Calibrated", "--", f"cal-val-{cid}")}{stat("", "", f"cal-3-{cid}")}</div>'
        tabs = "".join(f'<button class="tab" role="tab" aria-selected="{"true" if c.method == m else "false"}" data-act="cal-method" data-arg="{cid}|{m}">{esc(t)}</button>' for m, t in cd.methods(cid))
        body = {"manual": self._manual, "zerospan": self._points, "fit": self._points, "syringe": self._syringe}[c.method](c)
        foot = (
            '<div class="row-actions" style="border-top:1px solid var(--border);padding-top:10px">'
            f'{btn("Save calibration", "cal-save", "btn primary", arg=cid, disabled=not c.dirty)}{btn("Discard changes", "cal-discard", arg=cid, disabled=not c.dirty)}'
            f'{btn("Load defaults", "cal-defaults", arg=cid)}{"<span class=dirty>Unsaved changes</span>" if c.dirty else ""}</div>'
        )
        return f'<section class="card {cid}"><div class="card-head"><h3>{cd.NAMES[cid]}</h3><span class="meta">{esc(meta)}</span></div>{note}{live}<div class="subtabs" role="tablist">{tabs}</div>{body}{foot}</section>'

    def _capture_btn(self, c: CalCard, target: str, text: str = "Capture") -> str:
        busy = target in c.capturing
        return btn(cd.CAPTURING if busy else text, "cal-capture", "btn small", arg=f"{c.cid}|{target}", disabled=busy, title=cd.CAPTURE_TIP)

    def _manual(self, c: CalCard) -> str:
        cid, d = c.cid, c.draft
        cal, u = d["calibration"], c.unit
        out = field("Calibration type", seg("cal-type", cal["type"], cd.TYPES, f"{cid}|"))
        if cal["type"] == "linear":
            out += number_field("Gain", "cal-num", cal["gain"], f"{u}/V", None, f"{cid}-gain", arg=f"{cid}|gain")
            out += number_field("Offset", "cal-num", cal["offset"], u, cd.LINEAR_HINT, f"{cid}-offset", arg=f"{cid}|offset")
        elif cal["type"] == "polynomial":
            out += field(
                cd.COEFFS_LABEL,
                f'<input id="{cid}-coeffs" type="text" class="wide" value="{esc(c.coeffs_text())}" data-change="cal-coeffs" data-arg="{cid}">',
                f"value = c0 + c1·V + c2·V² + …  (in {esc(u)})",
                f"{cid}-coeffs",
            )
        else:
            rows = "".join(
                f'<tr><td>{num("cal-pt", p[0], None, 0.0001, arg=f"{cid}|{i}|0", aria=f"Point {i + 1} volts")}</td><td>{num("cal-pt", p[1], None, 0.001, arg=f"{cid}|{i}|1", aria=f"Point {i + 1} value")}</td>'
                f'<td class="row-actions">{self._capture_btn(c, f"pt{i}", "Capture V")}{btn("Remove", "cal-pt-remove", "btn small", arg=f"{cid}|{i}", disabled=not c.can_remove_point())}</td></tr>'
                for i, p in enumerate(cal["points"])
            )
            out += (
                f'<div style="padding:8px 0"><table class="pts"><thead><tr><th>Volts</th><th>Value ({esc(u)})</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
                f'<div class="row-actions" style="margin-top:8px">{btn("Add point", "cal-pt-add", "btn small", arg=cid)}<span class="note">{esc(cd.TABLE_NOTE)}</span></div></div>'
            )
        out += number_field("Zero offset", "cal-zero", d["zeroOffset"], u, cd.ZERO_HINT, f"{cid}-zero", arg=cid)
        out += f'<div class="row-actions" style="padding:8px 0">{self._capture_btn(c, "zero", cd.zero_button(cid))}</div>'
        if cid == "pneumo":
            fm = c.flow_model()
            out += number_field("Flow constant k1", "cal-fm", fm["k1"], "L/min per Pa", None, "pneumo-k1", arg="k1")
            out += number_field("Quadratic term k2", "cal-fm", fm["k2"], "L/min per Pa²", cd.K2_HINT, "pneumo-k2", arg="k2")
        return f"<div>{out}</div>"

    def _points(self, c: CalCard) -> str:
        cid, mode, u = c.cid, c.method, c.unit
        pts = c.pts[mode]
        if mode == "zerospan":
            intro = '<ol class="steps">' + "".join(f"<li>{esc(x)}</li>" for x in cd.zerospan_steps(cid)) + "</ol>"
        else:
            intro = f'<p class="note">{esc(cd.FIT_INTRO)}</p>'
        rows = ""
        for i, p in enumerate(pts):
            rem = btn("Remove", "cal-ref-remove", "btn small", arg=f"{cid}|{i}") if c.can_remove_ref(mode) else ""
            lab = p["label"]
            ref_in = num("cal-ref", p["ref"], None, 0.001, arg=f"{cid}|{i}|ref", aria=f"{lab} reference")
            volts_in = num("cal-ref", p["volts"], None, 0.0001, arg=f"{cid}|{i}|volts", aria=f"{lab} volts")
            rows += f'<tr><td>{esc(c.point_name(mode, i))}</td><td>{ref_in}</td><td>{volts_in}</td><td class="row-actions">{self._capture_btn(c, f"{mode}{i}")}{rem}</td></tr>'
        acts = btn("Add point", "cal-ref-add", "btn small", arg=cid) if mode == "fit" else ""
        acts += "".join(btn(text, "cal-fit", "btn primary" if k == 0 else "btn", arg=f"{cid}|{deg}", disabled=not ok) for k, (deg, text, ok) in enumerate(c.fits(mode)))
        res = f'<p class="ok-text">{esc(c.result[mode])}</p>' if c.result[mode] else ""
        return (
            f'<div style="display:grid;gap:8px">{intro}<table class="pts"><thead><tr><th>Point</th><th>Reference ({esc(u)})</th><th>Volts</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="row-actions">{acts}</div>{res}</div>'
        )

    def _syringe(self, c: CalCard) -> str:
        out = '<div style="display:grid;gap:8px"><ol class="steps">' + "".join(f"<li>{esc(x)}</li>" for x in cd.SYRINGE_STEPS) + "</ol>"
        out += number_field("Syringe volume", "syr-vol", c.volume, "L", None, "syringe-vol", 0.1, 10, 0.1)
        running = c.stroke_running
        out += '<div class="row-actions">' + (btn("Stop", "syr-stop", "btn danger") if running else btn("Start stroke", "syr-start", "btn primary"))
        out += '<span class="note" id="syr-live"></span></div>' if running else "</div>"
        if c.stroke:
            out += '<div class="stats">' + "".join(stat(k, v) for k, v in c.stroke_stats()) + "</div>"
            _, err = c.new_k1()
            if err:
                out += f'<p class="err">{esc(err)}</p>'
            if c.can_apply_k1():
                out += f'<div class="row-actions">{btn(c.apply_k1_text(), "syr-apply", "btn primary")}<span class="note">Then Save calibration.</span></div>'
        return out + "</div>"

    def _calibration_live(self, s: Snapshot) -> None:
        for cid, c in self.cards.items():
            raw, value, third = c.live(s)
            _set(f"cal-raw-{cid}", raw[1])
            _set(f"cal-val-{cid}", value[1])
            el = byid(f"cal-3-{cid}")
            if el is not None:
                el.textContent = third[1]
                el.previousElementSibling.textContent = third[0]
            if c.stroke_running:
                _set("syr-live", c.stroke_live())

    # ---------- alarms and display

    def _alarms(self, s: Snapshot, key_only: bool = False):
        a = s.settings.alarms
        if key_only:
            return (json.dumps(dataclasses.asdict(a)), s.audio_armed)
        return (
            '<div style="max-width:720px"><h2>Alarms</h2><p class="lead">CO2 limits, delays and audio. Changes apply immediately. Technical alarms (no data, sensor at rail, device disconnected) are always on.</p>'
            '<p class="note">The warning limit tells the operator the end of the test is near; the stop limit says to turn the valve back to room air now.</p>'
            f'{alarm_form_html(a, "settings", s)}<div class="row-actions" style="margin-top:14px">{btn("Audio armed" if s.audio_armed else "Arm audio", "arm-audio", "btn" if s.audio_armed else "btn primary")}'
            f'{btn("Test alarm (high, then medium tone)", "test-alarm")}</div></div>'
        )

    def _display(self, s: Snapshot, key_only: bool = False):
        st = s.settings
        p = st.processing
        if key_only:
            lag = s.numbers.co2_lag_sec
            return (json.dumps(dataclasses.asdict(st)), round(lag, 2) if math.isfinite(lag) else None)
        return (
            '<div style="max-width:760px"><h2>Display & units</h2><p class="lead">How the monitor looks and how raw signals are turned into numbers.</p><h3 style="margin-top:10px">Screen</h3>'
            + field("Theme", seg("set-theme", st.theme, [("dark", "Dark"), ("light", "Light"), ("system", "Match system")]), "Dark is easier on the eyes in a dim room or overnight. Light suits a bright bench.")
            + field("CO2 units", seg("set-unit", st.co2_unit, [("mmHg", "mmHg"), ("%", "%")]), "Alarm limits are always set in mmHg.")
            + field("Sweep speed", select("set-num", st.sweep_sec, [(x, f"{x} s per screen") for x in (6, 12, 30, 60)], "sweep", arg="sweep_sec"), None, "sweep")
            + field("Capnogram scale", select("set-num", st.co2_scale_max_mm_hg, [(x, f"0–{x} mmHg") for x in (40, 60, 80, 100)], "co2scale", arg="co2_scale_max_mm_hg"), None, "co2scale")
            + field("Flow scale", select("set-num", st.flow_scale_lpm, [(x, f"±{x} L/min") for x in (30, 60, 100, 200)], "flowscale", arg="flow_scale_lpm"), None, "flowscale")
            + field("Show raw volts on the monitor", toggle("set-bool", st.show_raw_volts, "rawv", "show_raw_volts"), "Useful while calibrating; hide it for clinical use.", "rawv")
            + '<h3 style="margin-top:18px">Signal processing</h3>'
            + spec_form_html(PROCESSING_FIELDS, p, "proc", "proc", s)
            + '<h3 style="margin-top:18px">SpO2 (safety channel)</h3>'
            + spec_form_html(SPO2_FIELDS, st.spo2, "spo2", "spo2", s)
            + "</div>"
        )

    # ---------- data & recordings

    def _data(self, s: Snapshot, key_only: bool = False):
        st, r, e = s.settings, s.recording, self.app.engine
        recs = e.library.list()
        if key_only:
            return (r.library_version, r.active, int(s.now // 2000) if r.active else 0, st.study_code, st.auto_record, self.confirm_del, s.trend_version, len(e.markers), self.usage, self.persisted, bool(e.trend), dataclasses.astuple(st.test))
        out = (
            '<div style="max-width:1180px"><h2>Data & recordings</h2><p class="lead">Each recording is one CSV file with every sample, every breath, every alarm and every marker, ready for Excel, MATLAB, '
            "Python or R. While it records it is saved to this browser every second, so a crash loses at most a second. When you stop, the file is saved to your computer and also kept here. "
            "Never put a name, MRN or date of birth in the study code.</p><div class=\"cards\" style=\"margin-bottom:12px\">"
        )
        status = (
            f"<p>Recording for <b>{duration(s.now - r.started_at)}</b>, {size_text(r.bytes)} so far, saved to this browser every second.</p>"
            if r.active
            else '<p class="note">Press Record on the monitor or Start here. Stopping saves the file to your Downloads folder.</p>'
        )
        out += (
            f'<section class="card"><div class="card-head"><h3>Recording</h3><span class="meta">{esc(r.file_name if r.active else "Next file: " + e.next_file_name())}</span></div>'
            + field(
                "Study code",
                f'<input id="study" type="text" class="wide" maxlength="40" value="{esc(st.study_code)}" data-change="study"{" disabled" if r.active else ""}>',
                "Goes in the file name and header. Letters, numbers and dashes work best.",
                "study",
            )
            + field(
                "Start recording when the Arduino connects",
                toggle("set-bool", st.auto_record, "autorecord", "auto_record"),
                "Useful for overnight runs: connecting (or reconnecting after an unplug) starts a recording if none is running.",
                "autorecord",
            )
            + f'<h3 style="margin-top:12px">Test and subject</h3>{spec_form_html(TEST_FIELDS, st.test, "test", "test", s)}'
            + f'{status}<div class="row-actions">{btn("Stop and save file" if r.active else "Start recording", "record", "btn danger" if r.active else "btn primary")}</div></section>'
        )
        out += (
            '<section class="card"><div class="card-head"><h3>Import</h3><span class="meta">.csv session files, or LabChart .txt exports</span></div>'
            '<p class="note">Bring a session back to review it, replay it through the monitor, or export parts of it. Files made by older versions of this app are converted when you export them. A LabChart (PowerLab) text export with a flow and a CO2 channel is turned into a session: its breaths, response and report are worked out here.</p>'
            '<div class="row-actions"><label class="btn primary" style="display:inline-flex;align-items:center">Import session files…<input type="file" accept=".csv,.txt,text/csv,text/plain" multiple hidden data-change="import-files"></label></div></section></div>'
        )
        u = self.usage
        meta = f"{size_text(u[0])} used of {size_text(u[1])} available in this browser" if u else ""
        keep = "" if self.persisted else btn("Keep when disk is low", "persist", "btn small", style="margin-left:8px")
        out += f'<section class="card" style="margin-bottom:12px"><div class="card-head"><h3>Saved sessions</h3><span class="meta">{meta}{keep}</span></div>'
        if not recs:
            out += '<p class="note">No sessions yet. Record one, or import a file.</p>'
        else:
            rows = ""
            for rec in recs:
                recording = rec.status == "recording"
                if recording and r.active:
                    length = s.now - rec.started_at
                else:
                    dd = rec.stats.get("duration_s")
                    length = dd * 1000 if dd is not None else (rec.ended_at or rec.last_write_at) - rec.started_at
                menu = (
                    f'<details class="menu-wrap"><summary class="btn small">Export ▾</summary><span class="menu">{btn("Whole session (one .csv)", "export", arg=f"{rec.id}|session")}'
                    f'{btn("Breaths only (.csv)", "export", arg=f"{rec.id}|breaths")}{btn("Alarms & events only (.csv)", "export", arg=f"{rec.id}|events")}{btn("CO2 response report (.pdf)", "export", arg=f"{rec.id}|report")}</span></details>'
                )
                if self.confirm_del == rec.id:
                    dele = btn("Confirm delete", "delete-session", "btn small danger", arg=rec.id) + btn("Keep", "delete-cancel", "btn small")
                else:
                    dele = btn("Delete", "delete-ask", "btn small", arg=rec.id, disabled=recording and r.active)
                rows += (
                    f'<tr><td class="mono">{esc(rec.file_name)}</td><td>{esc(rec.study_code or "—")}</td><td>{stamp(rec.started_at)}</td><td>{duration(length)}</td><td>{size_text(rec.bytes)}</td>'
                    f'<td>{rec.stats.get("breaths", "—")}</td><td>{rec.stats.get("alarms", "—")}</td><td><span class="chip {rec.status}">{STATUS_LABELS.get(rec.status, rec.status)}</span></td>'
                    f'<td class="row-actions" style="flex-wrap:nowrap">{btn("Open", "open-review", "btn small", arg=rec.id)}{menu}{dele}</td></tr>'
                )
            out += (
                '<div style="overflow-x:auto"><table class="pts sessions"><thead><tr><th>File</th><th>Study</th><th>Started</th><th>Length</th><th>Size</th><th>Breaths</th><th>Alarms</th><th>Status</th><th></th></tr></thead>'
                f"<tbody>{rows}</tbody></table></div>"
            )
        out += '<p class="note">Sessions live in this browser on this computer. Clearing site data deletes them, so export anything you need to keep.</p></section>'
        marks = e.markers
        mk = (
            '<table class="pts"><thead><tr><th>Time</th><th>Label</th></tr></thead><tbody>'
            + "".join(f'<tr><td>{hms(m.t)}</td><td><input type="text" class="wide" aria-label="Marker {m.id} label" value="{esc(m.label)}" data-change="marker" data-arg="{m.id}"></td></tr>' for m in marks)
            + "</tbody></table>"
            if marks
            else '<p class="note">No markers yet.</p>'
        )
        out += (
            '<div class="cards"><section class="card"><div class="card-head"><h3>This session, without recording</h3></div>'
            '<p class="note">The monitor keeps the breath trend since this page was opened, even when nothing is being recorded.</p>'
            f'<div class="row-actions">{btn("Download breath trend (.csv)", "dl-trend", disabled=not e.trend)}{btn("Download session log (.txt)", "dl-log")}</div></section>'
            f'<section class="card"><div class="card-head"><h3>Event markers</h3><span class="meta">press M on the monitor to add one</span></div>{mk}</section></div></div>'
        )
        return out

    # ---------- diagnostics

    def _diagnostics(self, s: Snapshot, key_only: bool = False):
        chk = self.check
        job = chk["job"]
        running = job is not None and not job.done
        if job is not None and job.done and chk["results"] is not job.results:
            chk["results"], chk["ran"] = job.results, job.seconds
        if key_only:
            return (s.status == "connected", running, id(chk["results"]), chk["seconds"], s.i2c_scan)
        connected = s.status == "connected"
        out = (
            '<div><h2>Diagnostics</h2><p class="lead">Link quality and the raw stream, for checking wiring and firmware.</p>'
            '<section class="card" style="margin-bottom:12px"><div class="card-head"><h3>Hardware check</h3><span class="meta">USB link, ADC and each sensor\'s wiring</span></div>'
            '<p class="note">Run this after wiring or moving the setup, and before each study. It watches the live data and checks the handshake, sample rate, dropped and corrupt lines, and whether each '
            "channel is stuck, clipping or noisy. Leave the sensors connected and breathing through them (or the simulator running). 10 minutes is the acceptance test for a new setup (target: under 0.1 % dropped).</p>"
            f'<div class="row-actions">{seg("check-len", chk["seconds"], [(10, "10 s"), (60, "1 min"), (600, "10 min")])}'
            + (btn("Stop early", "check-stop", "btn danger", id="check-stop") if running else btn("Run hardware check", "check-run", "btn primary", disabled=not connected))
            + ("" if connected or running else '<span class="note">Connect the Arduino or start the simulator first.</span>')
            + (f'<span class="warn">Device I2C scan: {esc(s.i2c_scan)}</span>' if s.i2c_scan else "")
            + "</div>"
            + ('<div class="progress"><div id="check-progress" style="width:0%"></div></div>' if running else "")
        )
        res = chk["results"]
        if res and not running:
            v = overall(res)
            text = {"pass": "All checks passed.", "warn": "Usable, but check the items marked CHECK.", "fail": "Fix the items marked FAIL before using this setup."}[v]
            rows = "".join(f'<tr><td><span class="chip st-{r.status}">{LABEL[r.status]}</span></td><td>{esc(r.label)}</td><td>{esc(r.result)}</td><td class="note">{esc(r.fix or "")}</td></tr>' for r in res)
            out += (
                f'<p class="verdict {v}">{text}</p><table class="pts checks"><thead><tr><th></th><th>Check</th><th>Result</th><th>What to do</th></tr></thead><tbody>{rows}</tbody></table>'
                f'<div class="row-actions" style="margin-top:8px">{btn("Save report (.txt)", "check-save", "btn small")}</div>'
            )
        out += "</section>"
        out += '<div class="stats" id="diag-stats"></div><div class="cards"><section class="card"><h3>Last raw lines</h3><div class="loglist mono" id="diag-raw"></div></section>'
        out += '<section class="card"><h3>Session log</h3><div class="loglist" id="diag-log"></div></section></div></div>'
        return out

    def _diagnostics_live(self, s: Snapshot) -> None:
        job = self.check["job"]
        if job is not None:
            if job.done and self.check["results"] is not job.results:
                self.bump()
                return
            el = byid("check-progress")
            if el is not None:
                el.style.width = f"{job.progress * 100:.0f}%"
            _set("check-stop", f"Stop early ({round(job.progress * 100)} %)")
        stats = [
            ("Samples received", str(s.received)),
            ("Dropped (sequence gaps)", f"{s.dropped} ({fmt(s.drop_rate * 100, 3)} %)"),
            ("Bad lines (checksum/format)", str(s.bad_lines)),
            ("Measured rate", f"{fmt(s.measured_rate_hz, 1)} Hz"),
            ("Device rate", f"{s.rate_hz:g} Hz"),
            ("CO2 / flow / pneumo", " / ".join("stale" if s.stale[c] else "ok" for c in CHANNEL_IDS)),
        ]
        set_html("diag-stats", "".join(stat(k, v) for k, v in stats))
        lines = list(self.app.engine.recent_lines)[-25:]
        set_html("diag-raw", "".join(f"<div>{esc(x)}</div>" for x in lines) or "<div>Nothing received yet.</div>")
        log = self.app.engine.log
        set_html("diag-log", "".join(f'<div class="{x.level}">{hms(x.t)} · {esc(x.text)}</div>' for x in reversed(log[-200:])))


def _set(i: str, text: str) -> None:
    el = byid(i)
    if el is not None and el.textContent != text:
        el.textContent = text


def _iso_ms(s: str) -> float:
    from ..jsfmt import parse_iso_ms

    return parse_iso_ms(s)


# ------------------------------------------------------------------ actions


def install_settings_actions(app: "WebApp") -> None:
    sv = app.settings
    e = app.engine

    def card(el):
        return sv.cards[data(el, "arg").split("|")[0]]

    def parts(el):
        return data(el, "arg").split("|")

    def edit(change, *args, quiet: bool = False):
        """Make a change to a calibration card and redraw. What the change has to say
        (a message it returns, or the reason it refuses) is shown to the operator."""
        try:
            said = change(*args)
        except ValueError as ex:
            said, quiet = str(ex), False
        if isinstance(said, str) and said and not quiet:
            toast(said, 5000)
        sv.bump()

    # --- connection
    def rate(el):
        v = as_float(el)
        if v is not None:
            sv.rate = cf.clamp_rate(v)

    def sim(el):
        v = as_float(el)
        if v is not None:
            e.set_sim(**{data(el, "arg"): cf.clamp_sim(data(el, "arg"), v)})
        sv.bump()

    def replay_start(el):
        rid = sv.replay["id"]
        rec = e.library.get(rid) if rid else None
        if rec is None:
            return
        try:
            s = cf.for_replay(e.library.parse(rid))
        except (OSError, ValueError) as ex:
            toast(f"Could not replay {rec.file_name}: {ex}", 6000)
            return
        e.connect("replay", session=s, file_name=rec.file_name, speed=sv.replay["speed"], use_file_calibration=sv.replay["use_cal"])
        app.show("monitor")

    async def replay_file(el):
        for name, text in await read_files(el):
            try:
                s = cf.for_replay(parse_session_text(text))
            except ValueError as ex:
                toast(f"Could not replay {name}: {ex}", 6000)
                return
            e.connect("replay", session=s, file_name=name, speed=sv.replay["speed"], use_file_calibration=sv.replay["use_cal"])
            app.show("monitor")

    # --- calibration (what each key does is in ``calibration_draft.CalDraft``)
    def cal_method(el):
        cid, m = parts(el)
        sv.cards[cid].method = m
        sv.bump()

    def cal_type(el):
        cid, t = parts(el)
        edit(sv.cards[cid].switch_type, t)

    def cal_num(el):
        cid, key = parts(el)
        v = as_float(el)
        if v is None:
            sv.bump()  # not a number: put the saved value back
            return
        edit(sv.cards[cid].set_number, key, v)

    def cal_coeffs(el):
        edit(card(el).set_coeffs, str(el.value))

    def cal_pt(el):
        cid, i, j = parts(el)
        v = as_float(el)
        if v is not None:
            edit(sv.cards[cid].set_point, int(i), int(j), v)

    def cal_pt_remove(el):
        cid, i = parts(el)
        edit(sv.cards[cid].remove_point, int(i))

    def cal_pt_add(el):
        edit(card(el).add_point)

    def cal_zero(el):
        v = as_float(el)
        if v is not None:
            edit(sv.cards[data(el, "arg")].set_zero, v)

    def cal_fm(el):
        v = as_float(el)
        if v is not None:
            edit(sv.cards["pneumo"].set_flow, data(el, "arg"), v)

    def cal_capture(el):
        cid, target = parts(el)
        c = sv.cards[cid]
        c.capturing.add(target)

        def done(r, err):
            c.capturing.discard(target)
            if err:
                toast(err, 5000)
                sv.bump()
            elif target == "zero":
                edit(c.zero_now, r.volts)
            elif target.startswith("pt"):
                edit(c.capture_point, int(target[2:]), r.volts)
            else:
                mode = "zerospan" if target.startswith("zerospan") else "fit"
                edit(c.capture_ref, mode, int(target[len(mode) :]), r.volts, quiet=True)

        e.capture_average(cid, cd.CAPTURE_SEC, done)
        sv.bump()

    def cal_ref(el):
        cid, i, k = parts(el)
        c = sv.cards[cid]
        edit(c.set_ref, c.method, int(i), k, as_float(el))

    def cal_ref_add(el):
        edit(card(el).add_ref)

    def cal_ref_remove(el):
        cid, i = parts(el)
        edit(sv.cards[cid].remove_ref, int(i))

    def cal_fit(el):
        cid, deg = parts(el)
        c = sv.cards[cid]
        edit(c.fit, c.method, deg, quiet=True)  # the result is written under the points

    def syr_start(el):
        edit(sv.cards["pneumo"].stroke_start, e.on_sample)

    def syr_stop(el):
        if not sv.cards["pneumo"].stroke_stop():
            toast(cd.NO_STROKE, 5000)
        sv.bump()

    def syr_vol(el):
        v = as_float(el)
        if v is not None:
            sv.cards["pneumo"].set_volume(v)
        sv.bump()

    def syr_apply(el):
        edit(sv.cards["pneumo"].apply_k1)

    def cal_save(el):
        edit(sv.cards[data(el, "arg")].save, e)

    def cal_discard(el):
        edit(sv.cards[data(el, "arg")].discard)

    def cal_defaults(el):
        edit(sv.cards[data(el, "arg")].defaults)

    def cal_export(el):
        download(cd.export_name(), cd.export_text(e.channels), "application/json")

    async def cal_import(el):
        for name, text in await read_files(el):
            try:
                e.set_channels(validate_channels(json.loads(text)))
                toast(f"Imported {name}.")
            except ValueError as ex:
                toast(f"Could not import {name}: {ex}", 6000)
        sv.bump()

    def cal_reset(el):
        import js

        if js.window.confirm("Replace every channel's calibration with the defaults?"):
            e.reset_channels()
            toast(cd.RESET_DONE)
            sv.bump()

    # --- display
    def set_num(el):
        e.update_settings(**{data(el, "arg"): float(el.value)})
        sv.bump()

    # the processing and test rows come from co2response.forms (shared with the desktop window)
    install_spec_actions("proc", PROCESSING_FIELDS, lambda: e.settings.processing, lambda patch: e.update_settings(processing=dataclasses.replace(e.settings.processing, **patch)), sv.bump)
    install_spec_actions("spo2", SPO2_FIELDS, lambda: e.settings.spo2, lambda patch: e.update_settings(spo2=dataclasses.replace(e.settings.spo2, **patch)), sv.bump)
    install_spec_actions("test", TEST_FIELDS, lambda: e.settings.test, lambda patch: e.update_settings(test=dataclasses.replace(e.settings.test, **patch)), sv.bump)

    # --- data
    async def import_files(el):
        for name, text in await read_files(el):
            try:
                rec, _ = e.library.import_text(text, name)
                toast(f"Imported {name}: {rec.stats.get('breaths', 0)} breaths, {duration(rec.stats.get('duration_s', 0) * 1000)}.", 5000)
            except ValueError as ex:
                toast(f"Could not import {name}: {ex}", 7000)
        e.library_changed()
        app.storage.sync()
        sv.bump()

    def export(el):
        sid, what = parts(el)
        rec = e.library.get(sid)
        if rec is None:
            return
        try:
            if what == "session":
                download(rec.file_name, list(e.library.read_chunks(sid)))
                return
            parsed = e.library.parse(sid)
            base = rec.file_name[:-4] if rec.file_name.lower().endswith(".csv") else rec.file_name
            if what == "report":
                download(f"{base}_report.pdf", report_pdf(build_report(parsed, rec.file_name, app_version=__version__, generated_ms=e.clock())), "application/pdf")
                return
            download(f"{base}_{what}.csv", breaths_csv(parsed) if what == "breaths" else events_csv(parsed))
        except (OSError, ValueError) as ex:
            toast(f"Export failed: {ex}", 6000)

    def delete_session(el):
        rec = e.library.get(data(el, "arg"))
        e.library.delete(data(el, "arg"))
        sv.confirm_del = None
        e.library_changed()
        app.storage.sync()
        if rec:
            toast(f"Deleted {rec.file_name} from this browser.")
        sv.bump()

    async def persist(el):
        sv.persisted = await app.storage.persist()
        toast("The browser will keep these sessions." if sv.persisted else "The browser did not agree to keep them permanently; export important sessions.")
        sv.bump()

    # --- diagnostics
    def check_run(el):
        try:
            sv.check["job"] = e.run_hardware_check(sv.check["seconds"])
            sv.check["results"] = None
        except RuntimeError as ex:
            toast(str(ex))
        sv.bump()

    def check_save(el):
        if sv.check["results"]:
            download(f"co2r_hardware-check_{datetime.now():%Y-%m-%d-%H-%M-%S}.txt", report_text(sv.check["results"], sv.check["ran"]), "text/plain")

    actions(
        rate=rate,
        sim=sim,
        export=export,
        persist=persist,
        **{
            "settings-tab": lambda el: (setattr(sv, "tab", data(el, "arg")), sv.bump()),
            "rate-send": lambda el: [e.send(cmd) for cmd in cf.rate_commands(sv.rate)],
            "ask-info": lambda el: e.send("INFO"),
            "disconnect": lambda el: (e.disconnect(), sv.bump()),
            "baud": lambda el: (e.update_settings(baud_rate=int(el.value)), sv.bump()),
            "autoreconnect": lambda el: (e.update_settings(auto_reconnect=bool(el.checked)), sv.bump()),
            "sim-situation-pick": lambda el: (e.apply_situation(el.value, start=False), app.monitor.sim.bump(), sv.bump()),
            "sim-open-panel": lambda el: (setattr(app.monitor.sim, "open", True), app.show("monitor")),
            "replay-pick": lambda el: sv.replay.__setitem__("id", el.value),
            "replay-speed": lambda el: (sv.replay.__setitem__("speed", float(data(el, "arg"))), sv.bump()),
            "replay-cal": lambda el: sv.replay.__setitem__("use_cal", bool(el.checked)),
            "replay-start": replay_start,
            "replay-file": replay_file,
            "bridge-url": lambda el: e.update_settings(bridge_url=el.value.strip()),
            "cal-method": cal_method,
            "cal-type": cal_type,
            "cal-num": cal_num,
            "cal-coeffs": cal_coeffs,
            "cal-pt": cal_pt,
            "cal-pt-remove": cal_pt_remove,
            "cal-pt-add": cal_pt_add,
            "cal-zero": cal_zero,
            "cal-fm": cal_fm,
            "cal-capture": cal_capture,
            "cal-ref": cal_ref,
            "cal-ref-add": cal_ref_add,
            "cal-ref-remove": cal_ref_remove,
            "cal-fit": cal_fit,
            "cal-save": cal_save,
            "cal-discard": cal_discard,
            "cal-defaults": cal_defaults,
            "cal-export": cal_export,
            "cal-import": cal_import,
            "cal-reset": cal_reset,
            "syr-start": syr_start,
            "syr-stop": syr_stop,
            "syr-vol": syr_vol,
            "syr-apply": syr_apply,
            "set-theme": lambda el: (e.update_settings(theme=data(el, "arg")), app.apply_theme(), sv.bump()),
            "set-unit": lambda el: (e.update_settings(co2_unit=data(el, "arg")), sv.bump()),
            "set-num": set_num,
            "set-bool": lambda el: (e.update_settings(**{data(el, "arg"): bool(el.checked)}), sv.bump()),
            "study": lambda el: (e.update_settings(study_code=el.value.strip()), sv.bump()),
            "import-files": import_files,
            "delete-ask": lambda el: (setattr(sv, "confirm_del", data(el, "arg")), sv.bump()),
            "delete-cancel": lambda el: (setattr(sv, "confirm_del", None), sv.bump()),
            "delete-session": delete_session,
            "marker": lambda el: (e.rename_marker(int(data(el, "arg")), el.value), sv.bump()),
            "dl-trend": lambda el: download(e.next_file_name().replace(".csv", "_live-breaths.csv"), e.trend_csv()),
            "dl-log": lambda el: download(e.next_file_name().replace(".csv", "_log.txt"), e.log_text(), "text/plain"),
            "check-len": lambda el: (sv.check.__setitem__("seconds", int(float(data(el, "arg")))), sv.bump()),
            "check-run": check_run,
            "check-stop": lambda el: (sv.check["job"] and sv.check["job"].cancel(), sv.bump()),
            "check-save": check_save,
        },
    )
