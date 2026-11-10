"""Plays a recorded session back through the normal parser and processing path."""

from __future__ import annotations

import time
from typing import Callable

from ..jsfmt import js_round
from ..protocol import format_data_line
from ..storage.session_file import ParsedSession, session_markers
from .base import MarkerEvent, Source, SourceItem, StatusEvent


class ReplaySource(Source):
    kind = "replay"

    def __init__(self, session: ParsedSession, file_name: str, speed: float = 1, clock: Callable[[], float] = time.monotonic) -> None:
        self.session = session
        self.label = f"Replay {file_name}"
        self.speed = speed
        self._clock = clock
        self._t = session.t_ms.tolist()
        self._seq = session.seq.tolist()
        self._v = list(zip(session.v0.tolist(), session.v1.tolist(), session.v2.tolist()))
        # the recording's own markers are replayed too, so a rebreathing test is
        # split and fitted the same way it was live
        self._time_s = session.time_s.tolist()
        self._marks = sorted(session_markers(session.events))
        self._mi = 0
        self._i = 0
        self._t0_device = 0.0
        self._t0_wall = 0.0
        self._out: list[SourceItem] = []
        self._done = False

    @property
    def progress(self) -> float:
        n = self.session.n
        return self._i / n if n else 1.0

    @property
    def rate_hz(self) -> int:
        n = self.session.n
        if n < 2:
            return 100
        k = min(n - 1, 200)
        span = self._t[k] - self._t[0]
        return js_round((k * 1000) / span) if span > 0 else 100

    def start(self) -> None:
        fw = self.session.meta.get("firmware") or "replay"
        self._out = [f"I,FW,{fw},ADC,FILE,CH,3,RATE,{self.rate_hz}"]
        self._i = 0
        self._mi = 0
        self._done = False
        self._t0_device = self._t[0] if self._t else 0.0
        self._t0_wall = self._clock()

    def poll(self) -> list[SourceItem]:
        out, self._out = self._out, []
        if self._done:
            return out
        t, n = self._t, len(self._t)
        device_now = self._t0_device + (self._clock() - self._t0_wall) * 1000 * self.speed
        count = 0
        while self._i < n and count < 5000:
            i = self._i
            # the device clock restarts if the recording spans a reconnect: re-anchor
            if i > 0 and t[i] < t[i - 1]:
                self._t0_device = t[i]
                self._t0_wall = self._clock()
                device_now = self._t0_device
            if t[i] > device_now:
                break
            while self._mi < len(self._marks) and self._marks[self._mi][0] <= self._time_s[i]:
                out.append(MarkerEvent(self._marks[self._mi][1]))
                self._mi += 1
            out.append(format_data_line(self._seq[i], t[i], self._v[i]))
            self._i += 1
            count += 1
        if self._i >= n:
            self._done = True
            out.append(StatusEvent("disconnected", "Replay finished"))
        return out

    def stop(self) -> None:
        self._done = True
