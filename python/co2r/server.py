"""
WebSocket server: sends every line from a Source to all connected dashboards and
passes their commands back. The dashboard connects from Settings > Connection >
Python (default ws://localhost:8765).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable

import websockets

from .sources import Source


class Hub:
    """Collects lines from the source thread and sends them in batches every 20 ms."""

    def __init__(self, loop: asyncio.AbstractEventLoop, source: Source, log: Callable[[str], None] = print) -> None:
        self.loop = loop
        self.source = source
        self.log = log
        self.clients: dict[object, asyncio.Queue] = {}
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self.lines_sent = 0

    def publish(self, line: str) -> None:
        """Called from the source thread."""
        with self._lock:
            self._pending.append(line)

    async def pump(self) -> None:
        while True:
            await asyncio.sleep(0.02)
            with self._lock:
                lines, self._pending = self._pending, []
            if not lines:
                continue
            self.lines_sent += len(lines)
            msg = "\n".join(lines)
            for ws, q in list(self.clients.items()):
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    self.log("a client is not keeping up; disconnecting it")
                    self.clients.pop(ws, None)
                    asyncio.ensure_future(_close(ws))

    async def handler(self, ws, *_args) -> None:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)  # ~10 s of batches
        self.clients[ws] = q
        peer = getattr(ws, "remote_address", "?")
        self.log(f"dashboard connected: {peer} ({len(self.clients)} total)")

        async def sender() -> None:
            while True:
                await ws.send(await q.get())

        task = asyncio.ensure_future(sender())
        try:
            async for msg in ws:
                for cmd in str(msg).splitlines():
                    if cmd.strip():
                        await self.loop.run_in_executor(None, self.source.command, cmd.strip())
        except websockets.ConnectionClosed:
            pass
        finally:
            task.cancel()
            self.clients.pop(ws, None)
            self.log(f"dashboard left: {peer} ({len(self.clients)} total)")


async def _close(ws) -> None:
    try:
        await ws.close()
    except Exception:
        pass


async def serve(source: Source, host: str = "127.0.0.1", port: int = 8765, log: Callable[[str], None] = print, ready: Callable[[int], None] | None = None, stop: asyncio.Event | None = None) -> None:
    loop = asyncio.get_running_loop()
    hub = Hub(loop, source, log)
    source.start(hub.publish, log)
    pump = asyncio.ensure_future(hub.pump())
    try:
        async with websockets.serve(hub.handler, host, port, max_size=2**16) as server:
            actual = next(iter(server.sockets)).getsockname()[1] if getattr(server, "sockets", None) else port
            log(f"serving on ws://{host}:{actual}  (Ctrl+C to stop)")
            if ready:
                ready(actual)
            await (stop.wait() if stop else asyncio.Future())
    finally:
        pump.cancel()
        source.stop()
