#!/usr/bin/env python3
"""
Serial -> WebSocket bridge for the CO2-Response monitor (kept for existing instructions).

This now runs the co2r Python package (python/), which also supports NI-DAQmx,
LabJack and a simulated DAQ. New setups can use the package directly:

    pip install -e python
    co2r-daq serve --driver arduino --port COM3

Old usage still works:

    python bridge/serial_ws_bridge.py --list
    python bridge/serial_ws_bridge.py --port COM3
    python bridge/serial_ws_bridge.py --simulate

RESEARCH USE ONLY. Not a medical device.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from co2r.cli import main as co2r_main
except ImportError:  # running from a checkout without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
    from co2r.cli import main as co2r_main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: localhost only)")
    ap.add_argument("--ws-port", type=int, default=8765)
    ap.add_argument("--simulate", action="store_true", help="generate synthetic data instead of reading a port")
    ap.add_argument("--rate", type=int, default=100, help="simulator sample rate, Hz")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    a = ap.parse_args()
    if a.list:
        sys.exit(co2r_main(["list"]))
    if not a.simulate and not a.port:
        ap.error("give --port (see --list) or --simulate")
    args = ["serve", "--host", a.host, "--ws-port", str(a.ws_port)]
    args += ["--driver", "simulated", "--rate", str(a.rate)] if a.simulate else ["--driver", "arduino", "--port", a.port, "--baud", str(a.baud)]
    sys.exit(co2r_main(args))


if __name__ == "__main__":
    main()
