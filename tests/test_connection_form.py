"""What the Connection screen offers (``co2response/connection_form.py``), which the
desktop window and the browser page both draw."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from co2response import connection_form as cf
from co2response.settings import SimSettings
from co2response.sim.controls import SITUATIONS


def snap(**kw):
    base = dict(status="idle", status_detail="", source_label="", source_kind=None, info=None)
    return SimpleNamespace(**{**base, **kw})


def test_both_front_ends_use_the_shared_choices():
    root = Path(cf.__file__).parent
    for front_end in ("ui/settings/connection.py", "web/settings_view.py"):
        text = (root / front_end).read_text(encoding="utf-8")
        assert "connection_form" in text
        for own_copy in ("115200", "it has no samples", '"5×"', cf.RATE_HINT, cf.FILE_CAL_HINT):
            assert own_copy not in text, f"{front_end} has its own copy of {own_copy!r}"


def test_status_in_a_few_words():
    assert cf.status_text(snap()) == "not connected"
    assert cf.status_text(snap(status="connecting")) == "connecting…"
    assert cf.status_text(snap(status="connected", source_label="Simulator")) == "connected to Simulator"
    assert cf.status_text(snap(status="error", status_detail="USB unplugged")) == "lost (USB unplugged)"


def test_device_info_and_who_takes_a_rate():
    assert cf.device_info(snap()) == [("INFO", "waiting for reply")]
    assert cf.device_info(snap(info={"FW": "1.2.0", "RATE": 100})) == [("FW", "1.2.0"), ("RATE", "100")]
    assert cf.takes_rate(snap(source_kind="serial")) and cf.takes_rate(snap(source_kind="simulator"))
    assert not cf.takes_rate(snap(source_kind="replay"))


def test_rate_is_kept_inside_what_the_firmware_takes():
    assert cf.rate_commands(50) == ["RATE 50", "INFO"]
    assert cf.rate_commands(1000) == ["RATE 200", "INFO"] and cf.rate_commands(0.2) == ["RATE 1", "INFO"]
    assert cf.rate_commands(None) == ["RATE 100", "INFO"]  # nothing typed


def test_simulator_choices_and_limits():
    assert [i for i, _ in cf.situation_options()] == [x.id for x in SITUATIONS]
    sim = SimSettings()
    assert cf.situation_hint(sim) == next(x.description for x in SITUATIONS if x.id == sim.preset)
    sim.adjusted = True
    assert cf.situation_hint(sim) == cf.SIM_ADJUSTED
    assert cf.sim_button(snap(source_kind="simulator")) == "Restart simulator" and cf.sim_button(snap()) == "Start simulator"
    for key, _, _, lo, hi, _ in cf.SIM_FIELDS:
        assert hasattr(sim, key)
        assert cf.clamp_sim(key, lo - 1) == lo and cf.clamp_sim(key, hi + 1) == hi
    assert cf.clamp_sim("something else", 7) == 7


def test_what_can_be_replayed():
    rows = [SimpleNamespace(id="a", status="done"), SimpleNamespace(id="b", status="recording"), SimpleNamespace(id="c", status="imported")]
    assert [r.id for r in cf.replayable(SimpleNamespace(list=lambda: rows))] == ["a", "c"]
    ok = SimpleNamespace(n=10)
    assert cf.for_replay(ok) is ok
    with pytest.raises(ValueError, match="no samples"):
        cf.for_replay(SimpleNamespace(n=0))
