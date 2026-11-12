"""The monitor running in a browser tab (the GitHub Pages build).

``site/index.html`` loads Pyodide, unpacks this package and awaits ``main()``. From
there everything is Python: the same engine as the desktop app (sources, processing,
alarms, recorder), with browser adapters for Web Serial, Web Audio and storage, and
an HTML front end.
"""

from __future__ import annotations

import asyncio
import os
import time

import js

from .. import APP_NAME, __version__
from ..engine.monitor import MonitorEngine
from ..storage.library import ChunkedSessionLibrary
from ..storage.session_file import parse_session_text
from . import dom
from .dom import actions, byid, data, nn, proxy, run, set_html, toast
from .platform import Storage, WakeLock, WebAudio, WebBridgeSource, WebSerialSource, find_known_port, open_port, open_websocket, port_label, port_usb_id, web_serial_supported
from .review_view import ReviewView, install_review_actions
from .settings_view import SettingsView, install_settings_actions
from .sim_view import install_sim_actions
from .views import MonitorView, alarm_slot_html, header_html, install_alarm_actions, install_monitor_actions

SAMPLE = "sample-data/synthetic_session.csv"


class WebApp:
    def __init__(self, engine: MonitorEngine, storage: Storage) -> None:
        self.engine = engine
        self.storage = storage
        self.wake = WakeLock()
        self.screen = "monitor"
        self.theme = "dark"
        self.alarm_list_open = False
        self.known_port = None
        self.known_label: str | None = None
        self.monitor = MonitorView(self)
        self.settings = SettingsView(self)
        self.review = ReviewView(self)
        self.hotkey_mark = None
        self._title_flip = False
        self._last_sync = 0.0
        self._last_status = ""

    # ---------- shell and screens

    def render_shell(self) -> None:
        byid("root").innerHTML = (
            '<div class="app"><header class="header" id="hdr"></header><div id="alarmslot"></div><div id="screen" style="min-height:0;display:grid"></div>'
            '<div class="toast" id="toast" role="status" hidden></div></div>'
        )
        dom.forget()

    def show(self, screen: str, arg: str | None = None) -> None:
        self.screen = screen
        dom.forget()
        if screen == "monitor":
            set_html("screen", self.monitor.html(), force=True)
            self.monitor.mount()
        elif screen == "settings":
            set_html("screen", self.settings.html(), force=True)
            self.settings.mount()
        else:
            self.review.open(arg)
            set_html("screen", self.review.html(), force=True)
            self.review.mount()
        self.refresh()

    def rerender(self, force: bool = False) -> None:
        self.settings.version += 1
        self.refresh()

    def apply_theme(self) -> None:
        pref = self.engine.settings.theme
        if pref == "system":
            pref = "light" if js.window.matchMedia("(prefers-color-scheme: light)").matches else "dark"
        if pref != self.theme:
            self.theme = pref
            js.document.documentElement.dataset.theme = pref
            if self.screen == "review":
                self.show("review", self.review.sid)

    # ---------- timers

    def refresh(self) -> None:
        e = self.engine
        s = e.snapshot()
        set_html("hdr", header_html(s))
        set_html("alarmslot", alarm_slot_html(s, self.alarm_list_open))
        if self.screen == "monitor":
            self.monitor.refresh(s)
        elif self.screen == "settings":
            self.settings.refresh(s)

    def tick(self) -> None:
        e = self.engine
        e.tick()
        self.apply_theme()
        self.refresh()
        now = time.monotonic()
        if now - self._last_sync >= 1.0:  # recordings and settings to the browser's storage
            self._last_sync = now
            self.storage.sync()
        s = e.snapshot()
        if s.status != self._last_status:
            self._last_status = s.status
            run(self.wake.acquire() if s.status == "connected" else self.wake.release())
            if s.status != "connected":
                run(self.refresh_known_port())
        if e.alarms.active_priority() == "high":
            self._title_flip = not self._title_flip
            js.document.title = f"▲ ALARM · {APP_NAME}" if self._title_flip else f"{APP_NAME} Monitor"
        elif js.document.title != f"{APP_NAME} Monitor":
            js.document.title = f"{APP_NAME} Monitor"

    def frame(self, _ts=None) -> None:
        try:
            if self.screen == "monitor":
                self.monitor.frame()
            else:
                self.engine.take_waves()
        finally:
            js.requestAnimationFrame(self._frame_proxy)

    # ---------- connecting

    async def refresh_known_port(self) -> None:
        p = await find_known_port(self.engine.settings.last_port_usb)
        self.known_port = p
        self.known_label = port_label(p) if p is not None else None

    async def open_serial(self, port, auto: bool = False) -> bool:
        e = self.engine
        try:
            await open_port(port, e.settings.baud_rate)
        except Exception as ex:  # noqa: BLE001
            msg = str(ex)
            e._fail(f"Could not open the serial port: {msg}. Close the Arduino IDE Serial Monitor and other tabs using the board.")
            if auto:
                e._awaiting_reconnect = True
            self.refresh()
            return False
        e.update_settings(last_port_usb=port_usb_id(port))
        ok = e.connect_source(WebSerialSource(port), auto=auto)
        self.known_port, self.known_label = port, port_label(port)
        if ok and self.screen != "monitor":
            self.show("monitor")
        self.refresh()
        return ok

    async def choose_port(self) -> None:
        if not web_serial_supported():
            toast("This browser has no Web Serial. Use desktop Chrome or Edge, or Python acquisition (co2r-daq).", 6000)
            return
        try:
            port = await js.navigator.serial.requestPort()  # first await: still part of the click
        except Exception:  # noqa: BLE001  (the user closed the picker)
            return
        await self.open_serial(port)

    async def reconnect(self) -> None:
        p = self.known_port or await find_known_port(self.engine.settings.last_port_usb)
        if p is None:
            await self.choose_port()
        else:
            await self.open_serial(p)

    async def on_usb_connect(self, port) -> None:
        await self.refresh_known_port()
        e = self.engine
        if not e.awaiting_reconnect or e.source is not None or not e.settings.auto_reconnect:
            return
        want = e.settings.last_port_usb
        if want and port_usb_id(port) != want:
            return
        e._add_log("Arduino plugged back in: reconnecting")
        await asyncio.sleep(1.5)  # let the board boot after it enumerates
        if e.source is None and e.awaiting_reconnect:
            await self.open_serial(port, auto=True)

    async def connect_bridge(self) -> None:
        e = self.engine
        url = e.settings.bridge_url
        try:
            ws = await open_websocket(url)
        except Exception as ex:  # noqa: BLE001
            e._fail(str(ex))
            toast(str(ex), 6000)
            self.refresh()
            return
        if e.connect_source(WebBridgeSource(ws, url)):
            self.show("monitor")

    async def replay_sample(self) -> None:
        try:
            r = await js.fetch(SAMPLE)
            text = await r.text()
            s = parse_session_text(text)
        except Exception as ex:  # noqa: BLE001
            toast(f"Could not load the sample session: {ex}", 6000)
            return
        self.engine.connect("replay", session=s, file_name="synthetic_session.csv", speed=1)
        self.show("monitor")

    def toggle_recording(self) -> None:
        e = self.engine
        if e.recorder.active:
            res = e.stop_recording()
            self.storage.sync()
            if res.rec is not None:
                dom.download(res.file_name, list(e.library.read_chunks(res.rec.id)))
                toast(f"Saved {res.file_name}. It is also kept in Settings > Data & recordings.", 5000)
            else:
                toast(f"Could not keep {res.file_name} in this browser ({res.error}).", 7000)
        else:
            e.start_recording()
            toast(f"Recording {e.recorder.file_name}. Saved to this browser every second.")
        self.refresh()


