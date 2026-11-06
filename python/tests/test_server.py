"""End to end: a simulated DAQ served over WebSocket, read by a client the way the
dashboard reads it (lines may arrive several to a message)."""
import asyncio

import websockets
from co2r.drivers.simulated import SimulatedDriver
from co2r.protocol import SeqTracker, parse_line
from co2r.server import serve
from co2r.sources import SampledSource


def test_serves_lines_and_answers_commands():
    async def main():
        stop = asyncio.Event()
        port_ready: asyncio.Future = asyncio.get_running_loop().create_future()
        src = SampledSource(SimulatedDriver(seed=2), rate_hz=100)
        server = asyncio.ensure_future(serve(src, "127.0.0.1", 0, log=lambda m: None, ready=port_ready.set_result, stop=stop))
        port = await asyncio.wait_for(port_ready, 5)
        lines = []
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send("INFO")
            await ws.send("RATE 50")
            deadline = asyncio.get_running_loop().time() + 2.5
            while asyncio.get_running_loop().time() < deadline:
                msg = await asyncio.wait_for(ws.recv(), 2)
                lines += str(msg).split("\n")
        stop.set()
        await asyncio.wait_for(server, 5)
        return lines

    lines = asyncio.run(main())
    parsed = [parse_line(ln) for ln in lines]
    assert any(p["kind"] == "info" and p["fields"]["ADC"] == "Simulated DAQ" for p in parsed)
    assert "A,RATE,50" in lines
    data = [p for p in parsed if p["kind"] == "data"]
    assert len(data) > 100
    assert not [p for p in parsed if p["kind"] == "bad"]
    seq = SeqTracker()
    for d in data:
        seq.push(d["seq"], d["tMs"])
    assert seq.dropped == 0


def test_two_dashboards_get_the_same_stream():
    async def main():
        stop = asyncio.Event()
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        server = asyncio.ensure_future(serve(SampledSource(SimulatedDriver(seed=3)), "127.0.0.1", 0, log=lambda m: None, ready=ready.set_result, stop=stop))
        port = await ready
        async with websockets.connect(f"ws://127.0.0.1:{port}") as a, websockets.connect(f"ws://127.0.0.1:{port}") as b:
            ma = str(await asyncio.wait_for(a.recv(), 2))
            mb = str(await asyncio.wait_for(b.recv(), 2))
        stop.set()
        await asyncio.wait_for(server, 5)
        return ma, mb

    ma, mb = asyncio.run(main())
    assert ma.startswith("D,") or ma.startswith("I,")
    assert mb.startswith("D,") or mb.startswith("I,")
