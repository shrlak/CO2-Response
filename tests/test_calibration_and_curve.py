"""The calibration at the start of rebreathing, and the smooth response curve."""

import dataclasses
import random

import numpy as np
import pytest
from conftest import ROOT
from test_engine import make_engine, run

from co2response.processing.metrics import TestWindow, co2_response, lowess_curve, response_axes, response_curve
from co2response.storage.report import build_report, result_lines, session_response
from co2response.storage.session_file import parse_session_text


def breaths(n=30, every=4.0):
    """Breaths every 4 s: flat for 3 s of rebreathing, then EtCO2 and MV rising."""
    return [(i * every, 38 + max(0, i - 5) * 0.8, 7 + max(0, i - 5) * 0.5, i) for i in range(n)]


# ------------------------------------------------------------------ the calibration


def test_no_calibration_is_the_old_behaviour():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 0.0)
    assert r.skipped == [] and r.calibration == [] and r.calibration_end is None and len(r.rebreathing) == 25


def test_the_first_breaths_and_seconds_are_calibration_and_not_fitted():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 0.0, None, 4, 15)
    # the 4 breaths at 20, 24, 28 and 32 s; 15 s after the start is 35 s, so that is when it ends
    assert [p.key for p in r.calibration] == [5, 6, 7, 8] and r.calibration_end == 35.0
    assert [p.key for p in r.skipped] == [5, 6, 7, 8] and len(r.rebreathing) == 21
    # more breaths than seconds: the breaths decide
    r = co2_response(breaths(every=2.0, n=60), TestWindow(0.0, 20.0, None), 0.0, None, 6, 3)
    assert len(r.calibration) == 6 and r.calibration_end == pytest.approx(30.0)


def test_the_longer_of_the_calibration_and_the_skip_decides_where_the_fit_begins():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 30.0, None, 4, 15)
    assert len(r.calibration) == 4 and len(r.skipped) == 8  # 20 s .. 48 s are left out
    assert min(p.t for p in r.rebreathing) >= 50.0
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 5.0, None, 4, 15)
    assert len(r.skipped) == 4  # the calibration is longer than the skip


def test_a_calibration_that_has_not_finished_leaves_every_breath_of_it_out():
    r = co2_response(breaths(n=8), TestWindow(0.0, 20.0, None), 0.0, None, 4, 0)  # 3 breaths so far
    assert r.calibration_end is None and len(r.calibration) == 3 and r.rebreathing == [] and r.fit is None
    r = co2_response(breaths(n=60), TestWindow(0.0, 20.0, None), 0.0, None, 40, 0)
    assert len(r.calibration) == 40


def test_breaths_after_the_end_of_the_test_are_not_calibration():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, 30.0), 0.0, None, 4, 0)
    assert [p.key for p in r.calibration] == [5, 6, 7] and r.calibration_end is None  # only 3 before the end
    assert all(p.t >= 30.0 for p in r.skipped[len(r.calibration) :])


def test_the_baseline_is_not_calibrated():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 0.0, None, 4, 15)
    assert len(r.baseline) == 5 and all(p.t < 20.0 for p in r.baseline)


