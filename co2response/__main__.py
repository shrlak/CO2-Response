"""``python -m co2response`` opens the monitor window.

    python -m co2response                     # open the monitor
    python -m co2response --sim               # ...and start the simulator
    python -m co2response --port COM3         # ...and connect to the Arduino on COM3
    python -m co2response --replay file.csv   # ...and replay a recorded session
    python -m co2response --list-ports        # show serial ports and exit
    python -m co2response --report file.csv   # write the test report (file.pdf) and exit
    python -m co2response --tui --sim         # the monitor full-screen in a terminal (also: co2response-tui)
    python -m co2response --fast-test normal  # a whole simulated test in about a second: CSV, PDF and the graph
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
    ap.add_argument("--tui", action="store_true", help="run the monitor full-screen in this terminal (works in the VS Code terminal and over SSH)")
    ap.add_argument("--situation", metavar="ID", help="start the simulator in a situation, e.g. test_normal (a whole rebreathing test); --list-situations shows them")
    ap.add_argument("--list-situations", action="store_true", help="list the simulation situations and exit")
    ap.add_argument("--speed", type=float, default=None, metavar="X", help="simulation speed, 1 to 50 times real time")
    ap.add_argument("--board", choices=("digital", "analog"), help="the board to expect and to simulate: digital = Uno R4 with the SprintIR CO2 and SFM3304-D flow sensors (default); analog = ADS1115 with three analog sensors")
    ap.add_argument("--fast-test", choices=("normal", "blunted", "brisk"), metavar="KIND", help="run a whole simulated rebreathing test (normal, blunted or brisk) with no window as fast as the computer can, save it with its PDF report, print the MV-vs-EtCO2 graph and exit")
    ap.add_argument("--variability", type=float, default=None, metavar="PCT", help="with --fast-test: breath-to-breath variability of VT, %% (default 15)")
    ap.add_argument("--sessions", metavar="DIR", help="with --fast-test: the folder to save the session in (default: the sessions folder)")
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

    if args.list_situations:
        from .sim.controls import SITUATIONS

        for sit in SITUATIONS:
            print(f"{sit.id:20} {sit.group:28} {sit.label}")
        return 0

    if args.report:
        return report(args.report, args.out, args.skip)

    if args.fast_test:
        from .headless import main as fast

        return fast(args.fast_test, args.sessions, args.board or "digital", args.variability)

    if args.board:
        import os

        os.environ["CO2R_HARDWARE"] = args.board  # the default of a new installation...
        from .settings import SettingsStore

        store = SettingsStore()  # ...and the saved choice, so the window opens on that board
        s = store.load_settings()
        s.hardware = args.board
        store.save_settings(s)

    if args.situation:
        from .sim.controls import SITUATION_BY_ID

        if args.situation not in SITUATION_BY_ID:
            print(f"Unknown situation {args.situation!r}; --list-situations shows them.", file=sys.stderr)
            return 2

    tui = args.tui
    if not tui:
        try:
            import pyqtgraph  # noqa: F401
            import PySide6.QtWidgets  # noqa: F401
        except ImportError:
            print("The desktop window needs PySide6 and pyqtgraph (pip install \"co2response[desktop]\"); opening the terminal monitor instead.", file=sys.stderr)
            tui = True
    if tui:
        from .tui.app import run as run_tui

        return run_tui(sim=args.sim, port=args.port, replay=args.replay, situation=args.situation, speed=args.speed, exit_after=args.exit_after)

    from .ui.app import run

    return run(sim=args.sim or bool(args.situation), port=args.port, replay=args.replay, exit_after=args.exit_after, situation=args.situation, speed=args.speed)


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
