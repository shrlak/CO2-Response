"""The simulation panel's controls: every value the monitor shows can be set, the
ready-made situations reach the monitor through the real engine, and the old
scenarios keep working."""

import dataclasses
import math

import pytest
from conftest import DEFAULT_ENCODERS
from test_engine import make_engine, run

from co2response.protocol import SeqTracker, parse_line
from co2response.settings import SimSettings
from co2response.sim.controls import (
    GROUPS,
    SITUATIONS,
    SLIDERS,
    SPEED,
    SWITCHES,
    fmt_value,
    model_params,
    monitor_values,
    situation_settings,
    slider_index,
    slider_value,
    switch_on,
    switch_patch,
)
from co2response.sim.core import SimulatorCore
from co2response.sim.physiology import PhysioModel


def co2_trace(m: PhysioModel, seconds: float) -> list[float]:
    return [((m.step(0.01).co2_pct or 0) / 100) * 760 for _ in range(int(seconds * 100))]


def lines(core: SimulatorCore, seconds: float):
    out = []
    for _ in range(int(seconds * core.rate_hz)):
        out += [parse_line(x) for x in core.next_sample()]
    return out


# ------------------------------------------------------------------ the model


def test_every_control_is_a_simulator_setting_and_in_range():
    fields = {f.name for f in dataclasses.fields(SimSettings)}
    for key, s in SLIDERS.items():
        assert key in fields
        assert s.lo <= getattr(SimSettings(), key) <= s.hi, key
        assert slider_value(s, slider_index(s, getattr(SimSettings(), key))) == pytest.approx(getattr(SimSettings(), key))
    for key in SWITCHES:
        assert getattr(SimSettings(), key) is False
    assert sum(len(g.items) for g in GROUPS) + 2 == len(SLIDERS) + len(SWITCHES)  # + the speed slider and the record switch at the top
    assert SLIDERS["speed"] is SPEED and "speed" not in model_params({"speed": 5})
    assert fmt_value(SLIDERS["ie_ratio"], 2) == "1:2.0"
    assert fmt_value(SLIDERS["rr"], 15) == "15 /min"


def test_every_situation_stays_within_the_slider_ranges():
    for sit in SITUATIONS:
        sim = situation_settings(sit.id)
        for key, s in SLIDERS.items():
            assert s.lo <= getattr(sim, key) <= s.hi, (sit.id, key)


def test_default_settings_reproduce_the_original_model():
    """The panel's defaults differ from the original model only in how fast EtCO2 reaches
    a new value (``settle``); with the original pacing they reproduce it sample for sample,
    through a change of EtCO2 too. (The sample session relies on this.)"""
    a = PhysioModel(seed=4)
    b = PhysioModel(seed=4, **model_params(dataclasses.asdict(SimSettings(settle=0.5))))
    for i in range(6000):
        if i == 2000:
            a.set_params(etco2=55)
            b.set_params(etco2=55)
        sa, sb = a.step(0.01), b.step(0.01)
        assert (sa.co2_pct, sa.flow_lpm, sa.pneumo_pa) == (sb.co2_pct, sb.flow_lpm, sb.pneumo_pa)


def breath_peaks(m: PhysioModel, seconds: float) -> list[float]:
    """Each whole breath's peak CO2 (mmHg), from the model's own breath timing."""
    peaks, cur, last_start = [], -1.0, m._breath_start
    for _ in range(int(seconds * 100)):
        co2 = (m.step(0.01).co2_pct / 100) * 760
        if m._breath_start != last_start:
            peaks.append(cur)
            cur, last_start = -1.0, m._breath_start
        cur = max(cur, co2)
    return peaks[1:]  # the first may have started before the change


