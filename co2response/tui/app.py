"""The monitor in a terminal: the whole screen drawn with text, so it runs in any
terminal window, over SSH, and in the terminal of VS Code or another editor.

    co2response-tui --sim            # or:  python -m co2response --tui --sim

It drives the same engine as the desktop window and the browser page (the same
processing, breaths, alarms, test keys and recordings); only the drawing differs.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import APP_NAME, __version__
from ..alarms.audio import AlarmAudio
from ..engine.monitor import MonitorEngine, Snapshot
from ..processing.co2 import mmhg_to_pct
from ..sim.controls import SITUATIONS, situation_label
from .canvas import Canvas, SweepBuffer, big_digits, draw_response, draw_sweep, draw_trend

#: colours of the three channels, as on the desktop monitor
COLOR = {"co2": "yellow1", "flow": "deep_sky_blue1", "pneumo": "spring_green2"}
PRIORITY_STYLE = {"high": "bold white on red3", "medium": "bold black on gold1", "advisory": "bold white on dodger_blue3"}


def fmt(v: float, d: int = 0) -> str:
    return "--" if v is None or not math.isfinite(v) else f"{v:.{d}f}"


def mss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# ------------------------------------------------------------------ widgets


class Panel(Static):
    """A bordered box whose content is redrawn to fit its size."""

    def __init__(self, title: str = "", **kw) -> None:
        super().__init__("", **kw)
        self.border_title = title

    def area(self) -> tuple[int, int]:
        s = self.content_size
        return max(1, s.width), max(1, s.height)


class WavePanel(Panel):
    def __init__(self, cid: str, title: str, **kw) -> None:
        super().__init__(title, **kw)
        self.cid = cid
        self.sweep = SweepBuffer()

    def draw(self, vmin: float, vmax: float, ticks, signed: bool, limit: float | None) -> None:
        w, h = self.area()
        cv = Canvas(w, h)
        draw_sweep(cv, self.sweep, vmin, vmax, COLOR[self.cid], ticks=ticks, signed=signed, limit=limit)
        self.update(cv.render())


class NumbersPanel(Panel):
    """The large numbers: EtCO2 in block digits, then RR, live flow, VT, MV (and SpO2)."""

    def draw(self, s: Snapshot, flash: bool) -> None:
        w, h = self.area()
        n = s.numbers
        st = s.settings
        pct = st.co2_unit == "%"
        pb, wv = st.processing.barometric_mm_hg, st.processing.water_vapor

        def disp(mm):
            return mmhg_to_pct(mm, pb, wv) if pct else mm

        co2_alarm = next((a for a in s.alarms if a.id in ("co2Stop", "co2High", "co2Low") and a.state == "active"), None)
        apnea = next((a for a in s.alarms if a.id == "apnea" and a.state == "active"), None)
        et = "---" if s.stale["co2"] else fmt(disp(n.etco2), 1 if pct else 0)
        style = COLOR["co2"]
        if co2_alarm:
            style = "bold white on red3" if co2_alarm.priority == "high" else "bold black on gold1"
            if flash and not co2_alarm.acknowledged:
                style = "bold red3"
        a = st.alarms
        stop = f" / stop {fmt(disp(a.stop_mm_hg))}" if a.stop_mm_hg is not None else ""
        out = Text(no_wrap=True, overflow="crop")
        out.append(f"EtCO2 {'%' if pct else 'mmHg'}", f"bold {COLOR['co2']}")
        out.append(f"   warn {fmt(disp(a.high_mm_hg))}{stop}\n", "grey62")
        for digits in big_digits(et):
            out.append(f" {digits}\n", style)
        q = "no CO2 signal" if s.stale["co2"] else f"last breath left out ({n.rejected.split(':')[0]})" if n.rejected else f"median of 3 breaths {fmt(disp(n.etco2_median), 1 if pct else 0)}"
        out.append(f" {q}\n", "grey62")

        def row(label: str, unit: str, value: str, color: str, note: str = "") -> None:
            out.append(f"{label:<5}", f"bold {color}")
            out.append(f"{value:>7}", f"bold {color}")
            out.append(f" {unit:<6}", "grey62")
            out.append(f"{note}\n", "grey50")

        rr_style = "bold white on red3" if apnea and (not flash or apnea.acknowledged) else COLOR["co2"]
        out.append("\n")
        row("RR", "/min", fmt(n.rr), COLOR["co2"] if not apnea else rr_style, "apnea!" if apnea else "")
        row("Flow", "L/min", "---" if s.stale["flow"] else fmt(n.flow), COLOR["flow"], "live")
        row("VT", "mL", "---" if s.stale["pneumo"] else fmt(n.vt), COLOR["pneumo"], f"exp {fmt(n.vte)} mL" if math.isfinite(n.vte) else "")
        row("MV", "L/min", "---" if s.stale["pneumo"] else fmt(n.mv, 1), COLOR["pneumo"], "this breath")
        if st.spo2.enabled:
            row("SpO2", "%", fmt(n.spo2), "white", f"low {fmt(st.spo2.low_pct)}" if st.spo2.low_pct > 0 else "")
        if s.capno is not None:
            c = s.capno
            out.append("\nCapnostream 35 says\n", "bold grey70")
            if c:
                parts = [f"EtCO2 {fmt(c.get('etco2', math.nan))}", f"RR {fmt(c.get('rr', math.nan))}", f"SpO2 {fmt(c.get('spo2', math.nan))}", f"PR {fmt(c.get('pr', math.nan))}"]
                out.append(" " + " · ".join(parts) + "\n", "grey70")
            else:
                out.append(" no numbers from it yet\n", "grey50")
        lines = out.split("\n")[:h]
        self.update(Text("\n").join(lines))


class StatusBar(Static):
    def draw(self, s: Snapshot, engine: MonitorEngine) -> None:
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f" {APP_NAME} ", "bold black on dark_orange")
        t.append(" RESEARCH USE ONLY ", "bold dark_orange")
        dot = {"connected": ("●", "green3"), "connecting": ("●", "gold1"), "error": ("●", "red")}.get(s.status, ("○", "grey50"))
        t.append(f" {dot[0]} ", dot[1])
        if s.status == "connected":
            t.append(s.source_label, "bold")
            t.append(f" · {s.measured_rate_hz:.0f} Hz · {s.dropped} dropped", "grey62")
            t.append(f" · {'Uno R4, digital sensors' if s.hardware == 'digital' else 'ADC board'}", "grey62")
            if s.capno is not None:
                t.append(" · CO2 from the Capnostream 35", "grey62")
        else:
            t.append(s.status_detail or "Not connected: press o to connect", "grey62")
        rec = s.recording
        if rec.active:
            t.append(f"   ● REC {mss((s.now - rec.started_at) / 1000)} {rec.file_name}", "bold red")
        t.append("   " + time.strftime("%H:%M:%S"), "bold")
        self.update(t)


class AlarmBar(Static):
    def draw(self, s: Snapshot, flash: bool) -> None:
        active = [a for a in s.alarms if a.state == "active" and a.id != "audioOff"]
        if not active:
            t = Text(" No alarms", "green3")
            if not s.audio_armed:
                t.append("   · alarm sound off (no sound output): the board's buzzer and the screen still alarm", "grey50")
            self.update(t)
            return
        t = Text(no_wrap=True, overflow="ellipsis")
        for a in active[:3]:
            st = PRIORITY_STYLE.get(a.priority, "bold")
            if flash and not a.acknowledged and a.priority == "high":
                st = "bold red3 on grey11"
            t.append(f" {a.message} ", st)
            t.append(" ")
        if s.silence_ms > 0:
            t.append(f" silenced {mss(s.silence_ms / 1000)}", "grey62")
        self.update(t)


class TestBar(Static):
    def draw(self, s: Snapshot, engine: MonitorEngine) -> None:
        ts = s.test
        t = Text(no_wrap=True, overflow="ellipsis")
        phase_style = {"baseline": "bold black on sky_blue1", "rebreathing": "bold black on gold1", "ended": "bold black on grey70"}.get(ts.phase, "bold grey70")
        t.append(f" {ts.phase.upper()} ", phase_style)
        text = ts.text
        if text.upper().startswith(ts.phase.upper()):
            text = text[len(ts.phase) :].lstrip(" ·:")
        t.append(f" {text}", "")
        if s.source_kind == "simulator":
            sim = s.settings.sim
            t.append(f"   · simulation: {situation_label(sim)}, {engine.time_scale():g}× speed", "grey62")
            wt = engine.sim_test_text()
            if wt:
                t.append(f" · {wt}", "grey62")
        self.update(t)


class BreathTable(Panel):
    """The last breaths: each one's EtCO2, VT, its own RR and MV (VT × 60 / its length)."""

    def draw(self, engine: MonitorEngine) -> None:
        w, h = self.area()
        rows = h - 1
        out = Text(no_wrap=True, overflow="crop")
        out.append(f"{'#':>4} {'EtCO2':>6} {'VT':>5} {'RR':>4} {'MV':>5}  \n", "bold")
        pts = engine.trend[-rows:] if rows > 0 else []
        for i, q in enumerate(pts):
            n = len(engine.trend) - len(pts) + i + 1
            rr = q.mv_breath / (q.vt / 1000) if math.isfinite(q.vt) and q.vt > 0 and math.isfinite(q.mv_breath) else math.nan
            st = "" if q.quality == "ok" else "grey42"
            line = f"{n:>4} {fmt(q.etco2):>6} {fmt(q.vt):>5} {fmt(rr):>4} {fmt(q.mv_breath, 1):>5}"
            out.append(line, st)
            out.append((" x" if q.excluded else "  " if q.quality == "ok" else f" {q.quality[:6]}") + "\n", "grey50")
        self.update(out)


