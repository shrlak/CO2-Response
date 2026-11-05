import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))


@pytest.fixture
def fixtures():
    with open(os.path.join(ROOT, "docs", "protocol-fixtures.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_session():
    return os.path.join(ROOT, "sample-data", "synthetic_session.csv")
