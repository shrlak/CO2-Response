"""Settings > Connection: the Arduino, the simulator, replay, and the network bridge."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QVBoxLayout

from ...engine.monitor import MonitorEngine, Snapshot
from ...sim.physiology import SCENARIOS
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

BAUDS = [9600, 57600, 115200, 230400, 250000, 500000]


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
        self.rate = NumberEdit(100, 1, 200)
        self.rate_form.add(
            "rate",
            "Sample rate per channel",
            hbox(self.rate, label("Hz", "muted"), button("Send", self._send_rate, small=True), button("Ask INFO", lambda: engine.send("INFO"), small=True)),
            hint="Sends RATE to the device (1–200 Hz). 100 Hz is enough for breathing waveforms.",
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
        f.add("baud", "Baud rate", combo([(b, str(b)) for b in BAUDS], s.baud_rate, lambda v: engine.update_settings(baud_rate=v), 120), hint="Must match BAUD_RATE in firmware/CO2Response/config.h.")
        self.autoreconnect = toggle(s.auto_reconnect, lambda v: engine.update_settings(auto_reconnect=v))
        f.add(
            "auto",
            "Reconnect when plugged back in",
            self.autoreconnect,
            hint="If the USB cable comes out, the device-disconnected alarm sounds; plugging the same Arduino back in reconnects without a click, and recording continues.",
        )
        usb.add(f)
        self.reconnect = button("", self._reconnect, kind="primary")
        usb.add(hbox(self.reconnect, button("Connect to selected port", self._connect_port, kind="primary"), stretch_end=True))
        cards.add(usb)

        # ---- simulator
        sim = Card("Simulator", "no hardware needed")
        sim.add(label("Generates realistic capnogram, flow and pneumotach signals through the same protocol, parser and processing as the Arduino.", "note", wrap=True))
        f = Form()
        self.scenario = combo(SCENARIOS, s.sim.scenario, lambda v: engine.set_sim(scenario=v), 240)
        f.add("scenario", "Scenario", self.scenario)
        self.sim_rr = number_field(f, "rr", "Respiratory rate", s.sim.rr, lambda v: engine.set_sim(rr=v), "/min", lo=4, hi=60)
        self.sim_et = number_field(f, "et", "Baseline EtCO2", s.sim.etco2, lambda v: engine.set_sim(etco2=v), "mmHg", lo=10, hi=80)
        self.sim_vt = number_field(f, "vt", "Tidal volume", s.sim.vt_ml, lambda v: engine.set_sim(vt_ml=v), "mL", lo=100, hi=1500)
        sim.add(f)
        self.sim_btn = button("Start simulator", lambda: engine.connect("simulator"), kind="primary")
        sim.add(hbox(self.sim_btn, stretch_end=True))
        cards.add(sim)

        # ---- replay
        rep = Card("Replay a saved session")
        f = Form()
        self.session = QComboBox()
        self.session.setFixedWidth(300)
        f.add("session", "Session", self.session)
        self.speed = Seg([(1, "1×"), (2, "2×"), (5, "5×"), (10, "10×")], 1)
        f.add("speed", "Speed", self.speed)
        self.use_file_cal = toggle(True, lambda v: None)
        f.add("cal", "Use the calibration saved in the file", self.use_file_cal, hint="On: replays exactly as recorded. Off: re-processes the raw volts with the current calibration.")
        rep.add(f)
        self.no_sessions = label("No saved sessions yet. Record one, or import a file in Data & recordings.", "note", wrap=True)
        rep.add(self.no_sessions)
        self.replay_btn = button("Start replay", self._replay, kind="primary")
        rep.add(hbox(self.replay_btn, button("Replay a file…", self._replay_file), stretch_end=True))
        cards.add(rep)

        # ---- bridge
        br = Card("Python bridge", "Arduino on another computer")
        br.add(
            label(
                "Run python bridge/serial_ws_bridge.py --port COM3 --host 0.0.0.0 on the computer with the Arduino, then connect here to watch it from this one.",
                "note",
                wrap=True,
            )
        )
        f = Form()
        self.bridge_url = QLineEdit(s.bridge_url)
        self.bridge_url.setFixedWidth(260)
        self.bridge_url.editingFinished.connect(lambda: engine.update_settings(bridge_url=self.bridge_url.text().strip()))
        f.add("url", "Bridge address", self.bridge_url)
        br.add(f)
        br.add(hbox(button("Connect to bridge", self._bridge, kind="primary"), stretch_end=True))
        cards.add(br)
        lay.addWidget(cards)
        lay.addStretch(1)
        self._lib_version = -1
        self._refresh_ports()

    # ---------- actions

    def _send_rate(self) -> None:
        self.engine.send(f"RATE {round(self.rate.value() or 100)}")
        self.engine.send("INFO")

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
            s = self.engine.library.parse(sid)
            if not s.n:
                raise ValueError("it has no samples")
        except (OSError, ValueError) as e:
            self.window.toast(f"Could not replay {rec.file_name}: {e}", 6000)
            return
        self.engine.connect("replay", session=s, file_name=rec.file_name, speed=self.speed.value() or 1, use_file_calibration=self.use_file_cal.isChecked())

    def _replay_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Replay a session file", str(downloads_dir()), "Session files (*.csv);;All files (*)")
        if not path:
            return
        try:
            s = read_session(path)
            if not s.n:
                raise ValueError("it has no samples")
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
        st = {"connected": f"connected to {s.source_label}", "connecting": "connecting…", "error": f"lost ({s.status_detail})"}.get(s.status, "not connected")
        self.status.setText(f"Status: <b>{st}</b>")
        connected = s.status == "connected"
        self.dev.setVisible(connected)
        if connected:
            self.info.set_all(list((s.info or {"INFO": "waiting for reply"}).items()))
            self.rate_form.setVisible(s.source_kind in ("serial", "bridge", "simulator"))
        self.reconnect.setVisible(bool(s.known_port))
        self.reconnect.setText(f"Reconnect {s.known_port}" if s.known_port else "")
        self.sim_btn.setText("Restart simulator" if s.source_kind == "simulator" else "Start simulator")
        sim = s.settings.sim
        set_combo(self.scenario, sim.scenario)
        self.sim_rr.set(sim.rr)
        self.sim_et.set(sim.etco2)
        self.sim_vt.set(sim.vt_ml)
        if s.recording.library_version != self._lib_version or force:
            self._lib_version = s.recording.library_version
            cur = self.session.currentData()
            self.session.clear()
            for r in self.engine.library.list():
                if r.status != "recording":
                    self.session.addItem(r.file_name, r.id)
            set_combo(self.session, cur)
            has = self.session.count() > 0
            self.no_sessions.setVisible(not has)
            self.replay_btn.setEnabled(has)
        if force:
            self._refresh_ports()
