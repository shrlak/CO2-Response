#!/usr/bin/env python3
"""
Serial -> WebSocket bridge for the CO2-Response monitor.

Relays protocol lines from the Arduino to every connected browser (Safari, Firefox,
or a second screen) and forwards commands (INFO, START, STOP, RATE n) back to the
device. The lines are passed through untouched; the browser does all parsing,
checksums and processing. Protocol: docs/protocol.md

    pip install -r requirements.txt
    python serial_ws_bridge.py --list                 # show serial ports
    python serial_ws_bridge.py --port COM3            # Windows
    python serial_ws_bridge.py --port /dev/ttyACM0    # Linux / macOS
    python serial_ws_bridge.py --simulate             # no hardware: synthetic data

Then in the dashboard: Settings > Connection > Python bridge > ws://localhost:8765

By default the bridge listens on localhost only. Use --host 0.0.0.0 to let other
computers on the lab network watch (anyone who can reach the port can see the data
and send commands, so only do this on a trusted network).

RESEARCH USE ONLY. Not a medical device.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import threading
import time

import websockets

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # --simulate works without pyserial
    serial = None
    list_ports = None


def checksum(payload: str) -> str:
    """XOR of every byte after 'D' and before '*', as 2 hex digits."""
    x = 0
    for ch in payload.encode("ascii"):
        x ^= ch
    return f"{x:02X}"


def data_line(seq: int, t_ms: int, volts: list[float]) -> str:
    payload = f",{seq & 0xFFFF},{t_ms},{','.join(f'{v:.4f}' for v in volts)}"
    return f"D{payload}*{checksum(payload)}"


class Hub:
    """Fan lines out to all clients; hand commands to the device."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.clients: set = set()
        self.send_to_device = lambda cmd: None

    def publish(self, line: str) -> None:
        """Thread-safe: called from the serial reader thread."""
        self.loop.call_soon_threadsafe(self._broadcast, line)

    def _broadcast(self, line: str) -> None:
        dead = []
        for ws in self.clients:
            try:
                # websockets queues the frame; drop clients that cannot keep up
                if ws.transport is not None and ws.transport.get_write_buffer_size() > 1_000_000:
                    dead.append(ws)
                    continue
                asyncio.ensure_future(ws.send(line))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def handler(self, ws, *_):
        self.clients.add(ws)
        peer = getattr(ws, "remote_address", "?")
        print(f"client connected: {peer} ({len(self.clients)} total)", flush=True)
        try:
            async for msg in ws:
                for cmd in str(msg).splitlines():
                    cmd = cmd.strip()
                    if cmd:
                        self.send_to_device(cmd)
        finally:
            self.clients.discard(ws)
            print(f"client left: {peer} ({len(self.clients)} total)", flush=True)


def run_serial(hub: Hub, port: str, baud: int, stop: threading.Event) -> None:
    ser = serial.Serial(port, baud, timeout=0.2)
    lock = threading.Lock()

    def write(cmd: str) -> None:
        with lock:
            ser.write((cmd + "\n").encode("ascii", "ignore"))

    hub.send_to_device = write
    print(f"reading {port} at {baud} baud", flush=True)
    buf = b""
    while not stop.is_set():
        try:
            chunk = ser.read(4096)
        except serial.SerialException as e:
            print(f"serial error: {e}", file=sys.stderr, flush=True)
            hub.publish("E,BRIDGE_SERIAL_LOST")
            break
        if not chunk:
            continue
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            hub.publish(raw.rstrip(b"\r").decode("ascii", "replace"))
    ser.close()


def run_simulator(hub: Hub, rate: int, stop: threading.Event) -> None:
    """Rough synthetic signals in volts, matching the default channels.json scaling."""
    state = {"rate": rate, "streaming": True}

    def command(cmd: str) -> None:
        parts = cmd.upper().split()
        if not parts:
            return
        if parts[0] == "INFO":
            hub.publish(f"I,FW,bridge-sim-1.0,ADC,SIMULATED,CH,3,RATE,{state['rate']}")
        elif parts[0] == "START":
            state["streaming"] = True
            hub.publish("A,START")
        elif parts[0] == "STOP":
            state["streaming"] = False
            hub.publish("A,STOP")
        elif parts[0] == "RATE" and len(parts) > 1 and parts[1].isdigit() and 1 <= int(parts[1]) <= 200:
            state["rate"] = int(parts[1])
            hub.publish(f"A,RATE,{state['rate']}")
        else:
            hub.publish("E,UNKNOWN_CMD" if parts[0] != "RATE" else "E,BAD_RATE")

    hub.send_to_device = command
    print(f"simulating at {rate} Hz", flush=True)
    seq, t0, n = 0, time.monotonic(), 0
    period, ti = 4.0, 4.0 / 3
    while not stop.is_set():
        due = int((time.monotonic() - t0) * state["rate"])
        while n < due:
            t = n / state["rate"]
            x = t % period
            if x < ti:
                flow = 35 * math.sin(math.pi * x / ti)
                co2 = 38 * math.exp(-x / 0.05)
            else:
                e = x - ti
                flow = -60 * math.exp(-e / 0.53) * (1 - math.exp(-e / 0.06))
                co2 = (35 + 3 * e / (period - ti)) / (1 + math.exp(-(e - 0.28) / 0.04))
            pct = co2 / 760 * 100
            volts = [
                pct / 2.0 + random.uniform(-0.0005, 0.0005),
                (flow + 100) / 40 + random.uniform(-0.01, 0.01),
                (flow / 0.2 + 500) / 200 + random.uniform(-0.005, 0.005),
            ]
            if state["streaming"]:
                hub.publish(data_line(seq, int(t * 1000), volts))
            seq += 1
            n += 1
        time.sleep(0.01)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: localhost only)")
    ap.add_argument("--ws-port", type=int, default=8765)
    ap.add_argument("--simulate", action="store_true", help="generate synthetic data instead of reading a port")
    ap.add_argument("--rate", type=int, default=100, help="simulator sample rate, Hz")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args()

    if args.list:
        if list_ports is None:
            sys.exit("pyserial is not installed: pip install -r requirements.txt")
        for p in list_ports.comports():
            print(f"{p.device}\t{p.description}")
        return
    if not args.simulate and not args.port:
        ap.error("give --port (see --list) or --simulate")
    if not args.simulate and serial is None:
        sys.exit("pyserial is not installed: pip install -r requirements.txt")

    hub = Hub(asyncio.get_running_loop())
    stop = threading.Event()
    target = (lambda: run_simulator(hub, args.rate, stop)) if args.simulate else (lambda: run_serial(hub, args.port, args.baud, stop))
    threading.Thread(target=target, daemon=True).start()

    async with websockets.serve(hub.handler, args.host, args.ws_port, max_size=2**16):
        print(f"bridge listening on ws://{args.host}:{args.ws_port}  (Ctrl+C to stop)", flush=True)
        try:
            await asyncio.Future()
        finally:
            stop.set()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
