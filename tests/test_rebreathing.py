"""The rebreathing test (Read method), the CO2-response fit and the CO2 delay alignment.

These cover the gaps found against DTM rev02 and the mentors' description of the CHOP
test (docs/requirements-review.md): the slope is breath by breath over the rebreathing
period only, the coached breaths are left out, the fit reports r and the x-intercept,
the end-test and time limits alarm, and a sidestream CO2 lag does not move each
breath's EtCO2 into the next breath.
"""

import dataclasses
import math

from conftest import DEFAULT_ENCODERS
from pytest import approx
from test_engine import make_engine, run

from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.processing.co2delay import estimate_co2_delay
from co2response.processing.metrics import linear_fit, t95
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline
from co2response.processing.response import ResponsePoint, baseline, breath_mv, response_fit
from co2response.protocol import parse_line
from co2response.sim.core import SimulatorCore

# ------------------------------------------------------------------ fit statistics


def test_fit_reports_r_x_intercept_and_slope_uncertainty():
    xs = [40, 42, 44, 46, 48, 50, 52]
    ys = [2.0 * x - 70 + e for x, e in zip(xs, (0.3, -0.2, 0.1, -0.3, 0.2, 0.0, -0.1))]
    f = linear_fit(xs, ys)
    assert f.slope == approx(2.0, abs=0.05)
    assert f.r == approx(math.sqrt(f.r2)) and f.r > 0.99
    assert f.x_intercept == approx(-f.intercept / f.slope)
    assert f.x_intercept == approx(35, abs=0.5)
    assert f.n == 7
    assert 0 < f.slope_se < 0.05
    assert f.slope_ci95 == approx(t95(5) * f.slope_se)


def test_fit_r_has_the_sign_of_the_slope_and_a_perfect_line_has_no_error():
    f = linear_fit([1, 2, 3, 4], [8, 6, 4, 2])
    assert f.r == approx(-1)
    assert f.slope_se == approx(0, abs=1e-9)
    assert f.x_intercept == approx(5)


def test_t_critical_values():
    assert t95(1) == approx(12.706)
    assert t95(10) == approx(2.228)
    assert t95(40) == approx(2.021, abs=0.002)
    assert t95(1000) == approx(1.962, abs=0.002)


# ------------------------------------------------------------------ response selection


def test_breath_mv_is_vt_times_60_over_the_period():
    assert breath_mv(500, 4) == approx(7.5)
    assert math.isnan(breath_mv(math.nan, 4))
    assert math.isnan(breath_mv(500, 0))


def test_response_fit_uses_only_the_window_and_skips_the_coached_breaths():
    pts = [ResponsePoint(t, 40 + t, 10 + 2 * t) for t in range(20)]
    pts[3].mv = 99  # a coached deep breath
    pts[12].quality = "noisy"
    res = response_fit(pts, 2, 15, skip=2)
    # breaths 2..15 are inside; 12 is rejected; the first 2 clean ones (2, 3) are skipped
    assert res.skipped == 2 and res.rejected == 1
    assert len(res.xs) == 15 - 2 + 1 - 1 - 2
    assert res.fit.slope == approx(2) and res.fit.r == approx(1)


def test_baseline_is_stable_only_with_enough_steady_breaths():
    steady = [ResponsePoint(t, 38 + (t % 2) * 0.5, 7) for t in range(0, 60, 4)]
    b = baseline(steady, 60)
    assert b.stable and b.etco2 == approx(38, abs=0.5) and b.mv == approx(7)
    drifting = [ResponsePoint(t, 30 + t / 4, 7) for t in range(0, 60, 4)]
    assert not baseline(drifting, 60).stable
    assert not baseline(steady[-3:], 60).stable  # too few breaths


# ------------------------------------------------------------------ the test in the engine


def _model(e):
    return e._source.core.model