@pytest.mark.parametrize("phase", ["inspiration", "expiration"])
def test_a_new_etco2_shows_on_the_next_breath(phase):
    """The panel's default: a new EtCO2 is on the next breath, or on the breath being
    drawn when it is still inspiring, instead of halving the gap breath by breath."""
    fast = PhysioModel(noise=0, settle=1)
    slow = PhysioModel(noise=0)
    t = 4 * 3 + (0.5 if phase == "inspiration" else 2.5)  # 4 s breaths, 1.33 s inspiration
    for m in (fast, slow):
        co2_trace(m, t)
        m.set_params(etco2=56)
    assert fast.expected()["etco2"] == (56 if phase == "inspiration" else 38)
    peaks = breath_peaks(fast, 13)
    assert all(p == pytest.approx(56, abs=0.5) for p in peaks)
    assert breath_peaks(slow, 13)[0] < 52  # the original model: halfway each breath

    # no step in the trace where the inspiring breath takes the new value
    m = PhysioModel(noise=0, settle=1)
    before = co2_trace(m, 12.5)[-1]
    m.set_params(etco2=56)
    assert abs(co2_trace(m, 0.01)[0] - before) < 0.5


def test_inspired_co2_raises_the_baseline():
    m = PhysioModel(fico2=8, etco2=40)
    trace = co2_trace(m, 20)[500:]
    assert 6.5 < min(trace) < 9
    assert 39 < max(trace) < 41.5


def test_obstruction_rounds_the_capnogram_but_keeps_etco2():
    def rise_time(**kw) -> float:
        """Seconds from the start of expiration until CO2 is within 10 % of EtCO2."""
        m = PhysioModel(rr=15, etco2=40, noise=0, **kw)
        tr = co2_trace(m, 8)[400:800]  # breath 2: 1.33 s inspiration then expiration
        return next(i for i, v in enumerate(tr) if i > 133 and v > 36) / 100 - 4 / 3

    assert rise_time() < 0.6
    assert rise_time(obstruction_pct=90) > 1.2
    m = PhysioModel(rr=15, etco2=40, obstruction_pct=90, noise=0)
    assert max(co2_trace(m, 20)) == pytest.approx(40, abs=0.6)


def test_ie_ratio_sets_peak_inspiratory_flow():
    fast = PhysioModel(**model_params({"ie_ratio": 1.0}))
    slow = PhysioModel(**model_params({"ie_ratio": 3.0}))
    assert fast.expected()["vt"] == pytest.approx(500)
    # half-sine: peak = VT * 60 * pi / (2 * Ti); Ti is 2 s at 1:1, 1 s at 1:3
    assert fast.expected()["peak"] == pytest.approx(0.5 * 60 * math.pi / 4)
    assert slow.expected()["peak"] == pytest.approx(2 * fast.expected()["peak"])


def test_drive_scales_the_breathing_response_to_co2():
    def mv_at_top(drive):
        m = PhysioModel(ramp=True, ramp_max_mm_hg=55, drive=drive)
        m._et = 55
        m._plan_breath()
        return m.expected()["mv"]

    assert mv_at_top(0) == pytest.approx(7.5)
    assert mv_at_top(1) > 1.5 * mv_at_top(0)
    assert mv_at_top(2) > mv_at_top(1)


def test_switches_unplug_sensors_and_stop_the_stream():
    core = SimulatorCore(DEFAULT_ENCODERS, flow_disconnected=True, pneumo_disconnected=True)
    for p in lines(core, 1):
        assert p.volts[1] == 5 and p.volts[2] == 5 and p.volts[0] < 5
    core.model.set_params(stop_data=True)
    assert lines(core, 1) == []
    core.model.set_params(stop_data=False, apnea=True)
    flows = [abs(core.model.step(0.01).flow_lpm) for _ in range(500)]
    assert max(flows) < 2


def test_line_errors_drop_and_corrupt_lines():
    data = lines(SimulatorCore(DEFAULT_ENCODERS, line_error_pct=6, seed=2), 30)
    seq = SeqTracker()
    for p in data:
        if p.kind == "data":
            seq.push(p.seq)
    bad = sum(p.kind == "bad" for p in data)
    assert 80 < bad < 160  # 4 % of 3000 corrupted
    assert 120 < seq.dropped < 240  # 2 % lost + the 4 % corrupted, which are discarded


