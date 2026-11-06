"""Settings in the browser: Connection, Calibration, Alarms, Display & units,
Data & recordings, Diagnostics. Same screens and CSS as the original web app."""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..channels import CHANNEL_IDS, DEFAULT_CHANNELS, clone_channels, validate_channels
from ..engine.link_test import overall, report_text
from ..engine.monitor import Snapshot
from ..jsfmt import precision
from ..processing.calibration import apply_calibration, fit_polynomial, pneumo_flow, syringe_k1, two_point_linear
from ..processing.co2 import pct_to_mmhg
from ..sim.physiology import SCENARIOS
from ..storage.library import STATUS_LABELS
from ..storage.session_file import breaths_csv, events_csv, parse_session_text
from .dom import actions, as_float, btn, byid, data, download, editing_inside, esc, field, num, number_field, read_files, seg, select, set_html, stat, toast, toggle
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
NAMES = {"co2": "CO2 (capnograph)", "flow": "Flow sensor", "pneumo": "Pneumotachograph"}
ZERO_REF = {"co2": 0.04, "flow": 0.0, "pneumo": 0.0}
SPAN_DEFAULT = {
    "co2": ("Room air", 0.04, "Calibration gas", 5.0),
    "flow": ("No flow", 0.0, "Reference flow", 30.0),
    "pneumo": ("No flow (ΔP = 0)", 0.0, "Reference pressure", 200.0),
}
LABEL = {"pass": "PASS", "warn": "CHECK", "fail": "FAIL", "info": "NOTE"}


