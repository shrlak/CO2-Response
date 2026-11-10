"""Entry point for the standalone build (PyInstaller): the same as ``python -m co2response``."""

import sys

from co2response.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
