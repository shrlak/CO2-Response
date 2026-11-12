import math

import pytest
from conftest import DEFAULT_ENCODERS

from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.processing.breath import BreathDetector, BreathOptions
from co2response.processing.calibration import (
    apply_calibration,
    fit_polynomial,
    interpolate_table,
    invert_calibration,
    pneumo_flow,
    syringe_k,
    syringe_k1,
    two_point_linear,
)
from co2response.processing.co2 import btps_factor, gas_factor, mmhg_to_pct, pct_to_mmhg, water_vapor_mmhg
from co2response.processing.filter import LowPass
from co2response.processing.metrics import (
    RespPoint,
    TestWindow,
    co2_response,
    co2_response_summary,
    etco2_median,
    is_rebreathing_marker,
    linear_fit,
    marker_kind,
    median,
    minute_ventilation,
    normalized_slopes,
    respiratory_rate,
    stability,
    test_window,
)
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline
from co2response.processing.quality import RailMonitor
from co2response.protocol import parse_line
from co2response.sim.core import SimulatorCore

approx = pytest.approx

# ------------------------------------------------------------------ calibration


def test_applies_linear_polynomial_and_table_calibrations():
    assert apply_calibration({"type": "linear", "gain": 2, "offset": -1}, 3) == 5
    assert apply_calibration({"type": "polynomial", "coeffs": [1, 2, 3]}, 2) == 1 + 4 + 12
    pts = [[0, 0], [1, 10], [3, 50]]
    assert apply_calibration({"type": "table", "points": pts}, 2) == 30
    assert interpolate_table(pts, -1) == -10  # extrapolates the first segment
    assert interpolate_table(pts, 4) == 70  # and the last


def test_inverts_calibrations():
    assert invert_calibration({"type": "linear", "gain": 40, "offset": -100}, 20) == approx(3)
    poly = {"type": "polynomial", "coeffs": [0, 1, 0.5]}
    assert apply_calibration(poly, invert_calibration(poly, 4, 0, 5)) == approx(4, abs=1e-6)


def test_builds_a_line_from_zero_and_span_readings():
    # CO2: room air 0.04 % reads 0.02 V, 5 % gas reads 2.50 V
    gain, offset = two_point_linear(0.02, 0.04, 2.5, 5)
    assert gain * 0.02 + offset == approx(0.04)
    assert gain * 2.5 + offset == approx(5)
    with pytest.raises(ValueError):
        two_point_linear(1, 0, 1, 5)


def test_fits_polynomials_by_least_squares():
    pts = [[x, 2 - x + 0.5 * x * x] for x in range(5)]
    c = fit_polynomial(pts, 2)
    assert c == approx([2, -1, 0.5])
    with pytest.raises(ValueError):
        fit_polynomial([[1, 1]], 1)


def test_pneumotach_flow_and_the_syringe_constant():
    assert pneumo_flow({"k1": 0.2, "k2": 0}, 100) == approx(20)
    assert pneumo_flow({"k1": 0.2, "k2": 0.001}, -100) == approx(-30)
    # 3 L pushed with ∫ΔP dt = 900 Pa·s → k1 = 60·3/900 = 0.2 L/min per Pa
    assert syringe_k(900, 3) == approx(0.2)
    assert syringe_k1(900, 0, 3, 0) == approx(0.2)
    with pytest.raises(ValueError):
        syringe_k(0, 3)


def test_co2_unit_conversion_with_optional_water_vapor_correction():
    assert pct_to_mmhg(5, 760) == approx(38)
    assert pct_to_mmhg(5, 760, True) == approx(35.65)
    assert mmhg_to_pct(38, 760) == approx(5)


# ------------------------------------------------------------------ filter


def _peak(f: LowPass, hz: float) -> float:
    peak = 0.0
    for i in range(2000):
        y = f.process(math.sin(2 * math.pi * hz * i / 100))
        if i > 1000:
            peak = max(peak, abs(y))
    return peak