def digits_for(unit: str) -> int:
    return 3 if unit == "%" else 1 if unit == "Pa" else 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class CalCard:
    """One channel's calibration editor state (the draft and the capture tools)."""

    def __init__(self, cid: str, saved: dict) -> None:
        self.cid = cid
        self.saved_json = json.dumps(saved, sort_keys=True)
        self.draft = copy.deepcopy(saved)
        self.method = "manual"
        a, ra, b, rb = SPAN_DEFAULT[cid]
        nan = math.nan
        self.pts = {
            "zerospan": [{"label": a, "ref": ra, "volts": nan}, {"label": b, "ref": rb, "volts": nan}],
            "fit": [{"label": "Point 1", "ref": ra, "volts": nan}, {"label": "Point 2", "ref": rb / 2, "volts": nan}, {"label": "Point 3", "ref": rb, "volts": nan}],
        }
        self.result = {"zerospan": "", "fit": ""}
        self.capturing: set[str] = set()
        self.syr = {"volume": 3.0, "off": None, "acc": None, "res": None}

    @property
    def dirty(self) -> bool:
        return json.dumps(self.draft, sort_keys=True) != self.saved_json

    def flow_model(self) -> dict:
        return self.draft.get("flowModel") or {"k1": 0.2, "k2": 0}


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
        self.measuring_lag = False
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
        sessions = [r for r in self.app.engine.library.list() if r.status != "recording"]
        if key_only:
            return (s.status, s.status_detail, s.source_kind, json.dumps(s.info), self.app.known_label, json.dumps(dataclasses.asdict(st.sim)), st.baud_rate, st.auto_reconnect, st.bridge_url, s.recording.library_version)
        conn = {"connected": f"connected to {esc(s.source_label)}", "connecting": "connecting…", "error": f"lost ({esc(s.status_detail)})"}.get(s.status, "not connected")
        out = f'<div><h2>Connection</h2><p class="lead">Status: <b>{conn}</b></p>'
        if s.status == "connected":
            stats = "".join(stat(k, v) for k, v in (s.info or {"INFO": "waiting for reply"}).items())
            rate = ""
            if s.source_kind in ("serial", "bridge", "simulator"):
                rate = field(
                    "Sample rate per channel",
                    num("rate", self.rate, "rate", 1, 1, 200) + '<span class="u">Hz</span>' + btn("Send", "rate-send", "btn small") + btn("Ask INFO", "ask-info", "btn small"),
                    "Sends RATE to the device (1–200 Hz). 100 Hz is enough for breathing waveforms.",
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
            + field("Baud rate", select("baud", st.baud_rate, [(b, str(b)) for b in (9600, 57600, 115200, 230400, 250000, 500000)], "baud"), "Must match BAUD_RATE in firmware/CO2Response/config.h.", "baud")
            + field(
                "Reconnect when plugged back in",
                toggle("autoreconnect", st.auto_reconnect, "autoreconnect"),
                "If the USB cable comes out, the device-disconnected alarm sounds; plugging the same Arduino back in reconnects without a click, and recording continues.",
                "autoreconnect",
            )
            + '<div class="row-actions">'
            + (btn(f"Reconnect {esc(known)}", "reconnect", "btn primary") if known else "")
            + btn("Choose another port…" if known else "Connect Arduino", "connect-serial", "btn" if known else "btn primary", disabled=not ws)
            + "</div></section>"
        )
        sim = st.sim
        simc = (
            '<section class="card"><div class="card-head"><h3>Simulator</h3><span class="meta">no hardware needed</span></div>'
            '<p class="note">Generates realistic capnogram, flow and pneumotach signals through the same protocol, parser and processing as the Arduino.</p>'
            + field("Scenario", select("sim-scenario", sim.scenario, SCENARIOS, "scenario"), None, "scenario")
            + number_field("Respiratory rate", "sim", sim.rr, "/min", None, "sim-rr", 4, 60, 1, "rr")
            + number_field("Baseline EtCO2", "sim", sim.etco2, "mmHg", None, "sim-et", 10, 80, 1, "etco2")
            + number_field("Tidal volume", "sim", sim.vt_ml, "mL", None, "sim-vt", 100, 1500, 10, "vt_ml")
            + f'<div class="row-actions">{btn("Restart simulator" if s.source_kind == "simulator" else "Start simulator", "start-sim", "btn primary")}</div></section>'
        )
        chosen = self.replay["id"] if any(r.id == self.replay["id"] for r in sessions) else (sessions[0].id if sessions else None)
        self.replay["id"] = chosen
        rep = '<section class="card"><div class="card-head"><h3>Replay a saved session</h3></div>'
        if sessions:
            rep += field("Session", select("replay-pick", chosen, [(r.id, r.file_name) for r in sessions], "replay-pick"), None, "replay-pick")
        else:
            rep += '<p class="note">No saved sessions yet. Record one, import a file in Data & recordings, or replay a file directly.</p>'
        rep += field("Speed", seg("replay-speed", self.replay["speed"], [(1, "1×"), (2, "2×"), (5, "5×"), (10, "10×")]))
        rep += field(
            "Use the calibration saved in the file",
            toggle("replay-cal", self.replay["use_cal"], "replay-cal"),
            "On: replays exactly as recorded. Off: re-processes the raw volts with the current calibration.",
            "replay-cal",
        )
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
            + field("Server address", f'<input id="bridge-url" type="url" class="wide" value="{esc(st.bridge_url)}" data-change="bridge-url">', "ws://localhost:8765 unless you changed --ws-port.", "bridge-url")
            + f'<div class="row-actions">{btn("Connect to Python", "connect-bridge", "btn primary")}</div></section>'
        )
        return out + f'<div class="cards">{usb}{simc}{rep}{bridge}</div></div>'

    # ---------- calibration

    def _calibration(self, s: Snapshot, key_only: bool = False):
        for cid, c in self.cards.items():
            sj = json.dumps(s.channels["channels"][cid], sort_keys=True)
            if sj != c.saved_json:
                c.saved_json = sj
                c.draft = copy.deepcopy(s.channels["channels"][cid])
        if key_only:
            return (s.status == "connected", s.using_file_calibration, tuple(c.saved_json for c in self.cards.values()))
        out = (
            "<div><h2>Calibration</h2><p class=\"lead\">Each channel turns the ADC voltage into a reading. Type the numbers in by hand, or capture live readings against references "
            "(room air and calibration gas, a reference flowmeter, a 3 L syringe). The preview updates as you edit; nothing changes on the monitor until you press Save.</p>"
        )
        if s.status != "connected":
            out += '<p class="warn">No live data. You can still enter calibrations by hand; capturing readings needs a connected device or the simulator.</p>'
        if s.using_file_calibration:
            out += '<p class="warn">A replay is running with the calibration stored in its file. Changes saved here apply after the replay ends.</p>'
        out += (
            f'<div class="row-actions" style="margin-bottom:12px">{btn("Export calibration (JSON)", "cal-export")}'
            '<label class="btn" style="display:inline-flex;align-items:center">Import calibration…<input type="file" accept=".json,application/json" hidden data-change="cal-import"></label>'
            f'{btn("Reset all to defaults", "cal-reset", "btn danger")}<span class="note">Saved in this browser. Export a copy for your lab notebook.</span></div>'
        )
        return out + '<div class="cards">' + "".join(self._card(c, s) for c in self.cards.values()) + "</div></div>"

    def _card(self, c: CalCard, s: Snapshot) -> str:
        cid, d = c.cid, c.draft
        saved = json.loads(c.saved_json)
        when = saved.get("calibratedAt")
        meta = f"CH{int(d['index'])} · " + (f"saved {stamp(_iso_ms(when))}" if when else "default (not yet calibrated)")
        note = f'<p class="note">{esc(saved.get("note", ""))}</p>' if saved.get("note") and not when else ""
        live = f'<div class="live">{stat("Raw", "-- V", f"cal-raw-{cid}")}{stat("Preview (unsaved)" if c.dirty else "Calibrated", "--", f"cal-val-{cid}")}{stat("", "", f"cal-3-{cid}")}</div>'
        methods = [("manual", "Enter values"), ("zerospan", "Zero & span" if cid == "co2" else "Two-point"), ("fit", "Multi-point fit")] + ([("syringe", "3 L syringe")] if cid == "pneumo" else [])
        tabs = "".join(f'<button class="tab" role="tab" aria-selected="{"true" if c.method == m else "false"}" data-act="cal-method" data-arg="{cid}|{m}">{t}</button>' for m, t in methods)
        body = {"manual": self._manual, "zerospan": self._points, "fit": self._points, "syringe": self._syringe}[c.method](c)
        foot = (
            '<div class="row-actions" style="border-top:1px solid var(--border);padding-top:10px">'
            f'{btn("Save calibration", "cal-save", "btn primary", arg=cid, disabled=not c.dirty)}{btn("Discard changes", "cal-discard", arg=cid, disabled=not c.dirty)}'
            f'{btn("Load defaults", "cal-defaults", arg=cid)}{"<span class=dirty>Unsaved changes</span>" if c.dirty else ""}</div>'
        )
        return f'<section class="card {cid}"><div class="card-head"><h3>{NAMES[cid]}</h3><span class="meta">{esc(meta)}</span></div>{note}{live}<div class="subtabs" role="tablist">{tabs}</div>{body}{foot}</section>'

    def _capture_btn(self, c: CalCard, target: str, text: str = "Capture") -> str:
        busy = target in c.capturing
        return btn("Capturing 2 s…" if busy else text, "cal-capture", "btn small", arg=f"{c.cid}|{target}", disabled=busy, title="Average the live voltage for 2 seconds")

    def _manual(self, c: CalCard) -> str:
        cid, d = c.cid, c.draft
        cal, u = d["calibration"], d.get("unit", "")
        out = field("Calibration type", seg("cal-type", cal["type"], [("linear", "Linear"), ("polynomial", "Polynomial"), ("table", "Lookup table")], f"{cid}|"))
        if cal["type"] == "linear":
            out += number_field("Gain", "cal-num", cal["gain"], f"{u}/V", None, f"{cid}-gain", arg=f"{cid}|gain")
            out += number_field("Offset", "cal-num", cal["offset"], u, "value = gain × volts + offset", f"{cid}-offset", arg=f"{cid}|offset")
        elif cal["type"] == "polynomial":
            out += field(
                "Coefficients c0, c1, c2, …",
                f'<input id="{cid}-coeffs" type="text" class="wide" value="{esc(", ".join(f"{x:g}" for x in cal["coeffs"]))}" data-change="cal-coeffs" data-arg="{cid}">',
                f"value = c0 + c1·V + c2·V² + …  (in {esc(u)})",
                f"{cid}-coeffs",
            )
        else:
            rows = "".join(
                f'<tr><td>{num("cal-pt", p[0], None, 0.0001, arg=f"{cid}|{i}|0", aria=f"Point {i + 1} volts")}</td><td>{num("cal-pt", p[1], None, 0.001, arg=f"{cid}|{i}|1", aria=f"Point {i + 1} value")}</td>'
                f'<td class="row-actions">{self._capture_btn(c, f"pt{i}", "Capture V")}{btn("Remove", "cal-pt-remove", "btn small", arg=f"{cid}|{i}", disabled=len(cal["points"]) <= 2)}</td></tr>'
                for i, p in enumerate(cal["points"])
            )
            out += (
                f'<div style="padding:8px 0"><table class="pts"><thead><tr><th>Volts</th><th>Value ({esc(u)})</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
                f'<div class="row-actions" style="margin-top:8px">{btn("Add point", "cal-pt-add", "btn small", arg=cid)}<span class="note">Values between points are interpolated; outside the table the end segments are extended.</span></div></div>'
            )
        out += number_field("Zero offset", "cal-zero", d["zeroOffset"], u, "Subtracted after the calibration. Set it by hand or with the button below.", f"{cid}-zero", arg=cid)
        out += f'<div class="row-actions" style="padding:8px 0">{self._capture_btn(c, "zero", "Zero now on room air (0.04 %)" if cid == "co2" else "Zero now (no flow)")}</div>'
        if cid == "pneumo":
            fm = c.flow_model()
            out += number_field("Flow constant k1", "cal-fm", fm["k1"], "L/min per Pa", None, "pneumo-k1", arg="k1")
            out += number_field("Quadratic term k2", "cal-fm", fm["k2"], "L/min per Pa²", "flow = k1·ΔP + k2·ΔP·|ΔP|. Leave k2 at 0 for a linear (Fleisch-type) pneumotach.", "pneumo-k2", arg="k2")
        return f"<div>{out}</div>"

    def _points(self, c: CalCard) -> str:
        cid, mode, u = c.cid, c.method, c.draft.get("unit", "")
        pts = c.pts[mode]
        if mode == "zerospan":
            if cid == "co2":
                steps = [
                    "Flow room air through the sample line, wait for the reading to settle, then capture point A.",
                    "Flow the calibration gas (e.g. 5 % CO2), wait for it to settle, then capture point B. Enter the gas's certified value.",
                ]
            else:
                ref = "flow (reference flowmeter)" if cid == "flow" else "pressure (manometer)"
                steps = ["With no flow through the sensor, capture point A.", f"Apply a known {ref} and capture point B. Enter the reference value."]
            steps.append("Press Compute line, check the preview at the top of this card, then Save.")
            intro = '<ol class="steps">' + "".join(f"<li>{x}</li>" for x in steps) + "</ol>"
        else:
            intro = '<p class="note">Capture three or more readings across the range you will measure, then fit a line, a quadratic, or use the points as a lookup table. You can also type volts in by hand.</p>'
        rows = ""
        for i, p in enumerate(pts):
            name = f"{'AB'[i]} · {p['label']}" if mode == "zerospan" else p["label"]
            rem = btn("Remove", "cal-ref-remove", "btn small", arg=f"{cid}|{i}") if mode == "fit" and len(pts) > 2 else ""
            lab = p["label"]
            ref_in = num("cal-ref", p["ref"], None, 0.001, arg=f"{cid}|{i}|ref", aria=f"{lab} reference")
            volts_in = num("cal-ref", p["volts"], None, 0.0001, arg=f"{cid}|{i}|volts", aria=f"{lab} volts")
            rows += f'<tr><td>{esc(name)}</td><td>{ref_in}</td><td>{volts_in}</td><td class="row-actions">{self._capture_btn(c, f"{mode}{i}")}{rem}</td></tr>'
        ready = len([p for p in pts if math.isfinite(p["volts"]) and math.isfinite(p["ref"])])
        if mode == "zerospan":
            acts = btn("Compute line", "cal-fit", "btn primary", arg=f"{cid}|1", disabled=ready < 2)
        else:
            acts = (
                btn("Add point", "cal-ref-add", "btn small", arg=cid)
                + btn("Fit line", "cal-fit", "btn primary", arg=f"{cid}|1", disabled=ready < 2)
                + btn("Fit quadratic", "cal-fit", arg=f"{cid}|2", disabled=ready < 3)
                + btn("Use as lookup table", "cal-fit", arg=f"{cid}|table", disabled=ready < 2)
            )
        res = f'<p class="ok-text">{esc(c.result[mode])}</p>' if c.result[mode] else ""
        return (
            f'<div style="display:grid;gap:8px">{intro}<table class="pts"><thead><tr><th>Point</th><th>Reference ({esc(u)})</th><th>Volts</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="row-actions">{acts}</div>{res}</div>'
        )

    def _syringe(self, c: CalCard) -> str:
        sy = c.syr
        steps = [
            "Zero the pneumotach with no flow (Enter values → Zero now), and save.",
            "Connect the calibration syringe with the plunger fully out.",
            "Press Start stroke, push the plunger all the way in with one smooth stroke (1–2 s), then press Stop.",
            "Check the measured volume. Apply the new k1, then push again to confirm it reads the syringe volume.",
        ]
        out = '<div style="display:grid;gap:8px"><ol class="steps">' + "".join(f"<li>{x}</li>" for x in steps) + "</ol>"
        out += number_field("Syringe volume", "syr-vol", sy["volume"], "L", None, "syringe-vol", 0.1, 10, 0.1)
        running = sy["off"] is not None
        out += '<div class="row-actions">' + (btn("Stop", "syr-stop", "btn danger") if running else btn("Start stroke", "syr-start", "btn primary"))
        out += '<span class="note" id="syr-live"></span></div>' if running else "</div>"
        res = sy["res"]
        if res:
            a1, a2, secs = res
            fm = c.flow_model()
            vol = sy["volume"]
            measured = abs(fm["k1"] * a1 + fm["k2"] * a2) / 60
            try:
                k1, err = syringe_k1(a1, a2, vol, fm["k2"]), ""
            except ValueError as e:
                k1, err = math.nan, str(e)
            out += '<div class="stats">' + stat("∫ΔP dt", f"{a1:.1f} Pa·s") + stat("Stroke time", f"{secs:.2f} s")
            out += stat("Volume with current k1", f"{fmt(measured, 3)} L ({fmt((measured - vol) / vol * 100, 1)} %)") + stat("New k1", f"{k1:.5g}" if math.isfinite(k1) else "--") + "</div>"
            if err:
                out += f'<p class="err">{esc(err)}</p>'
            if math.isfinite(k1) and k1 > 0:
                out += f'<div class="row-actions">{btn(f"Apply k1 = {k1:.5g}", "syr-apply", "btn primary", arg=f"{k1:.6g}")}<span class="note">Then Save calibration.</span></div>'
        return out + "</div>"

    def _calibration_live(self, s: Snapshot) -> None:
        for cid, c in self.cards.items():
            i = int(c.draft["index"])
            v = s.numbers.volts[i] if i < len(s.numbers.volts) else math.nan
            d = c.draft
            u = d.get("unit", "")
            value = apply_calibration(d["calibration"], v) - d["zeroOffset"] if math.isfinite(v) else math.nan
            _set(f"cal-raw-{cid}", f"{v:.4f} V" if math.isfinite(v) else "-- V")
            _set(f"cal-val-{cid}", f"{fmt(value, digits_for(u))} {u}")
            el = byid(f"cal-3-{cid}")
            if el is not None:
                if cid == "co2":
                    k, val = "As mmHg", f"{fmt(pct_to_mmhg(value, s.settings.processing.barometric_mm_hg, s.settings.processing.water_vapor), 1)} mmHg"
                elif cid == "pneumo":
                    k, val = "Flow", f"{fmt(pneumo_flow(c.flow_model(), value), 1)} L/min"
                else:
                    sv = json.loads(c.saved_json)
                    k, val = "Saved calibration", f"{fmt(apply_calibration(sv['calibration'], v) - sv['zeroOffset'] if math.isfinite(v) else math.nan, digits_for(u))} {u}"
                el.textContent = val
                el.previousElementSibling.textContent = k
            if c.syr["off"] is not None and c.syr["acc"]:
                _set("syr-live", f"Integrating… ∫ΔP dt = {c.syr['acc']['a1']:.1f} Pa·s")

    # ---------- alarms and display

    def _alarms(self, s: Snapshot, key_only: bool = False):
        a = s.settings.alarms
        if key_only:
            return (json.dumps(dataclasses.asdict(a)), s.audio_armed)
        return (
            '<div style="max-width:720px"><h2>Alarms</h2><p class="lead">CO2 limits, delays and audio. Changes apply immediately. Technical alarms (no data, sensor at rail, device disconnected) are always on.</p>'
            f'{alarm_form_html(a, "settings")}<div class="row-actions" style="margin-top:14px">{btn("Audio armed" if s.audio_armed else "Arm audio", "arm-audio", "btn" if s.audio_armed else "btn primary")}'
            f'{btn("Test alarm (high, then medium tone)", "test-alarm")}</div></div>'
        )

    def _display(self, s: Snapshot, key_only: bool = False):
        st = s.settings
        p = st.processing
        if key_only:
            return (json.dumps(dataclasses.asdict(st)), self.measuring_lag)
        return (
            '<div style="max-width:760px"><h2>Display & units</h2><p class="lead">How the monitor looks and how raw signals are turned into numbers.</p><h3 style="margin-top:10px">Screen</h3>'
            + field("Theme", seg("set-theme", st.theme, [("dark", "Dark"), ("light", "Light"), ("system", "Match system")]), "Dark is easier on the eyes in a dim room or overnight. Light suits a bright bench.")
            + field("CO2 units", seg("set-unit", st.co2_unit, [("mmHg", "mmHg"), ("%", "%")]), "Alarm limits are always set in mmHg.")
            + field("Sweep speed", select("set-num", st.sweep_sec, [(x, f"{x} s per screen") for x in (6, 12, 30, 60)], "sweep", arg="sweep_sec"), None, "sweep")
            + field("Capnogram scale", select("set-num", st.co2_scale_max_mm_hg, [(x, f"0–{x} mmHg") for x in (40, 60, 80, 100)], "co2scale", arg="co2_scale_max_mm_hg"), None, "co2scale")
            + field("Flow scale", select("set-num", st.flow_scale_lpm, [(x, f"±{x} L/min") for x in (30, 60, 100, 200)], "flowscale", arg="flow_scale_lpm"), None, "flowscale")
            + field("Show raw volts on the monitor", toggle("set-bool", st.show_raw_volts, "rawv", "show_raw_volts"), "Useful while calibrating; hide it for clinical use.", "rawv")
            + '<h3 style="margin-top:18px">Signal processing</h3>'
            + number_field("Barometric pressure", "proc", p.barometric_mm_hg, "mmHg", "Used to convert % CO2 to mmHg. Pittsburgh is typically about 735 mmHg; enter the lab's reading for accuracy.", "pb", 500, 800, 1, "barometric_mm_hg")
            + field("Water-vapor correction (BTPS)", toggle("proc-bool", p.water_vapor, "wv", "water_vapor"), "Subtracts 47 mmHg before converting % to mmHg.", "wv")
            + field("Low-pass filter", toggle("proc-bool", p.filter_on, "filt", "filter_on"), "2nd-order Butterworth on every channel.", "filt")
            + (number_field("Filter cutoff", "proc", p.filter_hz, "Hz", None, "filthz", 1, 40, 0.5, "filter_hz") if p.filter_on else "")
            + number_field("Breath detection deadband", "proc-db", round(p.deadband_lpm / 60, 3), "L/s", "Flow must pass ± this to count as a new inspiration. Raise it if noise is counted as breaths.", "deadband", 0.005, 0.5, 0.005)
            + number_field("Minimum breath length", "proc", p.min_breath_sec, "s", None, "minbreath", 0.3, 5, 0.1, "min_breath_sec")
            + number_field(
                "CO2 delay behind flow", "proc", p.co2_delay_ms, "ms",
                "How long the capnograph's CO2 lags the pneumotach flow (sample-line transport plus sensor response). CO2 is shifted back by this before breaths are cut, so each breath gets its own EtCO2 and FiCO2. "
                "A sidestream capnograph usually needs 1000–3000 ms; a mainstream sensor much less. Measure it with normal breathing.",
                "co2lag", 0, 6000, 50, "co2_delay_ms",
            )
            + f'<div class="row-actions" style="margin:4px 0 10px">{btn("Measuring… keep breathing normally" if self.measuring_lag else "Measure from 20 s of breathing", "measure-lag", "btn small")}</div>'
            + field("Remove spikes (Hampel filter)", toggle("proc-bool", p.despike, "despike", "despike"), 'Replaces single glitchy samples with the median of their neighbours before anything is calculated. Keeps real fast changes such as the capnogram upstroke. Adds 30 ms of delay.', "despike")
            + field("Leave noisy breaths out", toggle("proc-bool", p.reject_bad_breaths, "reject", "reject_bad_breaths"), 'Each breath is checked before it is used: too noisy (signal-to-noise below 8), a partial breath with no alveolar plateau, an impossible value, or an isolated EtCO2 jump. Those breaths are left out of the numbers, the EtCO2 trend, the response plot and the EtCO2 alarms, but stay in the recording, marked. A run of them raises a technical alarm.', "reject")
            + '<h3 style="margin-top:18px">Rebreathing test</h3>'
            + number_field("Breaths left out at the start", "proc", p.response_skip_breaths, "breaths", "The deep breaths the subject is coached to take when the valve turns to the bag. They are left out of the MV-against-EtCO2 slope.", "skipb", 0, 20, 1, "response_skip_breaths")
            + '<p class="note">The end-test CO2 limit, the warning before it and the time limit are under Alarms.</p>'
            + "</div>"
        )

    # ---------- data & recordings

    def _data(self, s: Snapshot, key_only: bool = False):
        st, r, e = s.settings, s.recording, self.app.engine
        recs = e.library.list()
        if key_only:
            return (r.library_version, r.active, int(s.now // 2000) if r.active else 0, st.study_code, st.auto_record, self.confirm_del, s.trend_version, len(e.markers), self.usage, self.persisted, bool(e.trend))
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
            + f'{status}<div class="row-actions">{btn("Stop and save file" if r.active else "Start recording", "record", "btn danger" if r.active else "btn primary")}</div></section>'
        )
        out += (
            '<section class="card"><div class="card-head"><h3>Import</h3><span class="meta">.csv session files, v1 or v2</span></div>'
            '<p class="note">Bring a session back to review it, replay it through the monitor, or export parts of it. Files made by older versions of this app are converted when you export them.</p>'
            '<div class="row-actions"><label class="btn primary" style="display:inline-flex;align-items:center">Import session files…<input type="file" accept=".csv,text/csv" multiple hidden data-change="import-files"></label></div></section></div>'
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
                    f'{btn("Breaths only (.csv)", "export", arg=f"{rec.id}|breaths")}{btn("Alarms & events only (.csv)", "export", arg=f"{rec.id}|events")}</span></details>'
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

    def redraft(c: CalCard, **patch):
        c.draft = {**c.draft, **copy.deepcopy(patch)}
        sv.bump()

    # --- connection
    def rate(el):
        v = as_float(el)
        if v is not None:
            sv.rate = round(min(200, max(1, v)))

    def sim(el):
        v = as_float(el)
        if v is not None:
            e.set_sim(**{data(el, "arg"): min(float(el.max), max(float(el.min), v))})
        sv.bump()

    def replay_start(el):
        rid = sv.replay["id"]
        rec = e.library.get(rid) if rid else None
        if rec is None:
            return
        try:
            s = e.library.parse(rid)
            if not s.n:
                raise ValueError("it has no samples")
        except (OSError, ValueError) as ex:
            toast(f"Could not replay {rec.file_name}: {ex}", 6000)
            return
        e.connect("replay", session=s, file_name=rec.file_name, speed=sv.replay["speed"], use_file_calibration=sv.replay["use_cal"])
        app.show("monitor")

    async def replay_file(el):
        for name, text in await read_files(el):
            try:
                s = parse_session_text(text)
                if not s.n:
                    raise ValueError("it has no samples")
            except ValueError as ex:
                toast(f"Could not replay {name}: {ex}", 6000)
                return
            e.connect("replay", session=s, file_name=name, speed=sv.replay["speed"], use_file_calibration=sv.replay["use_cal"])
            app.show("monitor")

    # --- calibration
    def cal_method(el):
        cid, m = parts(el)
        sv.cards[cid].method = m
        sv.bump()

    def cal_type(el):
        cid, t = parts(el)
        c = sv.cards[cid]
        cal = c.draft["calibration"]
        if t == cal["type"]:
            return

        def y(v):
            return apply_calibration(cal, v)

        if t == "linear":
            new = {"type": "linear", "gain": precision(y(1) - y(0), 6), "offset": precision(y(0), 6)}
        elif t == "polynomial":
            new = {"type": "polynomial", "coeffs": [cal["offset"], cal["gain"]] if cal["type"] == "linear" else [precision(y(0), 6), precision(y(1) - y(0), 6)]}
        else:
            new = {"type": "table", "points": [[v, precision(y(v), 6)] for v in (0, 1.25, 2.5, 3.75, 5)]}
        redraft(c, calibration=new)

    def cal_num(el):
        cid, key = parts(el)
        v = as_float(el)
        c = sv.cards[cid]
        if v is None:
            sv.bump()
            return
        redraft(c, calibration={**c.draft["calibration"], key: v})

    def cal_coeffs(el):
        c = card(el)
        try:
            co = [float(x) for x in el.value.replace(",", " ").split()]
        except ValueError:
            co = []
        if co and all(math.isfinite(x) for x in co):
            redraft(c, calibration={"type": "polynomial", "coeffs": co})
        else:
            toast("Enter numbers separated by commas, lowest order first.")
            sv.bump()

    def cal_pt(el):
        cid, i, j = parts(el)
        c = sv.cards[cid]
        v = as_float(el)
        pts = copy.deepcopy(c.draft["calibration"]["points"])
        if v is not None and int(i) < len(pts):
            pts[int(i)][int(j)] = v
            redraft(c, calibration={"type": "table", "points": pts})

    def cal_pt_remove(el):
        cid, i = parts(el)
        c = sv.cards[cid]
        redraft(c, calibration={"type": "table", "points": [p for k, p in enumerate(c.draft["calibration"]["points"]) if k != int(i)]})

    def cal_pt_add(el):
        c = card(el)
        pts = copy.deepcopy(c.draft["calibration"]["points"])
        pts.append([pts[-1][0] + 0.5, pts[-1][1]])
        redraft(c, calibration={"type": "table", "points": pts})

    def cal_zero(el):
        c = sv.cards[data(el, "arg")]
        v = as_float(el)
        if v is not None:
            redraft(c, zeroOffset=v)

    def cal_fm(el):
        c = sv.cards["pneumo"]
        v = as_float(el)
        if v is not None:
            redraft(c, flowModel={**c.flow_model(), data(el, "arg"): v})

    def cal_capture(el):
        cid, target = parts(el)
        c = sv.cards[cid]
        c.capturing.add(target)

        def done(r, err):
            c.capturing.discard(target)
            if err:
                toast(err, 5000)
                sv.bump()
                return
            volts = r.volts
            if target == "zero":
                d = c.draft
                z = apply_calibration(d["calibration"], volts) - ZERO_REF[cid]
                toast(f"Zero offset set to {z:.{digits_for(d.get('unit', ''))}f} {d.get('unit', '')}. Save to apply.")
                redraft(c, zeroOffset=precision(z, 6))
            elif target.startswith("pt"):
                pts = copy.deepcopy(c.draft["calibration"]["points"])
                i = int(target[2:])
                if i < len(pts):
                    pts[i][0] = round(volts, 4)
                    redraft(c, calibration={"type": "table", "points": pts})
            else:
                mode = "zerospan" if target.startswith("zerospan") else "fit"
                i = int(target[len(mode) :])
                if i < len(c.pts[mode]):
                    c.pts[mode][i]["volts"] = round(volts, 4)
                sv.bump()

        e.capture_average(cid, 2, done)
        sv.bump()

    def cal_ref(el):
        cid, i, k = parts(el)
        v = as_float(el)
        c = sv.cards[cid]
        c.pts[c.method][int(i)][k] = v if v is not None else math.nan
        sv.bump()

    def cal_ref_add(el):
        c = card(el)
        c.pts["fit"].append({"label": f"Point {len(c.pts['fit']) + 1}", "ref": math.nan, "volts": math.nan})
        sv.bump()

    def cal_ref_remove(el):
        cid, i = parts(el)
        del sv.cards[cid].pts["fit"][int(i)]
        sv.bump()

    def cal_fit(el):
        cid, deg = parts(el)
        c = sv.cards[cid]
        mode = c.method
        ready = [p for p in c.pts[mode] if math.isfinite(p["volts"]) and math.isfinite(p["ref"])]
        u = c.draft.get("unit", "")

        def apply(cal, how):
            c.draft = {**c.draft, "calibration": cal, "zeroOffset": 0}
            err = max(abs(apply_calibration(cal, p["volts"]) - p["ref"]) for p in ready)
            c.result[mode] = f"{how}. Largest error at the reference points: {err:.{digits_for(u)}f} {u}. Review the preview above, then Save."
            sv.bump()

        try:
            if deg == "table":
                apply({"type": "table", "points": sorted([[p["volts"], p["ref"]] for p in ready])}, f"Lookup table from {len(ready)} points")
            elif deg == "1" and len(ready) == 2:
                g, o = two_point_linear(ready[0]["volts"], ready[0]["ref"], ready[1]["volts"], ready[1]["ref"])
                apply({"type": "linear", "gain": precision(g, 7), "offset": precision(o, 7)}, f"Line through 2 points: gain {g:.5g} {u}/V, offset {o:.5g} {u}")
            else:
                co = [precision(x, 7) for x in fit_polynomial([(p["volts"], p["ref"]) for p in ready], int(deg))]
                if deg == "1":
                    apply({"type": "linear", "gain": co[1], "offset": co[0]}, f"Least-squares line through {len(ready)} points")
                else:
                    apply({"type": "polynomial", "coeffs": co}, f"Quadratic fit through {len(ready)} points")
        except ValueError as ex:
            c.result[mode] = ""
            toast(str(ex), 5000)
            sv.bump()

    def syr_start(el):
        c = sv.cards["pneumo"]
        idx = int(c.draft["index"])
        acc = {"a1": 0.0, "a2": 0.0, "last_t": None, "last_p": 0.0, "t0": 0.0, "t_end": 0.0}
        c.syr["acc"], c.syr["res"] = acc, None

        def on(p):
            d = c.draft
            v = p.volts[idx] if idx < len(p.volts) else math.nan
            if not math.isfinite(v):
                return
            pa = apply_calibration(d["calibration"], v) - d["zeroOffset"]
            if acc["last_t"] is not None:
                dt = p.t_sec - acc["last_t"]
                acc["a1"] += (pa + acc["last_p"]) / 2 * dt
                acc["a2"] += (pa * abs(pa) + acc["last_p"] * abs(acc["last_p"])) / 2 * dt
            else:
                acc["t0"] = p.t_sec
            acc["last_t"] = acc["t_end"] = p.t_sec
            acc["last_p"] = pa

        c.syr["off"] = e.on_sample(on)
        sv.bump()

    def syr_stop(el):
        c = sv.cards["pneumo"]
        if c.syr["off"]:
            c.syr["off"]()
        c.syr["off"] = None
        a = c.syr["acc"]
        if not a or a["last_t"] is None:
            toast("No pneumotach data arrived. Connect the device first.", 5000)
        else:
            c.syr["res"] = (a["a1"], a["a2"], a["t_end"] - a["t0"])
        sv.bump()

    def syr_vol(el):
        v = as_float(el)
        if v is not None:
            sv.cards["pneumo"].syr["volume"] = min(10, max(0.1, v))
        sv.bump()

    def syr_apply(el):
        c = sv.cards["pneumo"]
        redraft(c, flowModel={**c.flow_model(), "k1": float(data(el, "arg"))})

    def cal_save(el):
        c = sv.cards[data(el, "arg")]
        try:
            nxt = clone_channels(e.channels)
            nxt["channels"][c.cid] = {**c.draft, "calibratedAt": _utc_now()}
            e.set_channels(nxt)
            c.saved_json = json.dumps(e.channels["channels"][c.cid], sort_keys=True)
            c.draft = copy.deepcopy(e.channels["channels"][c.cid])
            toast(f"{NAMES[c.cid]} calibration saved.")
        except ValueError as ex:
            toast(str(ex), 5000)
        sv.bump()

    def cal_discard(el):
        c = sv.cards[data(el, "arg")]
        c.draft = json.loads(c.saved_json)
        sv.bump()

    def cal_defaults(el):
        c = sv.cards[data(el, "arg")]
        d = copy.deepcopy(DEFAULT_CHANNELS["channels"][c.cid])
        d["index"] = c.draft["index"]
        c.draft = d
        toast("Default values loaded. Save to apply.")
        sv.bump()

    def cal_export(el):
        data = {**e.channels, "exportedAt": _utc_now()}
        download(f"co2r-calibration-{datetime.now():%Y-%m-%d}.json", json.dumps(data, indent=2), "application/json")

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
            toast("All channels reset to the defaults.")
            sv.bump()

    # --- display
    def set_num(el):
        e.update_settings(**{data(el, "arg"): float(el.value)})
        sv.bump()

    def proc(el):
        v = as_float(el)
        if v is not None:
            v = min(float(el.max), max(float(el.min), v))
            name = data(el, "arg")
            if name == "response_skip_breaths":
                v = int(round(v))
            e.update_settings(processing=dataclasses.replace(e.settings.processing, **{name: v}))
        sv.bump()

    def measure_lag(_el):
        if sv.measuring_lag:
            return
        sv.measuring_lag = True

        def done(res, err):
            sv.measuring_lag = False
            if err:
                toast(err, 6000)
            else:
                lag, n = res
                ms = int(round(lag * 1000 / 10) * 10)
                e.update_settings(processing=dataclasses.replace(e.settings.processing, co2_delay_ms=ms))
                toast(f"CO2 lags flow by {ms} ms (median of {n} breaths). Saved.", 6000)
            sv.bump()

        e.measure_co2_delay(20, done)
        sv.bump()

    def proc_db(el):
        v = as_float(el)
        if v is not None:
            e.update_settings(processing=dataclasses.replace(e.settings.processing, deadband_lpm=min(0.5, max(0.005, v)) * 60))
        sv.bump()

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
        proc=proc,
        **{
            "settings-tab": lambda el: (setattr(sv, "tab", data(el, "arg")), sv.bump()),
            "measure-lag": measure_lag,
            "rate-send": lambda el: (e.send(f"RATE {sv.rate}"), e.send("INFO")),
            "ask-info": lambda el: e.send("INFO"),
            "disconnect": lambda el: (e.disconnect(), sv.bump()),
            "baud": lambda el: (e.update_settings(baud_rate=int(el.value)), sv.bump()),
            "autoreconnect": lambda el: (e.update_settings(auto_reconnect=bool(el.checked)), sv.bump()),
            "sim-scenario": lambda el: (e.set_sim(scenario=el.value), sv.bump()),
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
            "proc-bool": lambda el: (e.update_settings(processing=dataclasses.replace(e.settings.processing, **{data(el, "arg"): bool(el.checked)})), sv.bump()),
            "proc-db": proc_db,
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
