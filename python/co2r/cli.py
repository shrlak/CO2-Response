"""
co2r-daq: run the CO2-Response acquisition from Python.

    co2r-daq drivers                                   which DAQ drivers are installed
    co2r-daq list                                      serial ports and DAQ devices found
    co2r-daq serve --driver arduino --port COM3        Arduino -> dashboard
    co2r-daq serve --driver nidaq --device Dev1        NI DAQ  -> dashboard
    co2r-daq serve --driver labjack                    LabJack -> dashboard
    co2r-daq serve --driver simulated                  no hardware
    co2r-daq serve --config lab.json                   settings from a file
    co2r-daq check --driver nidaq --seconds 10         read without the dashboard and report

The dashboard connects from Settings > Connection > Python (ws://localhost:8765).
Research use only; not a medical device.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time

from . import __version__
from .drivers import DRIVERS, DaqDriver, DaqError, DaqOverrun
from .sources import SampledSource, SerialRelaySource, Source


def build_driver(name: str, a: argparse.Namespace) -> DaqDriver:
    channels = [c.strip() for c in a.channels.split(",")] if a.channels else None
    if name == "simulated":
        from .drivers.simulated import SimulatedDriver

        return SimulatedDriver(ramp_mmhg_per_min=a.ramp, seed=a.seed)
    if name == "nidaq":
        from .drivers.nidaq import NiDaqmxDriver

        lo, hi = a.range
        return NiDaqmxDriver(device=a.device or "Dev1", channels=channels or ["ai0", "ai1", "ai2"], min_v=lo, max_v=hi, terminal=a.terminal, oversample=a.oversample)
    if name == "labjack":
        from .drivers.labjack import LabJackDriver

        return LabJackDriver(identifier=a.device or "ANY", channels=channels or ["AIN0", "AIN1", "AIN2"], range_v=max(abs(a.range[0]), abs(a.range[1])), oversample=a.oversample)
    if name in DRIVERS:
        return DRIVERS[name]()
    raise SystemExit(f"unknown driver {name!r}; choose from: arduino, {', '.join(DRIVERS)}")


def build_source(a: argparse.Namespace) -> Source:
    if a.driver == "arduino":
        if not a.port:
            raise SystemExit("--driver arduino needs --port (see `co2r-daq list`)")
        return SerialRelaySource(a.port, a.baud)
    return SampledSource(build_driver(a.driver, a), rate_hz=a.rate)


def _apply_config(a: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Values in --config fill in anything not given on the command line."""
    if not getattr(a, "config", None):
        return
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    defaults = {act.dest: act.default for act in parser._actions}
    for k, v in cfg.items():
        k = k.replace("-", "_")
        if k not in defaults:
            raise SystemExit(f"{a.config}: unknown setting {k!r}")
        if getattr(a, k) == defaults[k]:
            if k == "range" and isinstance(v, list):
                v = tuple(float(x) for x in v)
            if k == "channels" and isinstance(v, list):
                v = ",".join(v)
            setattr(a, k, v)


def cmd_drivers(_a: argparse.Namespace) -> int:
    print("arduino     built in (needs pyserial)  relays the CO2-Response firmware over USB serial")
    for name, cls in DRIVERS.items():
        ok, msg = cls.available()
        print(f"{name:<11} {'ready' if ok else 'not installed':<14} {msg}")
    return 0


def cmd_list(_a: argparse.Namespace) -> int:
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception:
        ports = []
    print("Serial ports (Arduino):")
    for p in ports:
        print(f"  {p.device}\t{p.description}")
    if not ports:
        print("  none found")
    from .drivers import labjack, nidaq

    for title, devs in [("NI-DAQmx devices:", nidaq.list_devices()), ("LabJack devices:", labjack.list_devices())]:
        print(title)
        for d in devs:
            print(f"  {d}")
        if not devs:
            print("  none found (or driver not installed)")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    source = build_source(a)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: listening on {a.host}: anyone who can reach port {a.ws_port} can see the data and send commands.", file=sys.stderr)
    try:
        asyncio.run(_serve(source, a))
    except KeyboardInterrupt:
        pass
    return 0


async def _serve(source: Source, a: argparse.Namespace) -> None:
    from .server import serve

    await serve(source, a.host, a.ws_port, log=lambda m: print(m, flush=True))


