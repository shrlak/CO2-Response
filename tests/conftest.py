import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The suites written for the ADC board (volts on the line, a separate pneumotach) run on it;
# tests/test_digital_board.py covers the Uno R4 board, which a new installation expects.
os.environ.setdefault("CO2R_HARDWARE", "analog")

from co2response.channels import DEFAULT_CHANNELS  # noqa: E402
from co2response.sim.core import Encoders  # noqa: E402


def _lin(cid: str):
    cal = DEFAULT_CHANNELS["channels"][cid]["calibration"]
    assert cal["type"] == "linear", "default calibration must be linear"
    return lambda y: (y - cal["offset"]) / cal["gain"]


#: encoders that invert the default (linear) channel calibration
DEFAULT_ENCODERS = Encoders(co2=_lin("co2"), flow=_lin("flow"), pneumo=_lin("pneumo"))


def same(a, b) -> bool:
    """Equality where NaN equals NaN (like toEqual in the JavaScript tests)."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if hasattr(a, "__dataclass_fields__") and type(a) is type(b):
        return all(same(getattr(a, f), getattr(b, f)) for f in a.__dataclass_fields__)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CO2R_HOME", str(tmp_path))
    return tmp_path
