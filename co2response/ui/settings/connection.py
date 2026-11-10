"""Settings > Connection: the Arduino, the simulator, replay, and Python acquisition (co2r-daq)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QVBoxLayout

from ... import connection_form as cf
from ...engine.monitor import MonitorEngine, Snapshot
from ...storage.session_file import read_session
from ..widgets import (
    Card,
    Form,
    NumberEdit,
    Seg,
    StatGrid,
    button,
    combo,
    hbox,
    label,
    number_field,
    set_combo,
    toggle,
)
from .common import FlowGrid, Page, downloads_dir


class ConnectionPage(Page):
    def __init__(self, engine: MonitorEngine, window) -> None:
        super().__init__()
        self.engine = engine
        self.window = window
        s = engine.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Connection", "h2"))
        self.status = label("", "lead", wrap=True)
        lay.addWidget(self.status)

        # ---- connected device
        self.dev = Card("Connected device")
        self.dev.head.addWidget(button("Disconnect", engine.disconnect, kind="danger"))
        self.info = StatGrid(150, inner=True)
        self.dev.add(self.info)
        self.rate_form = Form()
        self.rate = NumberEdit(cf.RATE_DEFAULT, cf.RATE_MIN, cf.RATE_MAX)
        self.rate_form.add(
            "rate",
            cf.RATE_LABEL,
            hbox(self.rate, label("Hz", "muted"), button("Send", self._send_rate, small=True), button("Ask INFO", lambda: engine.send("INFO"), small=True)),
            hint=cf.RATE_HINT,
        )
        self.dev.add(self.rate_form)
        lay.addWidget(self.dev)

        cards = FlowGrid(430)
        # ---- Arduino over USB
        usb = Card("Arduino over USB")
        usb.add(label("Uses the computer's serial port directly (Windows, macOS or Linux). Plug the Arduino in, pick its port and connect.", "note", wrap=True))
        f = Form()
        self.port = QComboBox()
        self.port.setFixedWidth(260)
        f.add("port", "Serial port", hbox(self.port, button("Refresh", self._refresh_ports, small=True)))
        f.add("baud", "Baud rate", combo([(b, str(b)) for b in cf.BAUDS], s.baud_rate, lambda v: engine.update_settings(baud_rate=v), 120), hint=cf.BAUD_HINT)
        self.autoreconnect = toggle(s.auto_reconnect, lambda v: engine.update_settings(auto_reconnect=v))
        f.add("auto", cf.RECONNECT_LABEL, self.autoreconnect, hint=cf.RECONNECT_HINT)
        usb.add(f)
        self.reconnect = button("", self._reconnect, kind="primary")
        usb.add(hbox(self.reconnect, button("Connect to selected port", self._connect_port, kind="primary"), stretch_end=True))
        cards.add(usb)

        # ---- simulator
        sim = Card("Simulator", "no hardware needed")
        sim.add(label(cf.SIM_NOTE + "The Simulation key on the monitor opens a panel that sets every value the monitor shows.", "note", wrap=True))
        f = Form()
        self.scenario = combo(cf.situation_options(), s.sim.preset, lambda v: engine.apply_situation(v, start=False), 300)
        f.add("scenario", "Situation", self.scenario)
        self.sim_desc = label("", "note", wrap=True)
        sim.add(self.sim_desc)
        self.sim_fields = {}
        for key, text, unit, lo, hi, _ in cf.SIM_FIELDS:
            self.sim_fields[key] = number_field(f, key, text, getattr(s.sim, key), lambda v, key=key: engine.set_sim(**{key: v}), unit, lo=lo, hi=hi)
        self.sim_rr, self.sim_et, self.sim_vt = (self.sim_fields[k] for k in ("rr", "etco2", "vt_ml"))
        sim.add(f)
        self.sim_btn = button("Start simulator", lambda: engine.connect("simulator"), kind="primary")
        sim.add(hbox(self.sim_btn, button("Open the simulation panel", window.open_simulation), stretch_end=True))
        cards.add(sim)

        # ---- replay
        rep = Card("Replay a saved session")
        f = Form()
        self.session = QComboBox()
        self.session.setFixedWidth(300)
        f.add("session", "Session", self.session)
        self.speed = Seg(list(cf.REPLAY_SPEEDS), 1)
        f.add("speed", "Speed", self.speed)
        self.use_file_cal = toggle(True, lambda v: None)
        f.add("cal", cf.FILE_CAL_LABEL, self.use_file_cal, hint=cf.FILE_CAL_HINT)
        rep.add(f)
        self.no_sessions = label("No saved sessions yet. Record one, or import a file in Data & recordings.", "note", wrap=True)
        rep.add(self.no_sessions)
        self.replay_btn = button("Start replay", self._replay, kind="primary")
        rep.add(hbox(self.replay_btn, button("Replay a file…", self._replay_file), stretch_end=True))
        cards.add(rep)

        # ---- Python acquisition server (python/co2r: co2r-daq serve)
        br = Card("Python acquisition", "other DAQs, another computer")
        br.add(
            label(
                "co2r-daq reads the hardware and sends the same data here: an NI or LabJack DAQ, a simulated DAQ, or an Arduino on another "
                "computer. Start it where the hardware is plugged in, for example co2r-daq serve --driver nidaq --device Dev1, or "
                "co2r-daq serve --driver arduino --port COM3 --host 0.0.0.0 to watch from another computer. See docs/python-daq.md.",
                "note",
                wrap=True,
            )
        )
        f = Form()
        self.bridge_url = QLineEdit(s.bridge_url)
        self.bridge_url.setFixedWidth(260)
        self.bridge_url.editingFinished.connect(lambda: engine.update_settings(bridge_url=self.bridge_url.text().strip()))
        f.add("url", cf.BRIDGE_LABEL, self.bridge_url, hint="ws://localhost:8765 unless you changed --ws-port or it runs on another computer.")
        br.add(f)
        br.add(hbox(button("Connect to Python", self._bridge, kind="primary"), stretch_end=True))
        cards.add(br)
        lay.addWidget(cards)
        lay.addStretch(1)
        self._lib_version = -1
        self._refresh_ports()

    # ---------- actions

    def _send_rate(self) -> None:
        for cmd in cf.rate_commands(self.rate.value()):
            self.engine.send(cmd)

    def _refresh_ports(self) -> None:
        cur = self.port.currentData() or self.engine.settings.last_port
        self.port.clear()
        ports = self.engine._ports()
        for p in ports:
            self.port.addItem(p.label + ("  ·  likely Arduino" if p.likely_arduino else ""), p.device)
        if not ports:
            self.port.addItem("No serial ports found", None)
        set_combo(self.port, cur)

    def _connect_port(self) -> None:
        dev = self.port.currentData()
        if not dev:
            self.window.toast("No serial port selected. Plug the Arduino in and press Refresh.")
            return
        if not self.engine.connect("serial", port=dev):
            self.window.toast(self.engine.snapshot().status_detail, 7000)

    def _reconnect(self) -> None:
        if not self.engine.reconnect_known_port():
            self.window.toast(self.engine.snapshot().status_detail, 6000)

    def _replay(self) -> None:
        sid = self.session.currentData()
        rec = self.engine.library.get(sid) if sid else None
        if rec is None:
            return
        try:
            s = cf.for_replay(self.engine.library.parse(sid))
        except (OSError, ValueError) as e:
            self.window.toast(f"Could not replay {rec.file_name}: {e}", 6000)
            return
        self.engine.connect("replay", session=s, file_name=rec.file_name, speed=self.speed.value() or 1, use_file_calibration=self.use_file_cal.isChecked())

    def _replay_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Replay a session file", str(downloads_dir()), "Session files (*.csv);;All files (*)")
        if not path:
            return
        try:
            s = cf.for_replay(read_session(path))
        except (OSError, ValueError, UnicodeDecodeError) as e:
            self.window.toast(f"Could not replay {Path(path).name}: {e}", 6000)
            return
        self.engine.connect("replay", session=s, file_name=Path(path).name, speed=self.speed.value() or 1, use_file_calibration=self.use_file_cal.isChecked())

    def _bridge(self) -> None:
        self.engine.update_settings(bridge_url=self.bridge_url.text().strip())
        if not self.engine.connect("bridge"):
            self.window.toast(self.engine.snapshot().status_detail, 7000)

    # ---------- refresh

    def refresh(self, s: Snapshot, force: bool = False) -> None:
        self.status.setText(f"Status: <b>{cf.status_text(s)}</b>")
        connected = s.status == "connected"
        self.dev.setVisible(connected)
        if connected:
            self.info.set_all(cf.device_info(s))
            self.rate_form.setVisible(cf.takes_rate(s))
        self.reconnect.setVisible(bool(s.known_port))
        self.reconnect.setText(f"Reconnect {s.known_port}" if s.known_port else "")
        self.sim_btn.setText(cf.sim_button(s))
        sim = s.settings.sim
        set_combo(self.scenario, sim.preset)
        self.sim_desc.setText(cf.situation_hint(sim))
        for key, w in self.sim_fields.items():
            w.set(getattr(sim, key))
        if s.recording.library_version != self._lib_version or force:
            self._lib_version = s.recording.library_version
            cur = self.session.currentData()
            self.session.clear()
            for r in cf.replayable(self.engine.library):
                self.session.addItem(r.file_name, r.id)
            set_combo(self.session, cur)
            has = self.session.count() > 0
            self.no_sessions.setVisible(not has)
            self.replay_btn.setEnabled(has)
        if force:
            self._refresh_ports()
