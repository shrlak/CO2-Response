"""Browser adapters for the engine: Web Serial (Arduino), WebSocket (bridge), Web Audio
(alarm tones), persistent storage and the screen wake lock."""

from __future__ import annotations

import asyncio
import collections

import js
from pyodide.ffi import create_proxy

from ..protocol import LineSplitter
from ..sources.base import Source, SourceItem, StatusEvent
from .dom import obj, proxy

# ------------------------------------------------------------------ Web Serial


def web_serial_supported() -> bool:
    return hasattr(js.navigator, "serial") and js.navigator.serial is not None


def port_usb_id(port) -> str:
    info = port.getInfo()
    vid, pid = getattr(info, "usbVendorId", None), getattr(info, "usbProductId", None)
    return f"{vid:04x}:{(pid or 0):04x}:" if vid is not None else ""


def port_label(port) -> str:
    u = port_usb_id(port)
    return f"Arduino (USB {u[:9]})" if u else "Arduino (USB)"


async def granted_ports() -> list:
    if not web_serial_supported():
        return []
    try:
        ports = await js.navigator.serial.getPorts()
        return [ports[i] for i in range(ports.length)]
    except Exception:  # noqa: BLE001
        return []


async def find_known_port(last_usb: str):
    """A port the user already allowed that matches the last Arduino (or the only one)."""
    ports = await granted_ports()
    match = next((p for p in ports if last_usb and port_usb_id(p) == last_usb), None)
    if match is None and len(ports) == 1:
        match = ports[0]
    return match


class WebSerialSource(Source):
    """The Arduino over the Web Serial API (desktop Chrome or Edge). The port is
    opened before the source is handed to the engine."""

    kind = "serial"

    def __init__(self, port) -> None:
        self.port = port
        self.label = port_label(port)
        self._q: collections.deque[SourceItem] = collections.deque()
        self._closing = False
        self._reader = None
        self._writer = None

    def start(self) -> None:
        self._writer = self.port.writable.getWriter()
        asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        split = LineSplitter()
        try:
            self._reader = self.port.readable.getReader()
            while not self._closing:
                r = await self._reader.read()
                if r.done:
                    break
                for line in split.push(r.value.to_bytes().decode("ascii", errors="replace")):
                    self._q.append(line)
            if not self._closing:
                self._q.append(StatusEvent("error", "Device stopped sending (USB unplugged or board reset)"))
        except Exception as e:  # noqa: BLE001
            if not self._closing:
                self._q.append(StatusEvent("error", f"Device disconnected (USB unplugged or reset): {e}"))
        finally:
            try:
                self._reader.releaseLock()
            except Exception:  # noqa: BLE001
                pass

    def poll(self) -> list[SourceItem]:
        out = list(self._q)
        self._q.clear()
        return out

    def send(self, command: str) -> None:
        if self._writer is not None:
            try:
                self._writer.write(js.TextEncoder.new().encode(command.strip() + "\n"))
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._closing = True
        asyncio.ensure_future(self._close())

    async def _close(self) -> None:
        for step in (lambda: self._reader and self._reader.cancel(), lambda: self._writer and self._writer.releaseLock(), lambda: self.port.close()):
            try:
                r = step()
                if hasattr(r, "then"):
                    await r
            except Exception:  # noqa: BLE001
                pass


async def open_port(port, baud: int) -> None:
    await port.open(obj({"baudRate": baud, "bufferSize": 65536}))


# ------------------------------------------------------------------ bridge over WebSocket


class WebBridgeSource(Source):
    kind = "bridge"

    def __init__(self, ws, url: str) -> None:
        self.ws = ws
        self.label = f"Bridge {url}"
        self._q: collections.deque[SourceItem] = collections.deque()
        self._closing = False
        self._split = LineSplitter()
        self._procs = []

    def start(self) -> None:
        def on_message(ev):
            text = str(ev.data)
            for line in self._split.push(text if text.endswith("\n") else text + "\n"):
                self._q.append(line)

        def on_close(ev):
            if not self._closing:
                self._q.append(StatusEvent("error", "Bridge connection closed"))

        self._procs = [create_proxy(on_message), create_proxy(on_close)]
        self.ws.onmessage, self.ws.onclose = self._procs

    def poll(self) -> list[SourceItem]:
        out = list(self._q)
        self._q.clear()
        return out

    def send(self, command: str) -> None:
        if self.ws.readyState == 1:
            self.ws.send(command.strip())

    def stop(self) -> None:
        self._closing = True
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