def test_the_engine_shows_calibrating_then_settling_then_recording(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(test=dataclasses.replace(e.settings.test, response_skip_sec=25, calibration_breaths=4, calibration_sec=10))
    e.connect("simulator")
    run(e, 40)
    e.start_baseline()
    run(e, 20)
    assert e.snapshot().test.stage == ""
    m = e.add_marker()
    e.rename_marker(m.id, "Rebreathing")
    e.set_sim(scenario="co2Ramp")
    run(e, 3)
    ts = e.snapshot().test
    assert ts.stage == "calibrating" and "CALIBRATING: breath" in ts.text and "of 4" in ts.text
    seen = {ts.stage}
    for _ in range(14):
        run(e, 3)
        ts = e.snapshot().test
        seen.add(ts.stage)
    assert seen >= {"calibrating", "settling", "recording"} and ts.stage == "recording" and "RECORDING RESPONSE" in ts.text
    res = e.co2_response()
    assert len(res.calibration) >= 4 and all(p.t >= res.calibration[-1].t for p in res.rebreathing)
    # turning the calibration off leaves only the seconds left out
    e.update_settings(test=dataclasses.replace(e.settings.test, calibration_breaths=0, calibration_sec=0))
    assert e.co2_response().calibration == [] and e.snapshot().test.stage in ("settling", "recording")


def test_the_settings_are_clamped_and_written_in_the_header(tmp_path):
    from co2response.settings import TestSettings, sanitize_test

    s = sanitize_test(TestSettings(calibration_breaths=99, calibration_sec=-5))
    assert (s.calibration_breaths, s.calibration_sec) == (20, 0)
    e = make_engine(tmp_path)
    e.update_settings(test=dataclasses.replace(e.settings.test, calibration_breaths=6, calibration_sec=12))
    assert (e.settings.test.calibration_breaths, e.settings.test.calibration_sec) == (6, 12)


SAMPLE = (ROOT / "sample-data" / "synthetic_session.csv").read_text()
HEADER = '"responseSkipSec":30,"calibrationBreaths":4,"calibrationSec":15,'


def sample_with(breaths_n, sec):
    assert HEADER in SAMPLE
    return parse_session_text(SAMPLE.replace(HEADER, f'"responseSkipSec":0,"calibrationBreaths":{breaths_n},"calibrationSec":{sec},'))


def test_a_recording_is_analysed_with_the_calibration_its_header_names():
    none, cal = session_response(sample_with(0, 0)), session_response(sample_with(5, 10))
    assert none.calibration == [] and len(cal.calibration) == 5 and len(cal.rebreathing) == len(none.rebreathing) - 5
    r = build_report(sample_with(5, 10))
    assert any("5 calibration breaths at the start of rebreathing" in v for _, v in result_lines(r))
    # files from before the calibration existed are analysed as they were
    old = session_response(parse_session_text(SAMPLE.replace('"calibrationBreaths":4,"calibrationSec":15,', "")))
    assert old.calibration == [] and len(old.skipped) > 0


def test_the_stand_alone_analysis_library_applies_the_same_calibration():
    pytest.importorskip("pandas")
    import sys

    sys.path.insert(0, str(ROOT / "python"))
    from co2r.session import co2_response as lib_response
    from co2r.session import load_session

    path = ROOT / "sample-data" / "synthetic_session.csv"
    s = load_session(str(path))
    a, b = lib_response(s, calibration_breaths=0, calibration_s=0, skip_s=0), lib_response(s, calibration_breaths=5, calibration_s=10, skip_s=0)
    assert a["calibration_n"] == 0 and b["calibration_n"] == 5 and b["n"] == a["n"] - 5
    app = session_response(sample_with(5, 10))
    assert len(app.rebreathing) == b["n"]


# ------------------------------------------------------------------ the curve


def noisy(seed=3, n=60, sd=2.5):
    rnd = random.Random(seed)
    xs = [40 + 0.4 * i for i in range(n)]
    return xs, [8 + 0.5 * (x - 40) + rnd.gauss(0, sd) for x in xs]


def test_the_curve_is_dense_and_spans_the_breaths():
    xs, ys = noisy()
    cx, cy = response_curve(xs, ys)
    assert len(cx) == 200 and cx[0] == pytest.approx(min(xs)) and cx[-1] == pytest.approx(max(xs))
    assert all(np.isfinite(cy))
    assert response_curve([38] * 6, [7] * 6) == ([], []) and response_curve([38, 39, 40], [7, 8, 9]) == ([], [])


def test_the_curve_follows_a_bend_and_ignores_outliers():
    xs = [40 + 0.5 * i for i in range(40)]
    truth = lambda x: 5 + 0.05 * (x - 40) ** 2  # noqa: E731  (a response that steepens)
    ys = [truth(x) for x in xs]
    ys[20] += 15  # one erratic breath
    ys[7] -= 9
    cx, cy = response_curve(xs, ys)
    assert max(abs(a - truth(x)) for x, a in zip(cx, cy)) < 0.6  # much closer than the old LOWESS allowed (2.0)
    slopes = np.diff(cy) / np.diff(cx)
    assert slopes[-1] > 2 * slopes[10]


def test_the_curve_does_not_wiggle_with_scatter_and_never_runs_the_wrong_way():
    for seed in range(8):
        xs, ys = noisy(seed, sd=3.0)
        cx, cy = response_curve(xs, ys)
        d = np.diff(cy)
        assert np.all(d >= -1e-6)  # monotone: MV does not fall as EtCO2 rises
    # a falling trend is kept monotone the other way
    xs, ys = noisy(1)
    cx, cy = response_curve(xs, [20 - (y - 8) for y in ys])
    assert np.all(np.diff(cy) <= 1e-6)


def smoothness(cy):
    """Root mean square change of the curve's curvature (lower: smoother)."""
    d2 = np.diff(cy, 2)
    return float(np.sqrt(np.mean(np.diff(d2) ** 2)))


def test_the_curve_is_smoother_than_the_old_one_on_the_same_breaths():
    xs, ys = noisy(5, sd=2.0)
    nx, ny = response_curve(xs, ys, n_out=48)
    ox, oy = lowess_curve(xs, ys)
    assert smoothness(ny) <= smoothness(oy) + 1e-12
    # and it is not just a straight line when the data bend
    bx = [40 + 0.5 * i for i in range(50)]
    by = [4 + 0.04 * (x - 40) ** 2 + random.Random(2).gauss(0, 0.3) for x in bx]
    _, cy = response_curve(bx, by)
    assert abs(float(np.corrcoef(np.linspace(0, 1, 200), cy)[0, 1])) < 0.99


def test_the_axes_include_the_whole_curve():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 0.0, None, 4, 15)
    x0, x1, y0, y1 = response_axes(r)
    cx, cy = response_curve([p.etco2 for p in r.rebreathing], [p.mv for p in r.rebreathing])
    assert x0 <= min(cx) and x1 >= max(cx) and y0 <= min(cy) and y1 >= max(cy)


