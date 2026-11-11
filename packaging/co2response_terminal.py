"""Entry point for the standalone terminal build (PyInstaller): ``co2response --tui``.

Any option of ``co2response`` works (``--sim``, ``--port COM3``, ``--fast-test normal``…);
with none, it opens the terminal monitor on its connect menu."""

import sys

from co2response.__main__ import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if not any(a in args for a in ("--report", "--fast-test", "--list-ports", "--list-situations", "--version", "-h", "--help")):
        args = ["--tui", *args]
    sys.exit(main(args))