class GraphPanel(Panel):
    """The ventilatory response (MV vs EtCO2) or the trend, chosen with the v key."""

    def draw(self, s: Snapshot, engine: MonitorEngine, mode: str) -> None:
        w, h = self.area()
        cv = Canvas(w, h)
        if mode == "response":
            self.border_title = "Ventilatory response · MV of each breath vs its EtCO2"
            res = engine.co2_response()
            draw_response(cv, res, title=s.settings.study_code)
        else:
            series = {"etco2": ("EtCO2 (mmHg) per breath", "etco2", COLOR["co2"]), "mv": ("MV (L/min) per breath", "mv_breath", COLOR["pneumo"])}[mode]
            self.border_title = f"Trend · {series[0]}"
            pts = engine.trend
            now = s.now / 1000
            t1 = max(now, pts[-1].t if pts else now)
            span = max(60.0, min(30 * 60.0, t1 - (pts[0].t if pts else t1)))
            t0 = t1 - span
            ok = [q for q in pts if q.quality == "ok"]
            vals = [getattr(q, series[1]) for q in ok]
            top = max([v for v in vals if math.isfinite(v)] + [10.0])
            ymax = max(80.0, s.settings.co2_scale_max_mm_hg) if mode == "etco2" else max(10.0, math.ceil(top * 1.2))
            a = s.settings.alarms
            limits = [(a.high_mm_hg, "gold1")] + ([(a.stop_mm_hg, "red")] if a.stop_mm_hg is not None else []) if mode == "etco2" else []
            markers = [(m.t / 1000, m.label) for m in engine.markers]
            draw_trend(cv, [q.t for q in ok], vals, t0, t1, ymax, series[2], series[0], limits, markers)
        self.update(cv.render())