def test_low_pass_passes_dc_and_slow_signals_and_attenuates_fast_noise():
    f = LowPass(10, 100)
    y = 0.0
    for _ in range(200):
        y = f.process(3)
    assert y == approx(3)
    assert _peak(LowPass(10, 100), 1) > 0.98
    assert _peak(LowPass(10, 100), 40) < 0.1


# ------------------------------------------------------------------ breaths


def synth_breaths(seconds, et=40, vt_ml=500, rr=15, flow=True, noise=0.0):
    """15 breaths/min, square-ish capnogram peaking at ``et``, half-sine inspiration of ``vt_ml``."""
    period = 60 / rr
    ti = period / 3
    qpk = ((vt_ml / 1000) * 60 * math.pi) / (2 * ti)
    det = BreathDetector()
    out = []
    seed = 7

    def rnd():
        nonlocal seed
        seed = (seed * 16807) % 2147483647
        return (seed / 2147483647 - 0.5) * 2

    for i in range(int(seconds * 100)):
        t = i / 100
        x = t % period
        if x < ti:
            q = qpk * math.sin(math.pi * x / ti)
            c = et * (1 - x / 0.1) if x < 0.1 else 0
        else:
            e = x - ti
            q = -qpk * 0.8 * math.sin(math.pi * e / (period - ti))
            c = et * e / 0.2 if e < 0.2 else et
        b = det.push(t, q + noise * rnd() if flow else math.nan, c)
        if b:
            out.append(b)
    return out


def test_rr_15_and_etco2_40_for_a_15_per_min_capnogram_peaking_at_40():
    b = synth_breaths(60)
    assert 14.5 < respiratory_rate(b) < 15.5
    assert 39.5 < etco2_median(b) < 40.5
    assert b[-1].detected_by == "flow"


def test_integrates_tidal_volume_and_minute_ventilation():
    b = synth_breaths(90, vt_ml=500)
    assert 490 < b[-1].vt_ml < 510
    assert minute_ventilation(b, b[-1].t_end) == approx(7.5, abs=0.05)


def test_falls_back_to_the_capnogram_when_there_is_no_flow_signal():
    b = synth_breaths(60, flow=False)
    assert b[-1].detected_by == "co2"
    assert round(respiratory_rate(b)) == 15
    assert round(etco2_median(b)) == 40
    assert math.isnan(b[-1].vt_ml)


def test_ignores_noise_inside_the_deadband():
    b = synth_breaths(60, noise=2.5)
    assert 13 <= len(b) <= 15


def test_rejects_breaths_shorter_than_the_minimum_length():
    det = BreathDetector(BreathOptions(deadband_lpm=3, min_breath_sec=1))
    # inspiration, brief expiration, then a second "inspiration" only 0.4 s after the first
    out = [b for i, f in enumerate([10, 10, -10, 10, 10, -10]) if (b := det.push(i * 0.08, f, 30))]
    assert out == []


def test_median_and_linear_fit():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 2, 3]) == 2.5
    assert math.isnan(median([math.nan]))
    fit = linear_fit([40, 45, 50], [8, 13, 18])
    assert fit.slope == approx(1)
    assert fit.r2 == approx(1)


# ------------------------------------------------------------------ CO2 response (Read's method)


