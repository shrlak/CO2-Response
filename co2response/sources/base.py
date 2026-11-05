"""Anything that produces protocol lines: the Arduino, the simulator, a CSV replay, the bridge.

Sources are polled: the monitor engine calls ``poll()`` many times a second and gets
back the lines that arrived since the last call, plus any status changes. Sources that
read hardware do so on a background thread and hand lines over through a queue.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class StatusEvent:
    #: 'disconnected' (with a reason, e.g. "Replay finished") or 'error'
    status: str
    detail: str = ""


SourceItem = Union[str, StatusEvent]


class Source:
    kind = "base"  # 'serial' | 'simulator' | 'replay' | 'bridge'
    label = ""

    def start(self) -> None:
        """Open the connection. Raises an exception with a readable message on failure."""

    def poll(self) -> list[SourceItem]:
        """Lines (without newline) and status events since the last call, in order."""
        return []

    def send(self, command: str) -> None:
        """Send a command line (INFO, START, STOP, RATE n) to the device."""

    def stop(self) -> None:
        """Close the connection. Safe to call more than once."""


class ThreadedSource(Source):
    """Base for sources that read on a background thread."""

    def __init__(self) -> None:
        self._q: queue.SimpleQueue[SourceItem] = queue.SimpleQueue()
        self._closing = False

    def poll(self) -> list[SourceItem]:
        out: list[SourceItem] = []
        try:
            while len(out) < 20000:
                out.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return out
