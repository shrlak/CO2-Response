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
    assert 17990 <= len(s.samples) <= 18000  # the spike filter still holds the last 3 samples when recording stops
    assert len(s.breaths_all) == 48
    assert (s.breaths_all.breath_quality != "ok").sum() == 2  # two partial breaths in the sample
    assert len(s.breaths) == 46 and (s.breaths.breath_quality == "ok").all()  # only clean breaths
    assert len(load_session(sample_session, clean=False).breaths) == 48
    # v4: each breath's own length and expired volume, and from them its own minute ventilation
    b = s.breaths.iloc[5]
    assert b.mv_breath_lpm == pytest.approx(b.vt_ml / 1000 * 60 / b.period_s)
    assert b.vte_ml == pytest.approx(b.vt_ml, rel=0.03)
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
    from co2r.session import co2_response, load_session, test_window

    s = load_session(sample_session)
    # the sample's markers: Baseline at 20 s, "CO2 rebreathing started" at 60 s, End of test at 170 s
    assert test_window(s.events) == {"baseline": pytest.approx(20.03), "start": pytest.approx(60.03), "end": pytest.approx(170.03)}
    r = co2_response(s)
    assert r["rebreathing_start_s"] == pytest.approx(60.03) and r["end_s"] == pytest.approx(170.03)
    assert r["skip_s"] == 30  # from the recording's header
    # baseline: the clean breaths between the Baseline marker and rebreathing
    assert 8 <= r["baseline_n"] <= 11 and 36 < r["baseline_etco2_mmHg"] < 40 and 6.5 < r["baseline_mv_lpm"] < 8.5
    # not fitted: the first 30 s of rebreathing and the recovery after the end
    assert r["skipped_n"] >= 8 and r["n"] >= 15
    after_start = s.breaths[s.breaths.time_s >= 60.03]
    assert r["n"] + r["skipped_n"] == len(after_start)
    assert r["slope"] > 0 and 0.9 < r["r"] <= 1
    assert r["x_intercept"] == pytest.approx(-r["intercept"] / r["slope"])
    # the fit is on each breath's own MV, not the 60 s average, which lags the EtCO2 it is paired with
    fitted = after_start[(after_start.time_s >= 90.03) & (after_start.time_s < 170.03)]
    slope = float(__import__("numpy").polyfit(fitted.etco2_mmHg, fitted.mv_breath_lpm, 1)[0])
    assert r["slope"] == pytest.approx(slope)
    lagged = float(__import__("numpy").polyfit(fitted.etco2_mmHg, fitted.mv_lpm, 1)[0])
    assert abs(lagged - slope) > 0.01
    # no subject size in the sample's header
    assert math.isnan(r["slope_per_kg"])
    # options: no skip, breaths excluded by hand, an explicit start
    assert co2_response(s, skip_s=0)["n"] > r["n"]
    n0 = int(fitted.breath.iloc[0])
    assert co2_response(s, exclude=[n0])["n"] == r["n"] - 1
    whole = co2_response(s, start=-math.inf)
    assert whole["baseline_n"] == 0 and whole["n"] == len(s.breaths) and whole["skipped_n"] == 0


def test_co2_response_normalized_for_body_size(sample_session, tmp_path):
    import pytest

    pytest.importorskip("pandas")
    from co2r.session import co2_response, load_session

    text = open(sample_session, encoding="utf-8").read()
    sized = tmp_path / "sized.csv"
    sized.write_text(text.replace('"heightCm":null,"weightKg":null', '"heightCm":170,"weightKg":60'), encoding="utf-8")
    r = co2_response(load_session(str(sized)))
    assert r["slope_per_kg"] == pytest.approx(r["slope"] * 1000 / 60)
    assert r["slope_per_m2"] == pytest.approx(r["slope"] / (170 * 60 / 3600) ** 0.5)


def test_breaths_excluded_by_hand_are_left_out(sample_session, tmp_path):
    import pytest

    pytest.importorskip("pandas")
    from co2r.session import load_session

    lines = open(sample_session, encoding="utf-8").read().rstrip("\n").split("\n")
    # the operator took breaths 20 and 21 out, then put 21 back
    lines[-1] = lines[-1].replace("RECORDING_STOPPED", "BREATH_EXCLUDED 20 manual | BREATH_EXCLUDED 21 manual | BREATH_INCLUDED 21 | RECORDING_STOPPED")
    edited = tmp_path / "edited.csv"
    edited.write_text("\n".join(lines) + "\n", encoding="utf-8")
    s = load_session(str(edited))
    q = s.breaths_all.set_index("breath").breath_quality
    assert q[20] == "excluded" and q[21] == "ok"
    assert 20 not in set(s.breaths.breath) and 21 in set(s.breaths.breath)


def test_marker_kinds_and_the_latest_test():
    import pytest

    pd = pytest.importorskip("pandas")
    from co2r.session import marker_kind, test_window

    assert [marker_kind(x) for x in ("Baseline", "Rebreathing", "End of test", "Rebreathing stopped", "Event 2", "Weekend")] == ["baseline", "start", "end", "end", None, None]

    def ev(*marks):
        return pd.DataFrame({"time_s": [t for t, _ in marks], "type": ["MARK"] * len(marks), "event": [f"MARK {label}" for _, label in marks]})

    one = [(1.0, "Baseline"), (5.0, "Rebreathing"), (9.0, "End of test")]
    assert test_window(ev(*one)) == {"baseline": 1.0, "start": 5.0, "end": 9.0}
    assert test_window(ev(*one, (15.0, "Rebreathing"))) == {"baseline": 9.0, "start": 15.0, "end": None}
    assert test_window(ev(*one, (12.0, "Baseline"))) == {"baseline": 12.0, "start": None, "end": None}
    assert test_window(ev()) == {"baseline": None, "start": None, "end": None}


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