def test_co2_response_fits_rebreathing_breaths_only_and_averages_the_baseline():
    # baseline near 44 mmHg / 6.6 L/min (as in the CHOP example), then rebreathing on
    # MV = 1.25 · (EtCO2 − 36) with alternating ±1 L/min scatter
    base = [(t, 43 + (t % 3), 6 + (t % 2) * 1.2) for t in range(10)]
    reb = [(100 + i, 47 + i, 1.25 * (47 + i - 36) + (1 if i % 2 else -1)) for i in range(18)]
    res = co2_response(base + reb, 100)
    assert res.split and len(res.baseline) == 10 and len(res.rebreathing) == 18
    assert res.baseline_etco2 == approx(sum(p[1] for p in base) / 10)
    assert res.baseline_mv == approx(sum(p[2] for p in base) / 10)
    assert res.slope == approx(1.25, abs=0.05)
    assert 0.95 < res.r < 1
    assert res.x_intercept == approx(36, abs=1)
    # fitting the baseline too would bend the line away from the rebreathing slope
    assert abs(co2_response(base + reb, None).slope - res.slope) > 0.1
    line = co2_response_summary(res, lambda v, d: f"{v:.{d}f}")
    assert line.startswith("slope 1.2") and "x-int 3" in line and "baseline 43.9 mmHg, 6.6 L/min (n 10)" in line


def test_co2_response_without_a_marker_fits_everything_and_skips_missing_values():
    pts = [(i, 40 + i, 5 + 2 * i) for i in range(5)] + [(9, math.nan, 10), (10, 50, math.nan)]
    res = co2_response(pts, None)
    assert not res.split and res.baseline == [] and len(res.rebreathing) == 5
    assert res.slope == approx(2) and res.r == approx(1) and res.x_intercept == approx(37.5)
    assert math.isnan(res.baseline_etco2)
    assert "mark “Rebreathing”" in co2_response_summary(res, lambda v, d: f"{v:.{d}f}")
    few = co2_response(pts[:4], 2)
    assert few.fit is None and math.isnan(few.slope)
    assert co2_response_summary(few, lambda v, d: f"{v:.{d}f}") == "MV vs EtCO2 · needs at least 3 rebreathing breaths"


def test_recognises_rebreathing_markers():
    assert is_rebreathing_marker("Rebreathing")
    assert is_rebreathing_marker("CO2 rebreathing started")
    assert is_rebreathing_marker("REBREATH on")
    assert not is_rebreathing_marker("Event 3")
    # an end marker is not a start, even when it mentions rebreathing
    assert not is_rebreathing_marker("Rebreathing stopped")


def test_recognises_the_markers_of_a_test():
    kinds = {
        "Baseline": "baseline",
        "baseline start": "baseline",
        "Rebreathing": "start",
        "End of test": "end",
        "Test ended": "end",
        "Stop rebreathing": "end",
        "End rebreathing": "end",
        "Finished the test": "end",
        "Event 3": None,
        "Weekend": None,
        "drug given": None,
    }
    assert {k: marker_kind(k) for k in kinds} == kinds


def test_finds_the_latest_test_among_the_markers():
    assert test_window([]) == TestWindow(None, None, None)
    assert test_window([(5, "Rebreathing")]) == TestWindow(None, 5, None)
    one = [(1, "Baseline"), (5, "Rebreathing"), (9, "End of test")]
    assert test_window(one) == TestWindow(1, 5, 9)
    # a second test: its baseline starts where the first one ended unless it is marked
    assert test_window(one + [(15, "Rebreathing")]) == TestWindow(9, 15, None)
    assert test_window(one + [(12, "Baseline"), (15, "Rebreathing"), (20, "Test ended")]) == TestWindow(12, 15, 20)
    # a new baseline after a finished test opens the next one: no rebreathing yet
    assert test_window(one + [(12, "Baseline")]) == TestWindow(12, None, None)
    # an end marker before any start is ignored
    assert test_window([(2, "End of test"), (5, "Rebreathing")]) == TestWindow(2, 5, None)