def bow(cx, cy):
    """How far the curve is from the straight line between its ends, at each point."""
    cx, cy = np.array(cx), np.array(cy)
    return cy - (cy[0] + (cy[-1] - cy[0]) * (cx - cx[0]) / (cx[-1] - cx[0]))


def test_the_curve_is_always_curved_even_for_a_perfect_line():
    from co2response.processing.metrics import MIN_BOW

    xs = [40 + 0.4 * i for i in range(60)]
    rnd = random.Random(1)
    cases = {
        "perfect line": [8 + 0.5 * (x - 40) for x in xs],
        "noisy line": [8 + 0.5 * (x - 40) + rnd.gauss(0, 2.5) for x in xs],
        "steepening": [5 + 0.05 * (x - 40) ** 2 for x in xs],
        "flattening": [30 - 0.05 * (64 - x) ** 2 for x in xs],
        "falling": [30 - 0.5 * (x - 40) + rnd.gauss(0, 1) for x in xs],
    }
    for name, ys in cases.items():
        cx, cy = response_curve(xs, ys)
        rise = abs(cy[-1] - cy[0])
        assert np.max(np.abs(bow(cx, cy))) >= MIN_BOW * rise * 0.99, name  # visibly bent, never a straight line
        d = np.diff(cy)
        assert np.all(d >= -1e-9) or np.all(d <= 1e-9), name  # and still never running the wrong way
        assert cx[0] == pytest.approx(min(xs)) and cx[-1] == pytest.approx(max(xs)), name


def test_the_bend_goes_the_way_the_breaths_bend():
    xs = [40 + 0.5 * i for i in range(40)]
    cx, cy = response_curve(xs, [5 + 0.05 * (x - 40) ** 2 for x in xs])  # steepening: below the chord
    assert bow(cx, cy)[100] < 0
    cx, cy = response_curve(xs, [30 - 0.05 * (59.5 - x) ** 2 for x in xs])  # flattening: above it
    assert bow(cx, cy)[100] > 0
    cx, cy = response_curve(xs, [8 + 0.5 * (x - 40) for x in xs])  # no lean either way: convex, as the response usually is
    assert bow(cx, cy)[100] < 0


def test_the_straight_line_and_its_slope_are_unchanged_by_the_curve():
    r = co2_response(breaths(), TestWindow(0.0, 20.0, None), 0.0, None, 4, 15)
    assert r.fit is not None and r.slope == pytest.approx(0.625, rel=1e-6) and r.r == pytest.approx(1.0)  # the dashed line is still the least-squares fit