def cmd_check(a: argparse.Namespace) -> int:
    """Read for a few seconds without the dashboard and report rate, drops and levels."""
    if a.driver == "arduino":
        print("For the Arduino, use the dashboard's hardware check (Settings > Diagnostics).")
        return 2
    drv = build_driver(a.driver, a)
    try:
        info = drv.open(a.rate)
    except DaqError as e:
        print(f"FAIL  could not open {a.driver}: {e}")
        return 1
    print(f"opened {info.name}: {info.channels} channels at {info.rate_hz:g} Hz" + (f", range {info.full_scale_v:g} V" if info.full_scale_v else ""))
    n = 0
    lost = 0
    stats = [[math.inf, -math.inf, 0.0] for _ in range(info.channels)]
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < a.seconds:
            try:
                for s in drv.read(timeout=1.0):
                    n += 1
                    for i, v in enumerate(s[: info.channels]):
                        st = stats[i]
                        st[0] = min(st[0], v)
                        st[1] = max(st[1], v)
                        st[2] += v
            except DaqOverrun as e:
                lost += max(1, e.lost)
    finally:
        drv.close()
    secs = time.monotonic() - t0
    rate = n / secs if secs else 0
    ok_rate = abs(rate - info.rate_hz) / info.rate_hz < 0.05
    print(f"{'PASS' if ok_rate else 'CHECK'}  {n} samples in {secs:.1f} s = {rate:.1f} Hz (expected {info.rate_hz:g})")
    print(f"{'PASS' if lost == 0 else 'CHECK'}  {lost} samples lost")
    names = ["CO2 (ch 0)", "flow (ch 1)", "pneumotach (ch 2)"]
    for i, (lo, hi, total) in enumerate(stats):
        if n:
            print(f"      {names[i] if i < 3 else f'ch {i}'}: mean {total / n:.4f} V, min {lo:.4f} V, max {hi:.4f} V")
    return 0 if ok_rate and lost == 0 and n else 1


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="co2r-daq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"co2r {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("drivers", help="show which DAQ drivers are installed").set_defaults(fn=cmd_drivers)
    sub.add_parser("list", help="list serial ports and DAQ devices").set_defaults(fn=cmd_list)

    def daq_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", help="JSON file with any of these settings (command-line values win)")
        sp.add_argument("--driver", default="simulated", help=f"arduino, {', '.join(DRIVERS)} (default simulated)")
        sp.add_argument("--rate", type=float, default=100.0, help="samples/s per channel sent to the dashboard (default 100)")
        g = sp.add_argument_group("Arduino")
        g.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0")
        g.add_argument("--baud", type=int, default=115200)
        g = sp.add_argument_group("DAQ boards (nidaq, labjack)")
        g.add_argument("--device", help="NI device name (Dev1) or LabJack identifier (serial, IP or ANY)")
        g.add_argument("--channels", help="three inputs in order CO2,flow,pneumotach (default ai0,ai1,ai2 or AIN0,AIN1,AIN2)")
        g.add_argument("--range", type=lambda s: tuple(float(x) for x in s.split(",")), default=(0.0, 5.0), help="input range in volts, min,max (default 0,5)")
        g.add_argument("--terminal", default="RSE", help="NI input mode: RSE, NRSE, DIFF, PSEUDO_DIFF (default RSE)")
        g.add_argument("--oversample", type=int, default=10, help="average this many hardware samples per output sample (default 10)")
        g = sp.add_argument_group("simulator")
        g.add_argument("--ramp", type=float, default=0.0, help="CO2 rise in mmHg/min (default 0)")
        g.add_argument("--seed", type=int, default=None)

    sp = sub.add_parser("serve", help="acquire and serve the dashboard over WebSocket")
    daq_args(sp)
    sp.add_argument("--host", default="127.0.0.1", help="interface to listen on (default localhost only)")
    sp.add_argument("--ws-port", type=int, default=8765)
    sp.set_defaults(fn=cmd_serve, parser=sp)

    sp = sub.add_parser("check", help="read for a few seconds and report rate, losses and levels")
    daq_args(sp)
    sp.add_argument("--seconds", type=float, default=5.0)
    sp.set_defaults(fn=cmd_check, parser=sp)
    return p


def main(argv: list[str] | None = None) -> int:
    p = make_parser()
    a = p.parse_args(argv)
    if hasattr(a, "parser"):
        _apply_config(a, a.parser)
    rc = a.fn(a)
    if argv is None:
        sys.exit(rc)
    return rc