def test_response_leaves_out_the_first_seconds_and_everything_after_the_end():
    # 10 baseline breaths; 3 coached deep breaths with a huge MV; a clean response
    # MV = 1.5 · (EtCO2 − 40); then recovery breaths with CO2 falling while MV is still high
    base = [(t * 4, 42, 6) for t in range(10)]
    deep = [(100 + i * 5, 44, 25) for i in range(3)]
    reb = [(130 + i * 4, 46 + i, 1.5 * (46 + i - 40)) for i in range(15)]
    rec = [(200 + i * 4, 58 - 3 * i, 26 - i) for i in range(5)]
    pts = base + deep + reb + rec
    res = co2_response(pts, TestWindow(None, 100, 200), skip_sec=30)
    assert (len(res.baseline), len(res.rebreathing), len(res.skipped)) == (10, 15, 8)
    assert res.slope == approx(1.5) and res.x_intercept == approx(40) and res.r == approx(1)
    line = co2_response_summary(res, lambda v, d: f"{v:.{d}f}")
    assert "n 15" in line and "8 outside the fit" in line
    # without the skip and the end marker those breaths bend the line
    loose = co2_response(pts, 100)
    assert len(loose.rebreathing) == 23 and abs(loose.slope - 1.5) > 0.3
    # too few breaths after the skip: say why
    few = co2_response(base + deep, TestWindow(None, 100, None), skip_sec=30)
    assert co2_response_summary(few, lambda v, d: f"{v:.{d}f}").startswith("MV vs EtCO2 · needs at least 3 rebreathing breaths after the first 30 s")


def test_response_baseline_starts_at_its_marker_and_breaths_can_be_excluded_by_hand():
    settle = [(t, 30, 12) for t in range(0, 20, 4)]  # hyperventilating while settling in
    base = [(20 + t * 4, 42, 6, f"b{t}") for t in range(6)]
    reb = [(100 + i * 4, 46 + i, 1.5 * (46 + i - 40), f"r{i}") for i in range(12)]
    cough = (150, 50, 40, "cough")
    pts = settle + base + reb + [cough]
    res = co2_response(pts, TestWindow(20, 100, None))
    assert len(res.baseline) == 6 and res.baseline_etco2 == approx(42) and res.baseline_mv == approx(6)
    assert len(res.rebreathing) == 13 and abs(res.slope - 1.5) > 0.2
    out = co2_response(pts, TestWindow(20, 100, None), excluded=lambda k: k in ("cough", "b0"))
    assert out.slope == approx(1.5)
    assert [p.key for p in out.excluded] == ["b0", "cough"] and len(out.baseline) == 5
    assert "2 excluded by hand" in co2_response_summary(out, lambda v, d: f"{v:.{d}f}")
    # every point carries its time and key, for drawing and for clicking on it
    assert out.rebreathing[0] == RespPoint(46, 9, 100, "r0")


def test_a_baseline_with_no_rebreathing_yet_shows_only_its_own_breaths():
    old = [(t, 50 + t, 10 + t) for t in range(10)]  # an earlier test
    base = [(100 + t * 4, 42, 6) for t in range(5)]
    res = co2_response(old + base, TestWindow(100, None, None))
    assert res.split and len(res.baseline) == 5 and res.rebreathing == [] and res.fit is None


def test_baseline_stability():
    steady = [(42 + 0.3 * (i % 3), 6 + 0.2 * (i % 2)) for i in range(10)]
    ok, why = stability(steady)
    assert ok and why.startswith("EtCO2 ±0.")
    assert stability(steady[:4]) == (False, "4 of 6 breaths")
    drifting = [(38 + i, 6) for i in range(10)]
    assert stability(drifting)[0] is False
    irregular = [(42, 3 + 6 * (i % 2)) for i in range(10)]
    assert stability(irregular)[0] is False


def test_slope_normalized_for_body_size():
    n = normalized_slopes(1.2, 170, 60)
    assert n["bsa_m2"] == approx(math.sqrt(170 * 60 / 3600))
    assert n["per_kg_ml"] == approx(20) and n["per_m2"] == approx(1.2 / n["bsa_m2"])
    none = normalized_slopes(1.2, None, None)
    assert math.isnan(none["per_kg_ml"]) and math.isnan(none["per_m2"])


# ------------------------------------------------------------------ rails


