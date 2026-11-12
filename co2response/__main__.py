"""``python -m co2response`` opens the monitor window.

    python -m co2response                     # open the monitor
    python -m co2response --sim               # ...and start the simulator
    python -m co2response --port COM3         # ...and connect to the Arduino on COM3
    python -m co2response --replay file.csv   # ...and replay a recorded session
    python -m co2response --list-ports        # show serial ports and exit
    python -m co2response --report file.csv   # write the test report (file.pdf) and exit
    python -m co2response --tui --sim         # the monitor full-screen in a terminal (also: co2response-tui)
    python -m co2response --fast-test normal  # a whole simulated test in about a second: CSV, PDF and the graph
    python -m co2response --capnostream COM4  # take the CO2 from a Capnostream 35 on COM4 (saved; "off" to stop)
    python -m co2response --capture COM4      # record what a Capnostream sends, to set up its format
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
    ap.add_argument("--graphics", choices=("auto", "on", "off"), default="auto", help="with --tui: draw the waveforms, numbers and graphs as pictures like the browser page (auto: where the terminal can show images: iTerm2, VS Code with images on, Kitty, WezTerm, Ghostty, Windows Terminal), or text (off)")
    ap.add_argument("--situation", metavar="ID", help="start the simulator in a situation, e.g. test_normal (a whole rebreathing test); --list-situations shows them")
    ap.add_argument("--list-situations", action="store_true", help="list the simulation situations and exit")
    ap.add_argument("--speed", type=float, default=None, metavar="X", help="simulation speed, 1 to 50 times real time")
    ap.add_argument("--board", choices=("digital", "analog"), help="the board to expect and to simulate: digital = Uno R4 with the SprintIR CO2 and SFM3304-D flow sensors (default); analog = ADS1115 with three analog sensors")
    ap.add_argument("--fast-test", choices=("normal", "blunted", "brisk"), metavar="KIND", help="run a whole simulated rebreathing test (normal, blunted or brisk) with no window as fast as the computer can, save it with its PDF report, print the MV-vs-EtCO2 graph and exit")
    ap.add_argument("--variability", type=float, default=None, metavar="PCT", help="with --fast-test: breath-to-breath variability of VT, %% (default 15)")
    ap.add_argument("--sessions", metavar="DIR", help="with --fast-test: the folder to save the session in (default: the sessions folder)")
    cap = ap.add_argument_group("Capnostream 35 as the CO2 source (saved in the settings)")
    cap.add_argument("--capnostream", metavar="PORT", help="its data port: a serial port (COM4, /dev/ttyUSB0), socket://HOST:PORT for its LAN/Wi-Fi link, sim (a simulated one, with --sim) or off")
    cap.add_argument("--capno-baud", type=int, metavar="N", help="its serial speed (default 9600)")
    cap.add_argument("--capno-fields", metavar="LIST", help='its ASCII layout, e.g. "time,co2,etco2,rr,spo2,pr"; "auto" finds KEY=value pairs (default)')
    cap.add_argument("--capno-units", choices=("mmHg", "%", "kPa"), help="the unit of its CO2 values (default mmHg)")
    cap.add_argument("--capno-delay", type=float, metavar="MS", help="its sample-line delay, ms (default 2700)")
    cap.add_argument("--capture", metavar="PORT", help="record what a Capnostream sends on PORT (60 s), report what was found, and exit")
    cap.add_argument("--capture-seconds", type=float, default=60, metavar="SEC", help="with --capture: how long to record")
    cap.add_argument("--capture-baud", default="auto", metavar="N", help="with --capture: the serial speed, or auto to try the common ones")
    ap.add_argument("--exit-after", type=float, default=None, metavar="SEC", help="close the window by itself after this many seconds (for checking an installation)")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    ap.add_argument("--update", action="store_true", help="update to the latest version from GitHub (or git pull, when run from a copy of the repository) and exit")
    args = ap.parse_args(argv)

    if args.list_ports:
        from .sources.serial_source import list_ports

        ports = list_ports()
        for p in ports:
            print(f"{p.device:24} {p.label}{'   <- likely Arduino' if p.likely_arduino else ''}")
        if not ports:
            print("No serial ports found.")
        return 0

    if args.update:
        return update()

    if args.capture:
        return capture_capnostream(args.capture, args.capture_seconds, args.capture_baud, args.capno_fields, args.capno_units)

    if any(v is not None for v in (args.capnostream, args.capno_baud, args.capno_fields, args.capno_units, args.capno_delay)):
        save_capno(args)

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

        return run_tui(sim=args.sim, port=args.port, replay=args.replay, situation=args.situation, speed=args.speed, exit_after=args.exit_after, graphics=args.graphics)

    from .ui.app import run

    return run(sim=args.sim or bool(args.situation), port=args.port, replay=args.replay, exit_after=args.exit_after, situation=args.situation, speed=args.speed)


REPO_URL = "https://github.com/shrlak/CO2-Response"


def update(run=None) -> int:
    """``--update``: bring this installation up to the latest version.

    Run from a copy of the repository, that is ``git pull``; installed with pip (or the
    install script), it reinstalls the package from GitHub, with the desktop window if
    it is installed now."""
    import subprocess
    from pathlib import Path

    run = run or subprocess.call
    print(f"{APP_NAME} {__version__} now.")
    root = Path(__file__).resolve().parents[1]
    if (root / ".git").exists():
        print(f"Updating the copy of the repository in {root} (git pull) …")
        code = run(["git", "-C", str(root), "pull", "--ff-only"])
        if code == 0:
            print("Updated. If the requirements changed, run:  pip install -r requirements.txt")
        return code
    try:
        import PySide6  # noqa: F401

        extra = "[desktop]"
    except ImportError:
        extra = ""
    spec = f"co2response{extra} @ git+{REPO_URL}"
    print(f"Reinstalling from {REPO_URL} …")
    # the package itself always (same version number or not), then whatever it now needs
    code = run([sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", spec])
    if code == 0:
        code = run([sys.executable, "-m", "pip", "install", "--upgrade", spec])
    print("Updated: run co2response --version to see the version." if code == 0 else "The update did not finish; see the messages above.")
    return code


def save_capno(args) -> None:
    """Save the Capnostream options given on the command line in the settings."""
    import dataclasses

    from .settings import SettingsStore, sanitize_capno

    store = SettingsStore()
    s = store.load_settings()
    c = s.capno
    if args.capnostream is not None:
        off = args.capnostream.lower() in ("off", "none", "")
        c = dataclasses.replace(c, enabled=not off, port=c.port if off else args.capnostream)
    if args.capno_baud is not None:
        c = dataclasses.replace(c, baud=args.capno_baud)
    if args.capno_fields is not None:
        c = dataclasses.replace(c, fields="" if args.capno_fields.lower() == "auto" else args.capno_fields)
    if args.capno_units is not None:
        c = dataclasses.replace(c, units=args.capno_units)
    if args.capno_delay is not None:
        c = dataclasses.replace(c, delay_ms=args.capno_delay)
    s.capno = sanitize_capno(c)
    store.save_settings(s)
    state = f"on: {s.capno.port}, {s.capno.baud} baud, fields {s.capno.fields or 'auto'}, {s.capno.units}, delay {s.capno.delay_ms:g} ms" if s.capno.enabled else "off"
    print(f"Capnostream as the CO2 source: {state}")


def capture_capnostream(port: str, seconds: float, baud: str, fields: str | None, units: str | None) -> int:
    """``--capture``: record a Capnostream's output and say what the decoder makes of it."""
    import time
    from pathlib import Path

    from .sources.capnostream import CapnoDecoder, capture

    out = Path(f"capnostream-capture-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    dec = CapnoDecoder("" if not fields or fields.lower() == "auto" else fields, units or "mmHg")
    print(f"Capturing from {port}. On the Capnostream, turn on its data output (RS-232 / LAN) in the ASCII protocol.")
    try:
        with open(out, "w", encoding="utf-8") as f:
            r = capture(port, seconds, baud if baud == "auto" else int(baud), f, dec)
    except Exception as e:  # noqa: BLE001  (the port could not be opened)
        print(f"Could not read {port}: {e}", file=sys.stderr)
        return 1
    print(f"{r['lines']} lines, {r['bytes']} bytes at {r['baud']} baud, saved to {out.resolve()}")
    print(f"CO2 waveform samples found: {r['co2_samples']} ({r['co2_rate_hz']:.1f} a second; the monitor measures 20)")
    print(f"Numbers found: {', '.join(r['numbers']) or 'none'}; lines not understood: {r['unknown_lines']}")
    if r["co2_rate_hz"] < 15:
        print("The CO2 waveform was not found at 20 a second: look at the file and give the layout with --capno-fields (docs/capnostream.md).")
    return 0


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