def test_flow_sensor_gain_and_offset():
    m = PhysioModel(noise=0, flow_gain=0.5, flow_offset_lpm=5)
    ref = PhysioModel(noise=0)
    for _ in range(300):
        a, b = m.step(0.01), ref.step(0.01)
        assert a.flow_lpm == pytest.approx(0.5 * b.flow_lpm + 5)
        assert a.pneumo_pa == pytest.approx(b.pneumo_pa)  # the pneumotach is unaffected


def test_switches_follow_the_old_scenarios():
    sim = SimSettings(scenario="apnea")
    assert switch_on(sim, "apnea")
    assert switch_patch(sim, "apnea", False) == {"apnea": False, "scenario": "normal"}
    assert switch_patch(SimSettings(), "apnea", True) == {"apnea": True}
    assert switch_on(SimSettings(scenario="co2Ramp"), "ramp")


# ------------------------------------------------------------------ through the engine


STEADY = [s for s in SITUATIONS if not s.test]  # the whole tests change as they run: tested below


@pytest.mark.parametrize("sit", STEADY, ids=[s.id for s in STEADY])
def test_each_situation_reaches_the_monitor(tmp_path, sit):
    """The monitor measures what the situation sets: every number within a few %."""
    e = make_engine(tmp_path)
    e.apply_situation(sit.id)
    assert e.source.kind == "simulator"
    run(e, 75 if sit.values.get("ramp") else 40)
    s = e.snapshot()
    exp = e.sim_expected()
    mon = monitor_values(s.numbers)
    assert exp["clipping"] == []
    stale = {k for k, v in s.stale.items() if v}
    active = {a.id for a in s.alarms if a.state == "active"}
    v = sit.values
    if v.get("stop_data"):
        assert "noData" in active
        return
    if v.get("apnea"):
        assert "apnea" in active and math.isnan(mon["rr"])
        return
    expected_alarms = {"co2High", "audioOff"}
    for sensor, alarm, chan in (("co2_disconnected", "railCo2", "co2"), ("flow_disconnected", "railFlow", "flow"), ("pneumo_disconnected", "railPneumo", "pneumo")):
        assert (alarm in active) == bool(v.get(sensor)), sit.id
        assert (chan in stale) == bool(v.get(sensor)), sit.id
        if v.get(sensor):
            expected_alarms.add(alarm)
    assert mon["rr"] == pytest.approx(exp["rr"], abs=1.0)
    if "co2" not in stale:
        lag = 3 if v.get("ramp") else 1.5  # the median of 3 breaths trails a rising CO2
        assert mon["etco2"] == pytest.approx(exp["etco2"], abs=lag)
        assert mon["fico2"] == pytest.approx(exp["fico2"], abs=1.5)
    if "pneumo" not in stale:
        assert mon["vt"] == pytest.approx(exp["vt"], rel=0.05 if v.get("ramp") else 0.03)
    if "flow" not in stale:
        assert mon["peak"] == pytest.approx(exp["peak"], rel=0.12 if v.get("ramp") else 0.08, abs=2)
    if not (v.get("noise", 1) > 1 or v.get("line_error_pct") or v.get("partial_pct")):
        assert not active - expected_alarms, active


def test_situations_change_the_running_simulator_in_place(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 10)
    src = e.source
    e.apply_situation("hypoventilation")
    assert e.source is src  # no reconnect, no gap in the data
    assert e.settings.sim.preset == "hypoventilation" and not e.settings.sim.adjusted
    e.set_sim(rr=9)
    assert e.settings.sim.adjusted
    assert src.core.model.params.rr == 9
    e.apply_situation("rebreathing")
    assert [m.label for m in e.markers] == ["Rebreathing"]
    assert src.core.model.params.ramp


