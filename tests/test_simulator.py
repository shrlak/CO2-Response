import re

from conftest import DEFAULT_ENCODERS

from co2response.protocol import SeqTracker, parse_line
from co2response.sim.core import SimulatorCore
from co2response.sim.physiology import PhysioModel, mulberry32


def run(core: SimulatorCore, seconds: float):
    out = []
    for _ in range(int(seconds * core.rate_hz)):
        out += [parse_line(line) for line in core.next_sample()]
    return out


def test_mulberry32_matches_the_javascript_generator():
    r = mulberry32(100)
    assert [r() for _ in range(3)] == [0.2043598669115454, 0.31482121953740716, 0.531918739201501]


def test_breathes_at_the_set_rate():
    m = PhysioModel(rr=15)
    starts = 0
    prev = 0.0
    for _ in range(60 * 100):
        f = m.step(0.01).flow_lpm
        if prev <= 3 < f:
            starts += 1
        prev = f
    assert 14 <= starts <= 16


def test_peaks_at_the_set_end_tidal_co2():
    m = PhysioModel(etco2=40, barometric_mm_hg=760)
    peak = 0.0
    for _ in range(20 * 100):
        peak = max(peak, ((m.step(0.01).co2_pct or 0) / 100) * 760)
    assert 39.3 < peak < 41


def test_raises_co2_in_the_ramp_scenario():
    m = PhysioModel(scenario="co2Ramp", ramp_mm_hg_per_min=15)
    for _ in range(60 * 100):
        m.step(0.01)
    assert round(m.target_etco2) == 38 + 15


def test_stops_flow_during_apnea():
    m = PhysioModel(scenario="apnea")
    max_flow = max(abs(m.step(0.01).flow_lpm) for _ in range(10 * 100))
    assert max_flow < 2


def test_emits_valid_gap_free_data_lines_at_100_hz():
    lines = run(SimulatorCore(DEFAULT_ENCODERS), 5)
    assert len(lines) == 500
    seq = SeqTracker()
    for p in lines:
        assert p.kind == "data"
        seq.push(p.seq)
        assert len(p.volts) == 3
        assert all(v >= 0 for v in p.volts)
    assert seq.dropped == 0


def test_puts_a_disconnected_co2_sensor_on_the_high_rail():
    for p in run(SimulatorCore(DEFAULT_ENCODERS, scenario="co2Disconnect"), 1):
        assert p.volts[0] == 5


def test_sends_nothing_in_the_no_data_scenario():
    assert run(SimulatorCore(DEFAULT_ENCODERS, scenario="noData"), 1) == []


def test_injects_bad_checksums_and_dropped_samples_in_the_noisy_scenario():
    lines = run(SimulatorCore(DEFAULT_ENCODERS, scenario="noisy", seed=3), 60)
    seq = SeqTracker()
    bad = 0
    for p in lines:
        if p.kind == "bad":
            bad += 1
        if p.kind == "data":
            seq.push(p.seq)
    assert bad > 10
    assert seq.dropped > 5


def test_answers_firmware_commands():
    core = SimulatorCore(DEFAULT_ENCODERS)
    assert re.match(r"^I,FW,.*RATE,100$", core.command("INFO")[0])
    assert core.command("rate 50") == ["A,RATE,50"]
    assert core.command("RATE 500") == ["E,BAD_RATE"]
    assert core.command("STOP") == ["A,STOP"]
    assert core.next_sample() == []
    assert core.command("BOGUS") == ["E,UNKNOWN_CMD"]
