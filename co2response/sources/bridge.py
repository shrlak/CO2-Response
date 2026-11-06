"""Lines from the Python acquisition server (python/co2r, ``co2r-daq serve``, which is
also what bridge/serial_ws_bridge.py runs) over a WebSocket: an NI, LabJack or simulated
DAQ, or an Arduino plugged into another computer on the lab network. The server sends
several lines per message."""

from __future__ import annotations

import threading

from ..protocol import LineSplitter
from .base import StatusEvent, ThreadedSource


class BridgeSource(ThreadedSource):
    kind = "bridge"

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.label = f"Python {url}"
        self._ws = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as e:
            raise RuntimeError("The websockets package is not installed (pip install websockets).") from e
        try:
            # entered by hand (closed in stop()); newer websockets warn about a bare connect()
            self._ws = connect(self.url, open_timeout=3, close_timeout=1).__enter__()
        except Exception as e:
            raise RuntimeError(f"Could not reach {self.url}. Is `co2r-daq serve` running? ({e})") from e
        self._closing = False
        self._thread = threading.Thread(target=self._read_loop, name="bridge", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        split = LineSplitter()
        try:
            for msg in self._ws:
                text = msg if isinstance(msg, str) else msg.decode("ascii", errors="replace")
                for line in split.push(text if text.endswith("\n") else text + "\n"):
                    self._q.put(line)
        except Exception:
            pass
        if not self._closing:
            self._q.put(StatusEvent("error", "Python acquisition server closed the connection"))

    def send(self, command: str) -> None:
        try:
            if self._ws is not None:
                self._ws.send(command.strip())
        except Exception:
            pass

    def stop(self) -> None:
        self._closing = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._ws = None
        self._thread = None