def _install_actions(app: WebApp) -> None:
    e = app.engine

    def open_settings(el):
        if data(el, "arg"):
            app.settings.tab = data(el, "arg")
        app.show("settings")

    actions(
        ack=lambda el: (e.acknowledge(data(el, "arg")), app.refresh()),
        record=lambda el: app.toggle_recording(),
        reconnect=lambda el: app.reconnect(),
        **{
            "alarm-more": lambda el: (setattr(app, "alarm_list_open", not app.alarm_list_open), app.refresh()),
            "arm-audio": lambda el: (e.arm_audio(), app.refresh()),
            "start-sim": lambda el: (e.connect("simulator"), app.monitor.sim.bump(), app.show("monitor")),
            "connect-serial": lambda el: app.choose_port(),
            "connect-bridge": lambda el: app.connect_bridge(),
            "replay-sample": lambda el: app.replay_sample(),
            "open-settings": open_settings,
            "show-monitor": lambda el: app.show("monitor"),
        },
    )
    install_alarm_actions(app)
    install_monitor_actions(app)
    install_sim_actions(app)
    install_settings_actions(app)
    install_review_actions(app)

    def on_key(ev):
        t = nn(ev.target)
        typing = t is not None and (t.tagName in ("INPUT", "TEXTAREA", "SELECT") or t.isContentEditable)
        if typing or ev.metaKey or ev.ctrlKey or ev.altKey:
            # Enter commits a typed value: leave the field so the page can redraw with it
            if ev.key == "Enter" and t is not None and t.tagName == "INPUT" and (t.id == "mark-label" or t.hasAttribute("data-change")):
                t.blur()
            return
        if ev.key in ("f", "F") and app.screen == "monitor":
            from .views import toggle_fullscreen

            ev.preventDefault()
            toggle_fullscreen()
        if ev.key in ("m", "M") and app.screen == "monitor" and app.hotkey_mark:
            ev.preventDefault()
            app.hotkey_mark()
        if ev.key == "Escape":
            app.monitor.drawer = False
            app.monitor.mark = None
            if app.monitor.focus:
                app.monitor.set_focus(None)
            app.refresh()

    js.document.addEventListener("keydown", proxy(on_key))
    if web_serial_supported():
        js.navigator.serial.addEventListener("connect", proxy(lambda ev: run(app.on_usb_connect(ev.target))))
        js.navigator.serial.addEventListener("disconnect", proxy(lambda ev: run(app.refresh_known_port())))

    def on_visible(ev):
        if js.document.visibilityState == "visible" and app.wake.wanted:
            run(app.wake.acquire())

    js.document.addEventListener("visibilitychange", proxy(on_visible))


