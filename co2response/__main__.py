"""``python -m co2response`` opens the monitor window.

    python -m co2response                     # open the monitor
    python -m co2response --sim               # ...and start the simulator
    python -m co2response --port COM3         # ...and connect to the Arduino on COM3
    python -m co2response --replay file.csv   # ...and replay a recorded session
    python -m co2response --list-ports        # show serial ports and exit
    python -m co2response --report file.csv   # write the test report (file.pdf) and exit
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
    ap.add_argument("--report", metavar="CSV", help="write the CO2 response report of a recorded session as a PDF and exit")
    ap.add_argument("--out", metavar="PDF", help="where --report writes (default: next to the session file)")
    ap.add_argument("--skip", type=float, default=None, metavar="SEC", help="with --report: seconds of rebreathing left out of the fit (default: what the file's header says)")
    ap.add_argument("--exit-after", type=float, default=None, metavar="SEC", help="close the window by itself after this many seconds (for checking an installation)")
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

    if args.report:
        return report(args.report, args.out, args.skip)

    from .ui.app import run

    return run(sim=args.sim, port=args.port, replay=args.replay, exit_after=args.exit_after)


def report(csv_path: str, out: str | None = None, skip_sec: float | None = None) -> int:
    """Write the test report of a session file. No window is opened."""
    import time
    from pathlib import Path

    from .storage.report import build_report, report_pdf, report_text
    from .storage.session_file import read_session

    src = Path(csv_path)
    try:
        r = build_report(read_session(src), src.name, skip_sec, __version__, time.time() * 1000)
    except (OSError, ValueError) as e:
        print(f"Could not read {src}: {e}", file=sys.stderr)
        return 1
    dest = Path(out) if out else src.with_suffix(".pdf")
    dest.write_bytes(report_pdf(r))
    print(report_text(r), end="")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
