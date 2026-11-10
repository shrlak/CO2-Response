"""The simulation panel beside the monitor (browser): ready-made situations, a slider or
switch for every simulator value, and the values set next to what the monitor reads.

Sliders change the running simulator while they are dragged. The panel's HTML is
rebuilt only when its layout changes (a situation chosen, a switch flipped, the
simulator started or stopped), never during a drag; the numbers in it are updated
in place four times a second.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..engine.monitor import Snapshot
from ..sim.controls import (
    GROUPS,
    MEASURED_NOTE,
    READBACK,
    RECORD_TESTS,
    SITUATION_BY_ID,
    SITUATION_GROUPS,
    SITUATIONS,
    SLIDERS,
    SPEED,
    SWITCHES,
    Slider,
    fmt_value,
    monitor_values,
    scenario_note,
    situation_label,
    switch_on,
)
from .dom import btn, byid, data, esc, set_html, set_text

if TYPE_CHECKING:
    from .app import WebApp


def _num(v, d: int) -> str:
    if v is None or not math.isfinite(v):
        return "--"
    s = f"{v:.{d}f}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s


def clipping_text(sensors: list[str]) -> str:
    if not sensors:
        return ""
    what = " and ".join(sensors)
    return f"Peak flow is beyond the {what}'s range: the signal clips at the ADC rail, so breaths and volumes are lost. Lower VT or RR, or lengthen inspiration (I:E)."


class SimPanel:
    def __init__(self, app: "WebApp") -> None:
        self.app = app
        self.open = False
        self.version = 0
        self._key = None

    def bump(self) -> None:
        self.version += 1

    # ---------- HTML

    def _slider(self, c: Slider, value: float) -> str:
        hint = f'<div class="hint">{esc(c.hint)}</div>' if c.hint else ""
        return (
            f'<div class="sp-row"><label for="sp-{c.key}">{esc(c.label)}</label><output id="spv-{c.key}" for="sp-{c.key}">{esc(fmt_value(c, value))}</output>'
            f'<input type="range" id="sp-{c.key}" min="{c.lo:g}" max="{c.hi:g}" step="{c.step:g}" value="{value:g}" '
            f'data-input="sim-slide" data-change="sim-slide" data-arg="{c.key}">{hint}</div>'
        )

    def _switch(self, key: str, on: bool) -> str:
        c = SWITCHES[key]
        hint = f'<div class="hint">{esc(c.hint)}</div>' if c.hint else ""
        return (
            f'<div class="sp-row sp-sw"><label><input type="checkbox" id="sp-{key}" data-change="sim-switch" data-arg="{key}"{" checked" if on else ""}>'
            f"<span>{esc(c.label)}</span></label>{hint}</div>"
        )

    def html(self, s: Snapshot) -> str:
        sim = s.settings.sim
        running = s.source_kind == "simulator"
        head = f'<div class="sp-head"><h2>Simulation</h2>{btn("Close", "sim-close", "btn small")}</div>'
        start = ""
        if not running:
            start = (
                '<div class="sp-start"><p class="note">The simulator is not the data source. Choose a situation, or start the simulator, '
                "to drive the monitor with the values below.</p>"
                f'{btn("Start simulator", "start-sim", "btn primary")}</div>'
            )
        now = (
            '<div class="sp-now"><div><span class="k">Situation</span> <b id="sp-current"></b></div>'
            '<p class="note" id="sp-desc"></p><p class="warn" id="sp-progress" hidden></p><p class="warn" id="sp-warn" hidden></p></div>'
        )
        rows = "".join(f'<tr><th>{esc(label)} <span class="u">{esc(unit)}</span></th><td id="sp-set-{k}">--</td><td id="sp-mon-{k}">--</td></tr>' for k, label, unit, _ in READBACK)
        readback = f'<table class="sp-readback"><thead><tr><th></th><th>Set</th><th>Monitor</th></tr></thead><tbody>{rows}</tbody></table>'
        sits = ""
        for g in SITUATION_GROUPS:
            b = "".join(
                f'<button type="button" class="sp-sit" data-act="sim-situation" data-arg="{x.id}" title="{esc(x.description)}" '
                f'aria-pressed="{"true" if x.id == sim.preset else "false"}">{esc(x.label)}</button>'
                for x in SITUATIONS
                if x.group == g
            )
            sits += f'<div class="sp-gt">{esc(g)}</div><div class="sp-grid">{b}</div>'
        # how fast simulated time runs, above everything it speeds up
        speed = self._slider(SPEED, float(sim.speed)) + self._switch(RECORD_TESTS.key, sim.record_tests)
        out = head + start + speed + now + readback + f'<details open class="sp-group"><summary>Situations</summary>{sits}</details>'
        for g in GROUPS:
            inner = "".join(self._slider(it, float(getattr(sim, it.key))) if isinstance(it, Slider) else self._switch(it.key, switch_on(sim, it.key)) for it in g.items)
            out += f'<details open class="sp-group"><summary>{esc(g.title)}</summary>{inner}</details>'
        out += f'<p class="note">{esc(MEASURED_NOTE)}</p>'
        return out

    # ---------- refresh at 4 Hz

    def refresh(self, s: Snapshot) -> None:
        el = byid("simpanel")
        if el is None:
            return
        el.hidden = not self.open
        if not self.open:
            self._key = None
            return
        sim = s.settings.sim
        key = (self.version, s.source_kind == "simulator", sim.scenario, sim.preset, tuple(switch_on(sim, k) for k in SWITCHES))
        if key != self._key:
            self._key = key
            set_html("simpanel", self.html(s), force=True)
        set_text("sp-current", situation_label(sim))
        sit = SITUATION_BY_ID.get(sim.preset)
        set_text("sp-desc", sit.description if sit and not sim.adjusted else "Values changed by hand. Choose a situation to start again from its values.")
        exp = self.app.engine.sim_expected()
        mon = monitor_values(s.numbers)
        for k, _, _, d in READBACK:
            set_text(f"sp-set-{k}", _num(exp.get(k), d) if exp else "--")
            set_text(f"sp-mon-{k}", "---" if self._stale(s, k) else _num(mon[k], d))
        warn = " ".join(x for x in (clipping_text(exp.get("clipping", []) if exp else []), scenario_note(sim)) if x)
        prog = self.app.engine.sim_test_text()
        pr = byid("sp-progress")
        if pr is not None:
            pr.hidden = not prog
            if pr.textContent != prog:
                pr.textContent = prog
        w = byid("sp-warn")
        if w is not None:
            w.hidden = not warn
            if w.textContent != warn:
                w.textContent = warn

    @staticmethod
    def _stale(s: Snapshot, k: str) -> bool:
        ch = {"etco2": "co2", "fico2": "co2", "vt": "pneumo", "mv": "pneumo", "peak": "flow"}.get(k)
        return bool(ch and s.stale.get(ch))


def install_sim_actions(app: "WebApp") -> None:
    from .dom import actions

    e = app.engine
    panel = app.monitor.sim

    def slide(el):
        k = data(el, "arg")
        c = SLIDERS.get(k)
        if c is None:
            return
        try:
            v = float(el.value)
        except (TypeError, ValueError):
            return
        v = min(c.hi, max(c.lo, v))
        set_text(f"spv-{k}", fmt_value(c, v))
        if getattr(e.settings.sim, k) != v:
            e.set_sim(**{k: v})

    def switch(el):
        e.set_sim_switch(data(el, "arg"), bool(el.checked))
        app.refresh()

    def situation(el):
        e.apply_situation(data(el, "arg"))
        panel.bump()
        if app.screen == "monitor":
            app.refresh()
        else:
            app.show("monitor")

    def toggle(_el):
        panel.open = not panel.open
        app.refresh()

    actions(
        **{
            "sim-slide": slide,
            "sim-switch": switch,
            "sim-situation": situation,
            "sim-toggle": toggle,
            "sim-close": lambda el: (setattr(panel, "open", False), app.refresh()),
        }
    )