async def open_websocket(url: str, timeout: float = 4.0):
    ws = js.WebSocket.new(url)
    fut = asyncio.get_event_loop().create_future()
    ok = create_proxy(lambda ev: fut.done() or fut.set_result(True))
    bad = create_proxy(lambda ev: fut.done() or fut.set_result(False))
    ws.onopen, ws.onerror = ok, bad
    try:
        opened = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        opened = False
    ok.destroy()
    bad.destroy()
    ws.onopen = ws.onerror = None
    if not opened:
        ws.close()
        raise RuntimeError(f"Could not reach the bridge at {url}. Is serial_ws_bridge.py running?")
    return ws


# ------------------------------------------------------------------ Web Audio


class WebAudio:
    """Alarm tones with the Web Audio API, patterned after IEC 60601-1-8: high = 10
    pulses (5 + 5), medium = 3 pulses. Browsers allow sound only after a click, so
    ``arm`` must be called from a click."""

    def __init__(self) -> None:
        self.ctx = None
        self.volume = 0.8
        self.error = ""

    @property
    def armed(self) -> bool:
        return self.ctx is not None and self.ctx.state == "running"

    @property
    def backend(self) -> str:
        return "speaker" if self.armed else "none"

    def arm(self, confirm: bool = True) -> bool:
        try:
            if self.ctx is None:
                self.ctx = js.AudioContext.new()
            p = self.ctx.resume()
            if confirm:
                p.then(proxy(lambda *_: self.confirm()))
            return True
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            return False

    def _pulses(self, freq: float, count: int, group_of: int, pulse: float = 0.15, gap: float = 0.1, group_gap: float = 0.35) -> None:
        ctx = self.ctx
        if not self.armed:
            return
        peak = 0.25 * max(0.1, min(1.0, self.volume))
        t = ctx.currentTime + 0.02
        for i in range(count):
            o = ctx.createOscillator()
            g = ctx.createGain()
            o.type = "square"
            o.frequency.value = freq
            g.gain.setValueAtTime(0, t)
            g.gain.linearRampToValueAtTime(peak, t + 0.015)
            g.gain.setValueAtTime(peak, t + pulse - 0.02)
            g.gain.linearRampToValueAtTime(0, t + pulse)
            o.connect(g)
            g.connect(ctx.destination)
            o.start(t)
            o.stop(t + pulse + 0.01)
            t += pulse + gap + (group_gap if (i + 1) % group_of == 0 else 0)

    def high(self) -> None:
        self._pulses(960, 10, 5)

    def medium(self) -> None:
        self._pulses(660, 3, 3, 0.2, 0.15)

    def confirm(self) -> None:
        self._pulses(880, 1, 1, 0.12)


# ------------------------------------------------------------------ storage


class Storage:
    """Settings, calibration and sessions live in /data, which is kept in the
    browser's IndexedDB. ``sync`` writes changes back; call it about once a second."""

    MOUNT = "/data"

    def __init__(self) -> None:
        import pyodide_js

        self.FS = pyodide_js.FS
        self._busy = False
        self._done = create_proxy(self._synced)
        self.error = ""

    async def mount(self) -> None:
        FS = self.FS
        FS.mkdirTree(self.MOUNT)
        FS.mount(FS.filesystems.IDBFS, obj({}), self.MOUNT)
        fut = asyncio.get_event_loop().create_future()
        FS.syncfs(True, create_proxy(lambda err=None: fut.done() or fut.set_result(err)))
        err = await fut
        if err:
            self.error = str(err)

    def _synced(self, err=None) -> None:
        self._busy = False
        if err:
            self.error = str(err)

    def sync(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.FS.syncfs(False, self._done)

    async def persist(self) -> bool:
        """Ask the browser not to evict saved sessions when disk space runs low."""
        try:
            return bool(await js.navigator.storage.persist())
        except Exception:  # noqa: BLE001
            return False

    async def estimate(self) -> tuple[float, float] | None:
        try:
            e = await js.navigator.storage.estimate()
            return float(e.usage or 0), float(e.quota or 0)
        except Exception:  # noqa: BLE001
            return None


class WakeLock:
    """Keeps the screen on while a device is connected (where the browser allows it)."""

    def __init__(self) -> None:
        self.sentinel = None
        self.wanted = False

    async def acquire(self) -> None:
        self.wanted = True
        if self.sentinel is not None or not hasattr(js.navigator, "wakeLock"):
            return
        try:
            self.sentinel = await js.navigator.wakeLock.request("screen")
        except Exception:  # noqa: BLE001
            self.sentinel = None  # not granted (e.g. tab hidden); retried when visible

    async def release(self) -> None:
        self.wanted = False
        s, self.sentinel = self.sentinel, None
        if s is not None:
            try:
                await s.release()
            except Exception:  # noqa: BLE001
                pass