def test_rebreathing_test_end_to_end(tmp_path):
    e = make_engine(tmp_path)
    assert e.connect("simulator")
    e.start_recording()
    run(e, 70)
    st = e.snapshot().test
    assert st.phase == "baseline" and st.baseline.stable

    _model(e).set_params(scenario="co2Ramp", ramp_max_mm_hg=70)
    e.start_rebreathing()
    s = e.snapshot()
    assert s.test.phase == "rebreathing" and s.co2_high_limit == 65
    assert s.test.baseline.etco2 == approx(38, abs=1.5)

    # EtCO2 climbs past the normal 50 mmHg limit: no high alarm while rebreathing,
    # the approach warning at 60, then the end-test alarm above 65 (the simulated ramp
    # climbs 15 mmHg/min from 38)
    run(e, 70)
    assert [a.id for a in e.snapshot().alarms] == []  # ~55 mmHg: over 50, but rebreathing
    run(e, 25)
    names = {a.id: a for a in e.snapshot().alarms}
    assert "co2Warn" in names and "co2High" not in names
    run(e, 35)
    s = e.snapshot()
    hi = next(a for a in s.alarms if a.id == "co2High")
    assert hi.message.startswith("END TEST") and hi.value > 65
    # by now the simulated hyperpnoea is past the default pneumotach range: that is
    # reported as clipping, not as a false apnea, and the clipped breaths still count
    # for EtCO2 (their VT is unknown, so they stay out of the MV fit)
    got = [a.id for a in s.alarms]
    assert "pneumoClip" in got and "apnea" not in got
    assert any(b.clipped for b in e.breaths[-5:])
    assert all(math.isnan(b.vt_ml) for b in e.breaths[-5:] if "insp" in b.clipped)

    res = e.end_rebreathing()
    assert e.test.phase == "recovery" and res.basis == "rebreathing"
    f = res.fit
    assert res.skipped == 3
    assert f.n >= 20
    assert 0.3 < f.slope < 0.8  # simulator: MV ≈ 7.5 → 16.7 L/min over +20 mmHg
    assert f.r > 0.95
    assert 15 < f.x_intercept < 35
    # the end-test limit stays on in recovery until EtCO2 is back under the normal limit
    assert e.snapshot().co2_high_limit == 65
    _model(e).set_params(scenario="normal")
    run(e, 90)
    assert e.snapshot().co2_high_limit == 50

    text = e.stop_recording().path.read_text()
    assert "TEST_REBREATHING_START test=1 baseline_etco2=" in text
    assert "baseline_stable=yes" in text
    assert "TEST_REBREATHING_END test=1" in text and "reason=operator" in text and "slope=0." in text


def test_the_response_tab_falls_back_to_every_clean_breath_without_a_test(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    _model(e).set_params(scenario="co2Ramp")
    run(e, 60)
    res = e.response()
    assert res.basis == "window" and res.skipped == 0 and res.fit is not None


def test_rebreathing_time_limit(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, test_max_sec=60))
    run(e, 20)
    e.start_rebreathing()
    run(e, 55)
    assert "testTime" not in [a.id for a in e.snapshot().alarms]
    run(e, 10)
    t = next(a for a in e.snapshot().alarms if a.id == "testTime")
    assert t.state == "active" and t.priority == "medium" and "TIME LIMIT 1:00" in t.message
    e.end_rebreathing()
    t = next(a for a in e.snapshot().alarms if a.id == "testTime")
    assert t.state == "resolved"


def test_the_normal_high_limit_applies_outside_a_test(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    _model(e).set_params(scenario="co2Ramp")
    run(e, 120)
    hi = next(a for a in e.snapshot().alarms if a.id == "co2High")
    assert hi.message.startswith("EtCO2 HIGH") and "> 50" in hi.message


# ------------------------------------------------------------------ CO2 delay


def _stream(delay_sec, co2_delay_ms, seconds=60):
    core = SimulatorCore(DEFAULT_ENCODERS, scenario="normal", etco2=38, rr=15, vt_ml=500, co2_delay_sec=delay_sec)
    p = Pipeline(clone_channels(DEFAULT_CHANNELS), dataclasses.replace(DEFAULT_PROCESSING, co2_delay_ms=co2_delay_ms))
    breaths, samples = [], []
    for _ in range(seconds * 100):
        for line in core.next_sample():
            d = parse_line(line)
            out = p.process(d.t_ms, d.volts, d.seq) if d.kind == "data" else None
            if out is None:
                continue
            samples.append((out.t_sec, out.pneumo_lpm, out.co2_mmhg))
            if out.breath:
                breaths.append(out.breath)
    return breaths[3:], samples


def test_a_sidestream_lag_breaks_fico2_until_co2_is_aligned_with_flow():
    lagged, _ = _stream(1.5, 0)
    # without alignment, inspiration catches the previous breath's delayed plateau
    assert sum(b.fico2 for b in lagged) / len(lagged) > 10
    aligned, _ = _stream(1.5, 1750)
    ok = [b for b in aligned if b.quality == "ok"]
    assert len(ok) >= len(aligned) - 1
    assert all(b.fico2 < 2 for b in ok)
    assert sum(b.etco2 for b in ok) / len(ok) == approx(38, abs=1)


def test_estimates_the_co2_lag_from_ordinary_breathing():
    _, samples = _stream(1.5, 0, seconds=40)
    t, f, c = zip(*samples)
    lag, n = estimate_co2_delay(t, f, c)
    # 1.5 s of sample line + about 0.3 s of dead-space washout in the simulated capnogram
    assert 1.6 < lag < 2.0 and n >= 6
    _, samples = _stream(0, 0, seconds=40)
    t, f, c = zip(*samples)
    lag0, _ = estimate_co2_delay(t, f, c)
    assert 0.1 < lag0 < 0.5
    assert estimate_co2_delay([0, 1], [0, 0], [0, 0]) is None


def test_engine_measures_the_co2_delay(tmp_path):
    e = make_engine(tmp_path)
    e.set_sim(co2_delay_sec=1.0)
    e.connect("simulator")
    run(e, 5)
    got = {}
    e.measure_co2_delay(20, lambda r, err: got.update(r=r, err=err))
    run(e, 21)
    assert got["err"] is None and 1.1 < got["r"][0] < 1.5