def test_flags_a_channel_stuck_at_a_rail_for_2_s():
    r = RailMonitor(0, 5, 0.005)
    assert r.push(0, 0) is False
    assert r.push(1.9, 0) is False
    assert r.push(2.0, 0) is True
    assert r.push(2.1, 2.5) is False
    assert r.push(3, 4.999) is False
    assert r.push(5.1, 4.999) is True


def test_can_ignore_the_low_rail_for_a_capnograph():
    r = RailMonitor(0, 5, 0.005, 2, "high")
    assert r.push(0, 0) is False
    assert r.push(10, 0) is False
    assert r.push(11, 5) is False
    assert r.push(13, 5) is True


# ------------------------------------------------------------------ pipeline


def run_sim(scenario, seconds):
    core = SimulatorCore(DEFAULT_ENCODERS, scenario=scenario, etco2=38, rr=15, vt_ml=500)
    p = Pipeline(clone_channels(DEFAULT_CHANNELS), DEFAULT_PROCESSING)
    breaths, last = [], None
    for _ in range(seconds * 100):
        for line in core.next_sample():
            d = parse_line(line)
            if d.kind != "data":
                continue
            out = p.process(d.t_ms, d.volts, d.seq)
            if out is None:
                continue  # the spike filter is still filling
            last = out
            if last.breath:
                breaths.append(last.breath)
    return breaths, last


def test_pipeline_recovers_the_simulated_etco2_rr_and_vt():
    breaths, _ = run_sim("normal", 60)
    assert 37 < etco2_median(breaths) < 39.5
    assert round(respiratory_rate(breaths)) == 15
    assert 460 < breaths[-1].vt_ml < 540


def test_pipeline_marks_a_disconnected_co2_sensor_invalid_and_stuck():
    _, last = run_sim("co2Disconnect", 5)
    assert last.valid["co2"] is False
    assert last.stuck["co2"] is True
    assert math.isnan(last.co2_mmhg)
    assert last.valid["flow"] is True


def test_keeps_co2_valid_at_0_pct_during_inspiration():
    core = SimulatorCore(DEFAULT_ENCODERS, scenario="normal")
    p = Pipeline(clone_channels(DEFAULT_CHANNELS), DEFAULT_PROCESSING)
    invalid = 0
    for _ in range(2000):
        for line in core.next_sample():
            d = parse_line(line)
            out = p.process(d.t_ms, d.volts, d.seq) if d.kind == "data" else None
            if out is not None and not out.valid["co2"]:
                invalid += 1
    assert invalid == 0


def test_measures_expired_volume_and_each_breaths_own_minute_ventilation():
    breaths, _ = run_sim("normal", 60)
    b = breaths[-1]
    assert b.vte_ml == approx(b.vt_ml, rel=0.03)  # what goes in comes out
    assert b.mv_lpm == approx(b.vt_ml / 1000 * 60 / b.period_sec)
    assert 7 < b.mv_lpm < 8  # 15 /min × 500 mL
    # the capnogram rises about 0.3 s into expiration (the simulated dead-space washout)
    assert 0.2 < b.co2_lag_sec < 0.4


def run_lines(lines, **opts):
    import dataclasses

    p = Pipeline(clone_channels(DEFAULT_CHANNELS), dataclasses.replace(DEFAULT_PROCESSING, **opts))
    breaths = []
    for t_ms, volts in lines:
        out = p.process(t_ms, volts, 0)
        if out is not None and out.breath:
            breaths.append(out.breath)
    return breaths, p


def sim_lines(seconds, co2_late=0, **params):
    """Simulator samples as (t_ms, volts), with the CO2 channel ``co2_late`` samples late
    (a sidestream analyser's sample line)."""
    core = SimulatorCore(DEFAULT_ENCODERS, etco2=40, rr=15, vt_ml=500, **params)
    rows = [parse_line(line) for _ in range(seconds * 100) for line in core.next_sample()]
    rows = [d for d in rows if d.kind == "data"]
    out = []
    for i, d in enumerate(rows):
        v = list(d.volts)
        v[0] = rows[max(0, i - co2_late)].volts[0]
        out.append((d.t_ms, v))
    return out


