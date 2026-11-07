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
    # samples inside the two partial breaths keep their raw volts but lose their calibrated values
    bad = s.samples.breath_quality == "artifact"
    assert 100 < bad.sum() < 1000
    assert s.samples.loc[bad, "co2_mmHg"].isna().all() and s.samples.loc[bad, "v0"].notna().all()
    assert s.samples.loc[s.samples.breath_quality == "ok", "co2_mmHg"].notna().all()
    raw = load_session(sample_session, clean=False)
    assert raw.samples.loc[bad, "co2_mmHg"].notna().all()
    assert set(s.events.type) >= {"MARK", "ALARM_START"}
    assert (s.samples.time_s.diff().dropna().round(6) == 0.01).all()
    assert isinstance(s.samples, pd.DataFrame)


def test_co2_response_splits_baseline_from_rebreathing(sample_session):
    import math

    import pytest

    pytest.importorskip("pandas")
    from co2r.session import co2_response, load_session

    s = load_session(sample_session)
    r = co2_response(s)
    # the sample's marker "CO2 rebreathing started" is at 60.03 s
    assert r["rebreathing_start_s"] == pytest.approx(60.03)
    assert r["baseline_n"] + r["n"] == len(s.breaths)
    assert r["baseline_n"] >= 10 and r["n"] >= 20
    assert 36 < r["baseline_etco2_mmHg"] < 40
    assert r["slope"] > 0 and 0.9 < r["r"] <= 1
    assert r["x_intercept"] == pytest.approx(-r["intercept"] / r["slope"])
    # with no split every breath is fitted
    whole = co2_response(s, start=-math.inf)
    assert whole["baseline_n"] == 0 and whole["n"] == len(s.breaths)


def test_rebreathing_marker_found_after_a_rename():
    import pytest

    pd = pytest.importorskip("pandas")
    from co2r.session import rebreathing_start

    ev = pd.DataFrame(
        {
            "time_s": [5.0, 30.0, 31.5, 90.0],
            "type": ["MARK", "MARK", "MARK_RENAMED", "MARK"],
            "event": ["MARK Event 1", "MARK Event 2", "MARK_RENAMED 2 Rebreathing", "MARK Event 3"],
        }
    )
    assert rebreathing_start(ev) == 30.0  # when it was placed, not when it was labelled
    assert rebreathing_start(ev.iloc[[0, 1, 3]]) is None


def test_sample_quality_follows_breath_boundaries():
    import pytest

    pytest.importorskip("pandas")
    from co2r.session import sample_quality

    q = sample_quality([0.5, 1.5, 2.0, 2.5, 4.0, 5.5], [2.0, 4.0], ["ok", "noisy"], first_period=1.0)
    # before the first breath, breath 1 (1.0, 2.0], breath 2 (2.0, 4.0], after the last
    assert list(q) == ["", "ok", "ok", "noisy", "noisy", ""]
