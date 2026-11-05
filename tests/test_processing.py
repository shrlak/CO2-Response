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
from co2response.processing.co2 import mmhg_to_pct, pct_to_mmhg
from co2response.processing.filter import LowPass
from co2response.processing.metrics import (
    etco2_median,
    linear_fit,
    median,
    minute_ventilation,
    respiratory_rate,
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
            last = p.process(d.t_ms, d.volts)
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
            if d.kind == "data" and not p.process(d.t_ms, d.volts).valid["co2"]:
                invalid += 1
    assert invalid == 0