def test_co2_delay_setting_lines_a_late_capnogram_up_with_the_flow():
    lines = sim_lines(60, co2_late=150)  # CO2 arrives 1.5 s late
    truth, _ = run_lines(sim_lines(60))
    late, _ = run_lines(lines)
    fixed, pipe = run_lines(lines, co2_delay_ms=1500)
    assert pipe.co2_delay_sec == approx(1.5)
    # uncorrected: the plateau spills into the next inspiration, so inspired CO2 reads
    # high and the lag shows the delay
    assert late[-1].fico2 > 20 and late[-1].co2_lag_sec > 1.5
    assert truth[-1].fico2 < 2
    # corrected: the breaths read as they would with no delay
    assert fixed[-1].fico2 == approx(truth[-1].fico2, abs=0.5)
    assert fixed[-1].etco2 == approx(truth[-1].etco2, abs=0.3)
    assert fixed[-1].co2_lag_sec == approx(truth[-1].co2_lag_sec, abs=0.03)
    assert fixed[-1].vt_ml == approx(truth[-1].vt_ml, rel=0.01)


def test_gas_condition_helpers():
    assert water_vapor_mmhg(37) == approx(47, abs=0.2)
    assert btps_factor(22, 760) == approx(1.091, abs=0.003)
    assert btps_factor(37, 760) == approx(1, abs=0.001)
    assert gas_factor(20.95) == approx(1)
    assert gas_factor(100) == approx(0.90, abs=0.005)  # pure O2 is more viscous: the pneumotach over-reads
    assert 0.9 < gas_factor(50, 7) < 1


def test_btps_and_the_bag_gas_factor_scale_the_pneumotach_volumes():
    lines = sim_lines(40)
    plain, _ = run_lines(lines)
    btps, pipe = run_lines(lines, volume_btps=True, ambient_temp_c=22)
    assert pipe.flow_factor == approx(btps_factor(22, 760))
    assert btps[-1].vt_ml == approx(plain[-1].vt_ml * btps_factor(22, 760), rel=0.005)
    # the bag-gas factor applies only while rebreathing
    import dataclasses

    p = Pipeline(clone_channels(DEFAULT_CHANNELS), dataclasses.replace(DEFAULT_PROCESSING, rebreathing_gas_factor=0.9))
    assert p.flow_factor == 1
    p.rebreathing = True
    assert p.flow_factor == approx(0.9)
    vts = [out.breath.vt_ml for t, v in lines if (out := p.process(t, v, 0)) is not None and out.breath]
    assert vts[-1] == approx(plain[-1].vt_ml * 0.9, rel=0.005)


def test_response_axes_scale_to_the_breaths():
    from co2response.processing.metrics import co2_response, response_axes

    # no breaths yet: a typical range; then both axes follow the data, y not forced to zero
    assert response_axes(co2_response([], None)) == (30.0, 50.0, 0.0, 10.0)
    pts = [(i * 2.0, 40 + i * 0.5, 14 + i * 0.3, i) for i in range(30)]
    res = co2_response(pts, 20.0)
    x0, x1, y0, y1 = response_axes(res)
    xs = [p.etco2 for p in res.baseline + res.rebreathing + res.skipped + res.excluded]
    ys = [p.mv for p in res.baseline + res.rebreathing + res.skipped + res.excluded]
    assert x0 <= min(xs) and x1 >= max(xs) and y0 <= min(ys) and y1 >= max(ys)
    assert x1 - x0 < (max(xs) - min(xs)) * 1.5 and y0 > 5 and y1 - y0 < (max(ys) - min(ys)) * 1.6  # tight, not 0 to 1.35 x max
