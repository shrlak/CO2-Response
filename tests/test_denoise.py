"""Noise removal before interpretation: spike removal, breath screening, and what the
engine does with rejected breaths."""

import dataclasses
import math
import random

import pytest
from conftest import DEFAULT_ENCODERS
from test_engine import make_engine, run

from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.processing.breath import Breath
from co2response.processing.denoise import BreathScreen, Despiker, NoiseMeter, hampel, screen_series
from co2response.processing.metrics import etco2_median
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline
from co2response.protocol import parse_line
from co2response.sim.core import Encoders, SimulatorCore
from co2response.sources.simulator import SimulatorSource

NAN = float("nan")


def despike(xs, h=3):
    d = Despiker(h)
    out = [r for x in xs if (r := d.push([x], None)) is not None] + d.flush()
    return [c[0] for _, _, c in out]


# ------------------------------------------------------------------ spike removal


def test_hampel_replaces_a_lone_spike_with_the_local_median():
    assert hampel([1.0, 1.01, 0.99, 4.0, 1.0, 1.02, 0.98]) == pytest.approx(1.0, abs=0.02)
    assert hampel([1.0, 1.01, 0.99, 1.0, 1.0, 1.02, 0.98]) == 1.0  # nothing to do
    assert math.isnan(hampel([1, 1, 1, NAN, 1, 1, 1]))


def test_despiker_removes_spikes_but_keeps_every_sample_and_real_edges():
    base = [2.0 + 0.001 * math.sin(i / 3) for i in range(100)]
    step = [x + (1.5 if i >= 50 else 0) for i, x in enumerate(base)]  # a real fast change
    noisy = list(step)
    noisy[20] += 2.0  # single-sample spike
    noisy[70] -= 1.8
    noisy[71] -= 1.7  # two-sample spike
    out = despike(noisy)
    assert len(out) == len(noisy)  # nothing lost at either end
    assert max(abs(a - b) for a, b in zip(out, step)) < 0.01  # spikes gone, step intact
    ramp = [i * 0.05 for i in range(60)]  # a steep monotonic edge (capnogram upstroke)
    assert despike(ramp) == ramp


def test_despiker_output_is_delayed_by_half_a_window():
    d = Despiker(3)
    got = [d.push([float(i)], i) for i in range(10)]
    assert got[:3] == [None, None, None]
    assert [g[0] for g in got[3:]] == [0, 1, 2, 3, 4, 5, 6]
    assert [t for t, _, _ in d.flush()] == [7, 8, 9]


def test_noise_meter_ignores_slow_breathing_and_measures_white_noise():
    rng = random.Random(1)
    m = NoiseMeter()
    for i in range(2000):
        m.push(20 * math.sin(i / 60) + rng.gauss(0, 0.5))
    assert 0.45 < m.take() < 0.55
    assert math.isnan(m.take())  # nothing since the last call


# ------------------------------------------------------------------ breath screening


def breath(et=38.0, fi=0.5, vt=500.0, peak=35.0, plateau=0.8, noise_co2=0.2, noise_flow=0.3, by="flow", level=None) -> Breath:
    return Breath(0, 4, 4, et, fi, vt, peak, by, plateau, et - 1 if level is None else level, noise_co2, noise_flow)


def test_screen_rejects_noisy_partial_and_impossible_breaths():
    s = BreathScreen()
    assert s.reasons(breath())[0] == "ok"
    assert s.reasons(breath(noise_co2=6))[0] == "noisy"  # SNR 37/6 < 8
    assert s.reasons(breath(noise_co2=6, plateau=0.02))[0] == "noisy"  # noise is named before shape
    assert s.reasons(breath(noise_flow=8))[0] == "noisy"
    assert s.reasons(breath(plateau=0.05))[0] == "artifact"  # partial breath
    assert s.reasons(breath(et=150))[0] == "artifact"
    assert s.reasons(breath(vt=0.2))[0] == "artifact"
    # a flat CO2 line (sample line off) is not "noisy": the low-CO2 alarm must still see it
    assert s.reasons(breath(et=0.8, fi=0.2, noise_co2=0.3, plateau=NAN))[0] == "ok"


def test_screen_flags_an_isolated_jump_one_breath_later_but_keeps_a_real_step():
    s = BreathScreen()
    series = [38, 38.2, 37.9, 38.1, 24.0, 38.0, 38.2, 46, 46.3, 46.1]
    flagged = []
    for et in series:
        b = breath(et=et)
        prev = s.screen(b)
        if prev is not None:
            flagged.append(prev.etco2)
    assert flagged == [24.0]  # the dip; the step to 46 stays


def test_screen_series_cleans_files_from_earlier_versions():
    q = screen_series([38, 38.1, 37.9, 24.0, 38.0, 200, 38.1])
    assert q == ["ok", "ok", "ok", "outlier", "ok", "artifact", "ok"]
    assert screen_series([38, 39], ["noisy", ""]) == ["noisy", "ok"]


def test_screening_can_be_turned_off():
    s = BreathScreen(enabled=False)
    b = breath(noise_co2=50)
    s.screen(b)
    assert b.quality == "ok"


