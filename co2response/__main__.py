"""``python -m co2response`` opens the monitor window.

    python -m co2response                     # open the monitor
    python -m co2response --sim               # ...and start the simulator
    python -m co2response --port COM3         # ...and connect to the Arduino on COM3
    python -m co2response --replay file.csv   # ...and replay a recorded session
    python -m co2response --list-ports        # show serial ports and exit
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="co2response", description=f"{APP_NAME}: research capnography and flow monitor. RESEARCH USE ONLY.")
    ap.add_argument("--sim", action="store_true", help="start the simulator (no hardware needed)")
    ap.add_argument("--port", help="connect to the Arduino on this serial port, e.g. COM3 or /dev/ttyACM0")
    ap.add_argument("--replay", metavar="CSV", help="replay a recorded session file")
    ap.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = ap.parse_args(argv)

    if args.list_ports:
        from .sources.serial_source import list_ports

        ports = list_ports()
        for p in ports:
            print(f"{p.device:24} {p.label}{'   <- likely Arduino' if p.likely_arduino else ''}")
        if not ports:
            print("No serial ports found.")
        return 0

    from .ui.app import run

    return run(sim=args.sim, port=args.port, replay=args.replay)


if __name__ == "__main__":
    sys.exit(main())
