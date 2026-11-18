"""Plays a recorded session back through the normal parser and processing path.

The replay runs on the recording's own time (``time_s``), which never goes backwards,
not even across a reconnect or a resumed recording. Before each sample and marker it
hands the engine a ``ClockEvent`` with that time, so the engine's clock follows the
recording: the trends, the test's timers and the alarm delays come out exactly as they
were, at any speed, paused, or jumped forward. It can pause, change speed and jump
forward (``seek``); when it reaches the end it stays there (``at_end``), with everything
on the monitor, until it is stopped or sent back.
"""

from __future__ import annotations

import bisect
import time
from typing import Callable

from ..jsfmt import js_round
from ..protocol import format_data_line
from ..storage.session_file import ParsedSession, session_markers
from .base import ClockEvent, MarkerEvent, Source, SourceItem

#: lines handed over per poll at most (a jump forward or a high speed catches up in steps,
#: so the window stays responsive)
MAX_PER_POLL = 5000


class ReplaySource(Source):
    kind = "replay"

    def __init__(self, session: ParsedSession, file_name: str, speed: float = 1, clock: Callable[[], float] = time.monotonic) -> None:
        self.session = session
        self.file_name = file_name
        self.label = f"Replay {file_name}"
        self.speed = speed
        #: the computer's clock, s: how fast the recording's time moves on
        self._clock = clock
        self._seq = session.seq.tolist()
        self._v = list(zip(session.v0.tolist(), session.v1.tolist(), session.v2.tolist()))
        if len(session.v3) == session.n and session.n and bool((session.v3 == session.v3).any()):
            # a fourth input was recorded (the oximeter): replay it too
            self._v = [(a, b, c, d if d == d else 0.0) for (a, b, c), d in zip(self._v, session.v3.tolist())]  # type: ignore[misc]
        self._time_s = session.time_s.tolist()
        self._t0 = float(self._time_s[0]) if self._time_s else 0.0
        # the recording's own markers are replayed too, so a rebreathing test is
        # split and fitted the same way it was live
        self._marks = sorted(session_markers(session.events))
        self._mi = 0
        self._i = 0
        #: the recording's time (s from its first sample) the replay has reached by now:
        #: ``_target0 + (clock - _wall0) * speed`` while playing
        self._target0 = 0.0
        self._wall0 = 0.0
        self.paused = False
        self._out: list[SourceItem] = []

    # ---------- where it is

    @property
    def length_s(self) -> float:
        return float(self._time_s[-1] - self._t0) if self._time_s else 0.0

    @property
    def position_s(self) -> float:
        """How far into the recording the replay has played, s."""
        if not self._time_s or not self._i:
            return 0.0
        return float(self._time_s[min(self._i, len(self._time_s)) - 1] - self._t0)

    @property
    def progress(self) -> float:
        n = self.session.n
        return self._i / n if n else 1.0

    @property
    def at_end(self) -> bool:
        """Every sample has been played: the monitor shows the whole recording."""
        return self._i >= len(self._time_s)

    @property
    def rate_hz(self) -> int:
        n = len(self._time_s)
        if n < 2:
            return 100
        k = min(n - 1, 200)
        span = self._time_s[k] - self._time_s[0]
        return js_round(k / span) if span > 0 else 100

    def _target(self) -> float:
        if self.paused:
            return self._target0
        return self._target0 + (self._clock() - self._wall0) * self.speed

    def _anchor(self, target: float) -> None:
        self._target0 = target
        self._wall0 = self._clock()

    # ---------- controls

    def start(self) -> None:
        fw = self.session.meta.get("firmware") or "replay"
        self._out = [f"I,FW,{fw},ADC,FILE,CH,{len(self._v[0]) if self._v else 3},RATE,{self.rate_hz}"]
        self._i = 0
        self._mi = 0
        self._anchor(0.0)

    def set_paused(self, on: bool) -> None:
        if on == self.paused:
            return
        if on:
            self._target0 = self._target()
            self.paused = True
        else:
            self.paused = False
            self._anchor(self._target0)

    def set_speed(self, speed: float) -> None:
        """Change the speed from here on: the replay carries on from where it is."""
        self._anchor(self._target())
        self.speed = speed

    def seek(self, position_s: float) -> None:
        """Jump forward to ``position_s`` into the recording. Everything in between is
        still played (as fast as the computer manages), so every breath, marker and
        alarm on the way is on the monitor. Going back needs a fresh replay: the engine
        does that (``MonitorEngine.replay_seek``)."""
        pos = max(0.0, min(float(position_s), self.length_s))
        if pos > self._target():
            if self.paused:
                self._target0 = pos  # it goes there and stays paused
            else:
                self._anchor(pos)

    def poll(self) -> list[SourceItem]:
        out, self._out = self._out, []
        ts, n = self._time_s, len(self._time_s)
        if self._i >= n:
            return out
        due = self._t0 + self._target()
        # the samples due by now, at most MAX_PER_POLL of them
        stop = min(n, bisect.bisect_right(ts, due, lo=self._i), self._i + MAX_PER_POLL)
        marks, mi = self._marks, self._mi
        for i in range(self._i, stop):
            t = ts[i]
            while mi < len(marks) and marks[mi][0] <= t:
                out.append(ClockEvent(marks[mi][0]))
                out.append(MarkerEvent(marks[mi][1]))
                mi += 1
            out.append(ClockEvent(t))
            # the recording's time as the device clock: it never goes backwards, so a file
            # that spans a reconnect or a resume replays as one smooth stream
            out.append(format_data_line(self._seq[i], (t - self._t0) * 1000, self._v[i]))
        self._mi, self._i = mi, stop
        if self._i >= n:
            while self._mi < len(marks):  # markers after the last sample
                out.append(ClockEvent(marks[self._mi][0]))
                out.append(MarkerEvent(marks[self._mi][1]))
                self._mi += 1
        return out

    def stop(self) -> None:
        self._i = len(self._time_s)