# ------------------------------------------------------------------ pickers


class Picker(ModalScreen):
    """A list to choose from (Enter), or Esc to go back."""

    DEFAULT_CSS = """
    Picker { align: center middle; background: rgba(0, 0, 0, 0.6); }
    Picker > Vertical { width: 76; max-height: 90%; height: auto; border: round $accent; background: $panel; padding: 0 1; }
    Picker OptionList { height: auto; max-height: 30; }
    """
    BINDINGS = [Binding("escape", "dismiss(None)", "Back")]

    def __init__(self, title: str, options: list[tuple[str, str]], note: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.options = options
        self.note = note

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.title_text, "bold"))
            if self.note:
                yield Static(Text(self.note, "grey62"))
            yield OptionList(*[Option(label, id=key) if key else Option(label, disabled=True) for key, label in self.options])

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)


# ------------------------------------------------------------------ the app


class MonitorApp(App):
    TITLE = f"{APP_NAME} monitor"
    CSS = """
    Screen { layout: vertical; background: #000000; }
    StatusBar, AlarmBar, TestBar { height: 1; }
    #waves { height: 3fr; }
    #wavecol { width: 3fr; }
    #nums { width: 36; height: 100%; border: round #333333; }
    WavePanel { height: 1fr; border: round #333333; border-title-color: $text; }
    #bottom { height: 2fr; min-height: 12; }
    #graph { width: 3fr; height: 100%; border: round #333333; }
    #breaths { width: 36; height: 100%; border: round #333333; }
    """
    BINDINGS = [
        Binding("o", "connect", "Connect"),
        Binding("s", "situation", "Simulate"),
        Binding("plus,equals_sign", "speed(1)", "Faster", show=False),
        Binding("minus", "speed(-1)", "Slower", show=False),
        Binding("b", "baseline", "Baseline"),
        Binding("r", "rebreathing", "Rebreathe"),
        Binding("e", "end_test", "End test"),
        Binding("x", "emergency", "STOP"),
        Binding("m", "mark", "Mark"),
        Binding("space", "silence", "Silence"),
        Binding("c", "record", "Record"),
        Binding("v", "view", "Graph"),
        Binding("f", "freeze", "Freeze", show=False),
        Binding("a", "arm_audio", "Audio", show=False),
        Binding("h", "hardware", "Board", show=False),
        Binding("k", "capnostream", "Capnostream"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, sim: bool = False, port: str | None = None, replay: str | None = None, situation: str | None = None, speed: float | None = None, exit_after: float | None = None) -> None:
        super().__init__()
        self.start = (sim, port, replay, situation, speed)
        self.exit_after = exit_after
        self.engine = MonitorEngine(audio=AlarmAudio(fallback_beep=self._bell))
        self.engine.arm_audio(confirm=False)
        self.graph_mode = "response"
        self._epoch = -1
        self._flash = False
        self._last_print = ""

    def _bell(self) -> None:
        out = sys.__stdout__
        if out is not None:  # the terminal bell: the alarm sound when no sound output works
            try:
                out.write("\a")
                out.flush()
            except OSError:
                pass

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status")
        yield AlarmBar(id="alarms")
        with Horizontal(id="waves"):
            with Vertical(id="wavecol"):
                yield WavePanel("co2", "CO2 mmHg", id="w-co2")
                yield WavePanel("flow", "Flow L/min", id="w-flow")
                yield WavePanel("pneumo", "Pneumotach L/min", id="w-pneumo")
            yield NumbersPanel("Numbers", id="nums")
        yield TestBar(id="test")
        with Horizontal(id="bottom"):
            yield GraphPanel("", id="graph")
            yield BreathTable("Each breath · MV = VT × RR", id="breaths")
        yield Footer()

    def on_mount(self) -> None:
        # kept by reference: while a picker is open, queries would search the picker's screen
        self.w_status = self.query_one(StatusBar)
        self.w_alarms = self.query_one(AlarmBar)
        self.w_test = self.query_one(TestBar)
        self.w_nums = self.query_one(NumbersPanel)
        self.w_graph = self.query_one(GraphPanel)
        self.w_breaths = self.query_one(BreathTable)
        self.w_waves = {p.cid: p for p in self.query(WavePanel)}
        e = self.engine
        recovered = e.init()
        if recovered:
            self.notify(f"Recovered {len(recovered)} recording(s) left unfinished last time.", timeout=8)
        sim, port, replay, situation, speed = self.start
        if speed is not None:
            e.set_sim(speed=float(speed))
        if situation:
            e.apply_situation(situation)
        elif sim:
            e.connect("simulator")
        elif port:
            if not e.connect("serial", port=port):
                self.notify(e.snapshot().status_detail or f"Could not open {port}", severity="error", timeout=8)
        elif replay:
            self._replay(replay)
        self.set_interval(0.02, e.poll)
        self.set_interval(0.25, self._tick)
        self.set_interval(1 / 12, self._frame)
        if not (sim or port or replay or situation):
            self.call_after_refresh(self.action_connect)
        if self.exit_after is not None:
            self.set_timer(self.exit_after, self._finish_check)

    def _finish_check(self) -> None:
        n = self.engine.snapshot().numbers
        self._last_print = f"{APP_NAME}: ran {self.exit_after:g} s; {len(self.engine.breaths)} breaths, EtCO2 {fmt(n.etco2)} mmHg, RR {fmt(n.rr)} /min, MV {fmt(n.mv, 1)} L/min"
        self.exit()

    def _replay(self, path: str) -> None:
        from ..storage.session_file import read_session

        try:
            s = read_session(path)
            self.engine.connect("replay", session=s, file_name=Path(path).name)
        except (OSError, ValueError) as ex:
            self.notify(f"Could not replay {path}: {ex}", severity="error", timeout=8)

    # ---------- the two timers of the engine, and drawing

    def _tick(self) -> None:
        e = self.engine
        e.tick()
        self._flash = not self._flash
        s = e.snapshot()
        self.w_status.draw(s, e)
        self.w_alarms.draw(s, self._flash)
        self.w_test.draw(s, e)
        self.w_nums.draw(s, self._flash)
        self.w_graph.draw(s, e, self.graph_mode)
        self.w_breaths.draw(e)
        single = s.single_flow
        pn = self.w_waves["pneumo"]
        if pn.display == single:
            pn.display = not single
        self._scales = s

    def _frame(self) -> None:
        e = self.engine
        s = getattr(self, "_scales", None) or e.snapshot()
        st = s.settings
        waves = e.take_waves()
        panels = self.w_waves
        if e.wave_epoch != self._epoch:
            self._epoch = e.wave_epoch
            for p in panels.values():
                p.sweep.clear()
        n = max(10, round(st.sweep_sec * s.rate_hz))
        pct = st.co2_unit == "%"
        pb, wv = st.processing.barometric_mm_hg, st.processing.water_vapor
        co2_max = mmhg_to_pct(st.co2_scale_max_mm_hg, pb, wv) if pct else st.co2_scale_max_mm_hg
        for cid, p in panels.items():
            p.sweep.resize(n)
            p.sweep.push(waves[cid])
        k = e.wave_compression
        unit = "%" if pct else "mmHg"
        tag = f" · SIMULATED · {st.sweep_sec * k:g} s per screen" if s.source_kind == "simulator" else " · REPLAY" if s.source_kind == "replay" else ""
        panels["co2"].border_title = f"CO2 {unit}{tag}{' · FROZEN' if s.frozen else ''}"
        ticks = [0, 2, 4, 6, 8, 10] if pct else [0, 20, 40, 60, 80]
        lim = st.alarms.high_mm_hg
        panels["co2"].draw(0, co2_max, [t for t in ticks if t <= co2_max], False, mmhg_to_pct(lim, pb, wv) if pct else lim)
        fs = st.flow_scale_lpm
        for cid in ("flow", "pneumo"):
            if panels[cid].display:
                panels[cid].draw(-fs, fs, [-fs, 0, fs], True, None)

    # ---------- keys

    def action_connect(self) -> None:
        from ..sources.serial_source import list_ports

        opts: list[tuple[str, str]] = [
            ("sim", "Simulator: the Uno R4 board (SprintIR CO2 + SFM3304-D flow)"),
            ("sim-analog", "Simulator: the ADC board (capnograph, flow sensor, pneumotach)"),
        ]
        try:
            ports = list_ports()
        except Exception:  # noqa: BLE001
            ports = []
        for p in ports:
            opts.append((f"port:{p.device}", f"Arduino on {p.device}  {p.label}{'  ← likely the Arduino' if p.likely_arduino else ''}"))
        if not ports:
            opts.append(("", "No serial ports found (plug in the Arduino and press o again)"))
        sample = Path(__file__).resolve().parents[2] / "sample-data" / "synthetic_session.csv"
        if sample.exists():
            opts.append((f"replay:{sample}", "Replay the sample session"))
        opts.append(("disconnect", "Disconnect"))
        opts.append(("quit", "Quit"))

        def done(choice: str | None) -> None:
            e = self.engine
            if not choice:
                return
            if choice == "quit":
                self.exit()
            elif choice == "disconnect":
                e.disconnect()
            elif choice.startswith("sim"):
                e.update_settings(hardware="analog" if choice == "sim-analog" else "digital")
                e.connect("simulator")
            elif choice.startswith("port:"):
                if not e.connect("serial", port=choice[5:]):
                    self.notify(e.snapshot().status_detail or "Could not open the port", severity="error", timeout=8)
            elif choice.startswith("replay:"):
                self._replay(choice[7:])

        self.push_screen(Picker("Connect", opts, "Enter chooses, Esc goes back. The board in use is recognised from what it sends."), done)

    def action_situation(self) -> None:
        opts: list[tuple[str, str]] = []
        group = None
        for sit in SITUATIONS:
            if sit.group != group:
                group = sit.group
                opts.append(("", f"── {group} ──"))
            opts.append((sit.id, f"{sit.label}"))
        note = f"Speed {self.engine.settings.sim.speed:g}× (+ and - change it). A whole test runs by itself and fills the response graph."

        def done(choice: str | None) -> None:
            if choice:
                self.engine.apply_situation(choice)
                if choice.startswith("test_"):
                    self.graph_mode = "response"

        self.push_screen(Picker("Simulation situations", opts, note), done)

    def action_speed(self, step: int) -> None:
        steps = [1, 2, 3, 5, 10, 20, 50]
        cur = self.engine.settings.sim.speed
        i = min(range(len(steps)), key=lambda j: abs(steps[j] - cur))
        nxt = steps[max(0, min(len(steps) - 1, i + step))]
        self.engine.set_sim(speed=nxt)
        self.notify(f"Simulation speed {nxt:g}×", timeout=2)

    def action_baseline(self) -> None:
        self.engine.start_baseline()

    def action_rebreathing(self) -> None:
        self.engine.start_rebreathing()

    def action_end_test(self) -> None:
        self.engine.end_test()

    def action_emergency(self) -> None:
        self.engine.emergency_stop()
        self.notify("EMERGENCY STOP: valve to room air", severity="error", timeout=6)

    def action_mark(self) -> None:
        m = self.engine.add_marker()
        self.notify(f"Marker: {m.label}", timeout=2)

    def action_silence(self) -> None:
        self.engine.toggle_silence()

    def action_freeze(self) -> None:
        self.engine.set_frozen(not self.engine.frozen)

    def action_arm_audio(self) -> None:
        ok = self.engine.arm_audio()
        self.notify("Alarm sound armed" if ok else "No sound output: the terminal bell is used", timeout=3)

    def action_hardware(self) -> None:
        e = self.engine
        e.update_settings(hardware="analog" if e.settings.hardware == "digital" else "digital")
        self.notify(f"Expecting the {'Uno R4 board with digital sensors' if e.settings.hardware == 'digital' else 'ADC board'}", timeout=3)

    def action_capnostream(self) -> None:
        """Choose a Capnostream 35 as the CO2 source (or not)."""
        import dataclasses

        from ..sources.serial_source import list_ports

        e = self.engine
        c = e.settings.capno
        opts: list[tuple[str, str]] = [("off", "Off: the board's own CO2 sensor" + ("  (now)" if not c.enabled else ""))]
        opts.append(("sim", "Simulated Capnostream (with the simulator)" + ("  (now)" if c.enabled and c.port == "sim" else "")))
        try:
            ports = list_ports()
        except Exception:  # noqa: BLE001
            ports = []
        for p in ports:
            now = "  (now)" if c.enabled and c.port == p.device else ""
            opts.append((f"port:{p.device}", f"Serial port {p.device}  {p.label}{now}"))
        if c.port and c.port not in ("sim",) and c.port not in [p.device for p in ports]:
            opts.append((f"port:{c.port}", f"{c.port}" + ("  (now)" if c.enabled else "")))
        note = (
            f"Its CO2 waveform replaces the board's CO2 sensor; flow still comes from the board. {c.baud} baud, fields {c.fields or 'auto'}, "
            f"{c.units}, delay {c.delay_ms:g} ms (change with co2response --capno-baud/--capno-fields/--capno-units/--capno-delay)."
        )

        def done(choice: str | None) -> None:
            if not choice:
                return
            if choice == "off":
                new = dataclasses.replace(c, enabled=False)
            else:
                new = dataclasses.replace(c, enabled=True, port="sim" if choice == "sim" else choice[5:])
            e.update_settings(capno=new)
            self.notify("Capnostream off" if not new.enabled else f"CO2 from the Capnostream on {new.port}" + ("" if isinstance(e.source, type(None)) else " (applies at the next connection)" if e.source is not None and e.source.kind != "simulator" else ""), timeout=5)

        self.push_screen(Picker("Capnostream 35 as the CO2 source", opts, note), done)

    def action_view(self) -> None:
        order = ["response", "etco2", "mv"]
        self.graph_mode = order[(order.index(self.graph_mode) + 1) % len(order)]

    def action_record(self) -> None:
        e = self.engine
        if not e.recorder.active:
            rec = e.start_recording()
            self.notify(f"Recording {rec.file_name if rec else ''}", timeout=3)
            return
        res = e.stop_recording()
        msg = f"Saved {res.path or res.file_name}"
        if res.path:
            pdf = write_report(res.path)
            if pdf:
                msg += f"\nReport: {pdf}"
        self.notify(msg, timeout=10)

    def on_unmount(self) -> None:
        self.engine.shutdown()


def write_report(csv_path: Path) -> Path | None:
    """The PDF report (with the MV-vs-EtCO2 graph) next to a session file."""
    from ..storage.report import build_report, report_pdf
    from ..storage.session_file import read_session

    try:
        r = build_report(read_session(csv_path), csv_path.name, None, __version__, time.time() * 1000)
        dest = csv_path.with_suffix(".pdf")
        dest.write_bytes(report_pdf(r))
        return dest
    except (OSError, ValueError):
        return None


def run(sim: bool = False, port: str | None = None, replay: str | None = None, situation: str | None = None, speed: float | None = None, exit_after: float | None = None) -> int:
    app = MonitorApp(sim=sim, port=port, replay=replay, situation=situation, speed=speed, exit_after=exit_after)
    app.run()
    if app._last_print:
        print(app._last_print)
    return 0


def main(argv: list[str] | None = None) -> int:
    """``co2response-tui``: the same options as ``co2response``, in the terminal."""
    from ..__main__ import main as cli

    return cli(["--tui", *(sys.argv[1:] if argv is None else argv)])