@pytest.mark.parametrize("speed", [1, 5])
def test_a_situation_reaches_the_monitor_quickly(tmp_path, speed):
    """From a normal adult to hypoventilation (10 s breaths): the monitor's large EtCO2
    (the median of 3 breaths) is there in about 22 s at real time, where the original
    gradual approach took about 52 s, and five times sooner at 5×."""
    e = make_engine(tmp_path)
    e.set_sim(speed=speed)
    e.apply_situation("normal")
    run(e, 30)
    e.apply_situation("hypoventilation")
    seconds = 0.0
    while abs(e.snapshot().numbers.etco2_median - 63) > 1.5 and seconds < 60:
        run(e, 0.5)
        seconds += 0.5
    assert seconds <= (26 if speed == 1 else 6)


def test_speed_runs_simulated_time_faster(tmp_path):
    e = make_engine(tmp_path)
    e.set_sim(speed=5)
    assert not e.settings.sim.adjusted  # the speed is not a change to the situation
    e.connect("simulator")
    run(e, 12)
    s = e.snapshot()
    assert s.source_label == "Simulator (5× speed)"
    # the device clock runs 5× (a minute of breathing in 12 s), and what is measured on it stays right
    assert e._last.t_ms == pytest.approx(60_000, abs=500)
    assert 13 <= len(e.good) <= 16
    assert s.numbers.rr == pytest.approx(15, abs=0.5) and s.numbers.mv == pytest.approx(7.5, rel=0.05)
    # choosing a situation keeps the speed; it can be changed while running, and a recording notes it
    e.apply_situation("rebreathing")
    assert e.settings.sim.speed == 5 and e.source.speed == 5
    e.start_recording()
    run(e, 1)
    e.set_sim(speed=1)
    assert e.source.speed == 1 and e.snapshot().source_label == "Simulator"
    run(e, 1)
    text = e.stop_recording().path.read_text(encoding="utf-8")  # session files are UTF-8 on every OS
    assert "# source,Simulator (5× speed)" in text and "SIM_SPEED 1" in text
    # a stored speed out of range runs at the nearest end
    e.set_sim(speed=500)
    assert e.source.run_speed == 50