# ------------------------------------------------------------------ through the pipeline


def run_pipeline(scenario: str, seconds: int, **opts):
    core = SimulatorCore(DEFAULT_ENCODERS, scenario=scenario, seed=9)
    pipe = Pipeline(clone_channels(DEFAULT_CHANNELS), dataclasses.replace(DEFAULT_PROCESSING, **opts))
    breaths = []
    for _ in range(seconds * 100):
        for line in core.next_sample():
            d = parse_line(line)
            if d.kind == "data" and (p := pipe.process(d.t_ms, d.volts, d.seq)) is not None and p.breath:
                breaths.append(p.breath)
    return breaths, pipe


def test_spikes_no_longer_reach_etco2():
    clean, pipe = run_pipeline("artifacts", 120)
    raw, _ = run_pipeline("artifacts", 120, despike=False, reject_bad_breaths=False)
    assert pipe.despiker.replaced > 50
    good = [b.etco2 for b in clean if b.quality == "ok"]
    assert all(36 < x < 40 for x in good)  # simulated EtCO2 is 38
    assert max(b.etco2 for b in raw) > 45  # without cleaning, spikes inflate EtCO2
    assert any(b.quality == "artifact" for b in clean)  # the partial breaths
    assert 36 < etco2_median(clean[-3:]) < 40


def test_normal_and_moderately_noisy_signals_are_not_rejected():
    for scenario in ("normal", "noisy"):
        bs, _ = run_pipeline(scenario, 60)
        assert sum(b.quality != "ok" for b in bs) == 0, scenario


# ------------------------------------------------------------------ in the engine


def test_engine_keeps_rejected_breaths_out_of_numbers_trend_and_alarms(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, high_mm_hg=42))
    e.set_sim(scenario="artifacts")
    e.connect("simulator")
    e.start_recording()
    run(e, 150)
    assert not e.alarms.get("co2High")  # spikes would have crossed 42 mmHg
    bad = [b for b in e.breaths if b.quality != "ok"]
    assert bad and all(b not in e.good for b in bad)
    assert sum(tp.quality != "ok" for tp in e.trend) == len(bad)
    assert 36 <= round(e.snapshot().numbers.etco2) <= 39
    res = e.stop_recording()
    s = e.library.parse(res.rec.id)
    assert [b.quality for b in s.breaths] == [b.quality for b in e.breaths if b.rec_n is not None]


def test_a_persistently_noisy_signal_raises_a_technical_alarm_instead_of_false_co2_alarms(tmp_path):
    rng = random.Random(3)
    base = DEFAULT_CHANNELS["channels"]["co2"]["calibration"]
    noisy_co2 = Encoders(lambda pct: (pct + rng.uniform(-2.6, 2.6) - base["offset"]) / base["gain"], DEFAULT_ENCODERS.flow, DEFAULT_ENCODERS.pneumo)
    e = make_engine(tmp_path)
    src = SimulatorSource(noisy_co2, clock=e._seconds, seed=4)
    assert e.connect_source(src)
    run(e, 60)
    assert e.alarms.is_active("noisyBreaths")
    assert not e.alarms.get("co2High")  # the noise peaks would read as EtCO2 > 50
    assert e.snapshot().numbers.rejected.startswith("noisy")


def test_analysis_loader_keeps_only_clean_breaths(tmp_path):
    pytest.importorskip("pandas")
    import importlib.util

    from conftest import ROOT

    spec = importlib.util.spec_from_file_location("load_session", ROOT / "analysis" / "load_session.py")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["load_session"] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    s = mod.load_session(str(ROOT / "sample-data" / "synthetic_session.csv"))
    q = s.breaths_all["breath_quality"]
    assert (q != "ok").sum() >= 1 and len(s.breaths) == (q == "ok").sum()
    assert s.breaths["etco2_mmHg"].min() > 36  # the partial breaths (about 25 mmHg) are gone
    assert len(mod.load_session(str(ROOT / "sample-data" / "synthetic_session.csv"), clean=False).breaths) == len(s.breaths_all)
    # the samples of those breaths are blanked too, so analysing the samples skips them
    bad = s.samples["breath_quality"].isin(["noisy", "artifact", "outlier"])
    assert bad.any() and s.samples.loc[bad, "co2_mmHg"].isna().all() and s.samples.loc[bad, "v0"].notna().all()


def test_trend_line_leaves_out_the_co2_of_rejected_breaths(tmp_path):
    e = make_engine(tmp_path)
    e.set_sim(scenario="artifacts")
    e.connect("simulator")
    run(e, 120)
    tt, vv, join = e.trend_trace(0)
    assert len(tt) == len(vv) == len(join) and not join[-1]
    hidden = [t for t, ok in zip(e.trace_t, e.trace_ok) if not ok]
    rejected = [tp for tp in e.trend if tp.quality != "ok"]
    assert rejected and hidden and len(tt) == len(e.trace_t) - len(hidden)
    # every hidden point lies within a rejected breath (and the line breaks there)
    for t in hidden:
        assert any(tp.t - 6 < t <= tp.t for tp in rejected), t
    assert sum(not j for j in join) > len(rejected) // 2
