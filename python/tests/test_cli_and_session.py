import json

from co2r.cli import _apply_config, main, make_parser


def test_drivers_lists_every_driver(capsys):
    assert main(["drivers"]) == 0
    out = capsys.readouterr().out
    for name in ("arduino", "simulated", "nidaq", "labjack"):
        assert name in out


def test_check_passes_on_the_simulator(capsys):
    assert main(["check", "--driver", "simulated", "--seconds", "1.5"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "0 samples lost" in out


def test_config_file_fills_unset_options(tmp_path):
    cfg = tmp_path / "lab.json"
    cfg.write_text(json.dumps({"driver": "nidaq", "device": "Dev3", "channels": ["ai4", "ai5", "ai6"], "range": [-10, 10], "oversample": 4}))
    p = make_parser()
    a = p.parse_args(["serve", "--config", str(cfg), "--oversample", "8"])
    _apply_config(a, a.parser)
    assert (a.driver, a.device, a.channels, a.range, a.oversample) == ("nidaq", "Dev3", "ai4,ai5,ai6", (-10.0, 10.0), 8)


def test_example_configs_are_valid(tmp_path):
    import glob
    import os

    here = os.path.dirname(__file__)
    for path in glob.glob(os.path.join(here, "..", "examples", "*.json")):
        p = make_parser()
        a = p.parse_args(["serve", "--config", path])
        _apply_config(a, a.parser)
        assert a.driver in ("arduino", "nidaq", "labjack", "simulated")


def test_loads_the_sample_session(sample_session):
    import pytest

    pd = pytest.importorskip("pandas")
    from co2r.session import load_session

    s = load_session(sample_session)
    assert 14990 <= len(s.samples) <= 15000  # the spike filter still holds the last 3 samples when recording stops
    assert len(s.breaths_all) == 39
    assert (s.breaths_all.breath_quality != "ok").sum() == 2  # two partial breaths in the sample
    assert len(s.breaths) == 37 and (s.breaths.breath_quality == "ok").all()  # only clean breaths
    assert len(load_session(sample_session, clean=False).breaths) == 39
    assert set(s.events.type) >= {"MARK", "ALARM_START"}
    assert (s.samples.time_s.diff().dropna().round(6) == 0.01).all()
    assert isinstance(s.samples, pd.DataFrame)