def test_the_panel_warns_when_flow_would_clip(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.set_sim(vt_ml=1800, rr=30)
    run(e, 5)
    assert e.sim_expected()["clipping"] == ["flow sensor", "pneumotach"]
    e.set_sim(vt_ml=500, rr=15)
    run(e, 10)
    assert e.sim_expected()["clipping"] == []


# ------------------------------------------------------------------ whole simulated tests


def run_whole_test(e, limit_s: float = 400) -> float:
    """Run until the whole simulated test has finished; returns the real seconds it took."""
    seconds = 0.0
    while e._sim_test is not None and seconds < limit_s:
        run(e, 1)
        seconds += 1
    assert e._sim_test is None, e.sim_test_text()
    return seconds


def test_a_whole_simulated_test_fills_every_graph(tmp_path):
    """The simulation presses the test keys itself: baseline, rebreathing to the stop
    alarm, end, recovery. Afterwards the trends and the response plot are all there."""
    e = make_engine(tmp_path)
    e.set_sim(speed=10)
    e.apply_situation("test_normal")
    assert e.snapshot().test.phase == "baseline" and e.sim_test_text().startswith("Whole test: baseline")
    assert not e.source.core.model.params.ramp  # CO2 does not rise during the baseline
    took = run_whole_test(e)
    assert took < 40  # about 3.5 simulated minutes at 10x
    assert [m.label for m in e.markers] == ["Baseline", "Rebreathing", "End of test"]
    w = e.test_window()
    assert w.start - w.baseline == pytest.approx(30 / 10, abs=0.6)  # 30 simulated seconds of baseline
    # it ended because the stop alarm sounded
    assert any("START co2Stop" in line.text for line in e.log) and any("START co2High" in line.text for line in e.log)
    assert any(line.text.startswith("Simulated test finished: slope") for line in e.log)
    # the response plot: baseline, fitted line, breaths outside the fit
    res = e.co2_response()
    assert len(res.baseline) >= 6 and len(res.rebreathing) >= 20 and len(res.skipped) >= 5
    assert res.baseline_etco2 == pytest.approx(38, abs=1) and res.baseline_mv == pytest.approx(7.5, rel=0.06)
    assert 0.2 < res.slope < 0.6 and res.r > 0.97
    assert max(p.etco2 for p in res.rebreathing) > 63
    # the trends: EtCO2, MV and RR all rise through the test
    pts = [q for q in e.trend if q.quality == "ok"]
    top = max(pts, key=lambda q: q.etco2)
    assert top.etco2 > 64 and top.mv > 1.5 * pts[3].mv and top.rr > pts[3].rr + 2
    # and it is back to quiet breathing with the CO2 rise off, still "not adjusted"
    assert not e.source.core.model.params.ramp and not e.settings.sim.adjusted and e.settings.sim.preset == "test_normal"
    assert e.snapshot().test.phase == "ended"
    assert e.sim_test_text() == ""


def test_blunted_and_brisk_whole_tests_give_flat_and_steep_lines(tmp_path):
    slopes = {}
    for sid in ("test_blunted", "test_normal", "test_brisk"):
        e = make_engine(tmp_path / sid)
        e.set_sim(speed=10)
        e.apply_situation(sid)
        run_whole_test(e)
        res = e.co2_response()
        assert res.fit is not None and len(res.baseline) >= 4, sid
        slopes[sid] = res.slope
    assert slopes["test_blunted"] < 0.1 < slopes["test_normal"] < slopes["test_brisk"]
    # the brisk test ends where its CO2 stops rising (52 mmHg), without reaching the limits
    assert not any("co2Stop" in line.text for line in e.log) and max(q.etco2 for q in e.trend) < 55


def test_a_whole_simulated_test_can_be_recorded_for_the_review_and_the_report(tmp_path):
    from co2response.storage.report import build_report, result_lines
    from co2response.storage.session_file import session_markers

    e = make_engine(tmp_path)
    e.set_sim(speed=10, record_tests=True)
    assert not e.settings.sim.adjusted  # like the speed, not part of a situation
    e.apply_situation("test_normal")
    assert e.recorder.active and "recording" in e.sim_test_text()
    run_whole_test(e)
    assert not e.recorder.active  # it stops the recording it started
    rec = e.library.list()[0]
    parsed = e.library.parse(rec.id)
    assert [label for _, label in session_markers(parsed.events)] == ["Baseline", "Rebreathing", "End of test"]
    lines = dict(result_lines(build_report(parsed, rec.file_name)))
    assert lines["Response slope"].endswith("L/min per mmHg") and "baseline from" in lines["Test phases"] and "ended at" in lines["Test phases"]
    assert e.settings.sim.record_tests  # choosing a situation keeps the switch
    # a recording that was already running is left running
    e.start_recording()
    e.apply_situation("test_blunted")
    run_whole_test(e)
    assert e.recorder.active


def test_the_operator_can_take_over_a_whole_simulated_test(tmp_path):
    e = make_engine(tmp_path)
    e.set_sim(speed=5)
    e.apply_situation("test_normal")
    run(e, 2)
    e.start_rebreathing()  # before the 30 s of baseline are up
    run(e, 0.5)
    assert e.source.core.model.params.ramp and e.sim_test_text().startswith("Whole test: rebreathing")
    run(e, 8)
    e.end_test()  # before the stop alarm
    run(e, 0.5)
    assert not e.source.core.model.params.ramp and "recovery" in e.sim_test_text()
    # choosing another situation stops the script
    e.apply_situation("test_normal")
    run(e, 1)
    e.apply_situation("normal")
    assert e._sim_test is None and any(line.text == "Simulated test stopped: another situation was chosen" for line in e.log)
    # and so does stopping the simulator
    e.apply_situation("test_normal")
    e.disconnect()
    e.tick()
    assert e._sim_test is None
