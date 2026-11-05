"""
Load a CO2-Response session file for analysis (thin wrapper around co2r.session).

    import sys; sys.path.append("analysis")
    from load_session import load_session
    s = load_session("co2r_STUDY-01_20261105-010203.csv")

Command line:  python analysis/load_session.py file.csv --breaths breaths.csv
Needs pandas. The full package installs with:  pip install -e "python[analysis]"
"""
from __future__ import annotations

import os
import sys

try:
    from co2r.session import Session, load_session, main
except ImportError:  # running from a checkout without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
    from co2r.session import Session, load_session, main

__all__ = ["Session", "load_session"]

if __name__ == "__main__":
    main()