async def main() -> None:
    storage = Storage()
    await storage.mount()
    os.environ["CO2R_HOME"] = Storage.MOUNT
    engine = MonitorEngine(audio=WebAudio(), port_lister=lambda: [], prevent_sleep=False, library_factory=ChunkedSessionLibrary)
    engine.audio_off_message = "Audible alarm NOT armed. Press Arm audio."
    app = WebApp(engine, storage)
    js.window.co2r = app  # for the browser console and the smoke test
    dom.install()
    _install_actions(app)
    app.render_shell()
    app.apply_theme()
    recovered = engine.init()
    await app.refresh_known_port()
    app.show("monitor")
    if recovered:
        n = len(recovered)
        toast(f"Recovered {n} recording{'s' if n > 1 else ''} left unfinished when the page closed. Find {'them' if n > 1 else 'it'} in Settings > Data & recordings.", 9000)
    js.setInterval(proxy(engine.poll), 20)
    js.setInterval(proxy(app.tick), 250)
    app._frame_proxy = proxy(app.frame)
    js.requestAnimationFrame(app._frame_proxy)
    usage = await storage.estimate()
    app.settings.usage = usage
    params = js.URLSearchParams.new(js.location.search)
    if params.has("sim") or js.location.hash == "#sim":
        engine.connect("simulator")
        app.refresh()
    js.console.log(f"{APP_NAME} {__version__} running in Python {os.sys.version.split()[0]} (Pyodide)")
