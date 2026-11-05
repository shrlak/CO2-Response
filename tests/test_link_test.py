import dataclasses
import math
import re
from datetime import datetime, timezone

from conftest import DEFAULT_ENCODERS

from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.engine.link_test import (
    LinkStats,
    LinkTestCollector,
    analyze_link_test,
    noise_volts,
    overall,
    report_text,
)
from co2response.protocol import SeqTracker, parse_line
from co2response.sim.core import SimulatorCore

INFO = {"FW": "1.0.0", "ADC": "ADS1115", "CH": "3", "RATE": "100", "ADDR": "0x48", "FSR": "6.144"}


def check(scenario: str, seconds: int = 20, **patch):
    """Run the simulator through the collector the way the engine does."""
    channels = clone_channels(DEFAULT_CHANNELS)
    core = SimulatorCore(DEFAULT_ENCODERS, scenario=scenario, seed=4)
    col = LinkTestCollector(channels)
    seq = SeqTracker()
    bad = 0
    for _ in range(seconds * 100):
        for line in core.next_sample():
            d = parse_line(line)
            if d.kind == "bad":
                bad += 1
            if d.kind != "data":
                continue
            seq.push(d.seq, d.t_ms)
            col.sample(d.t_ms / 1000, d.volts)
    stats = LinkStats(seconds, col.span_sec, seq.received, seq.dropped, bad, INFO, 0, None, col.stats())
    stats = dataclasses.replace(stats, **patch)
    r = analyze_link_test(stats, channels)
    return r, (lambda i: next(x for x in r if x.id == i))


def test_passes_a_healthy_stream():
    r, by = check("normal")
    assert by("handshake").status == "pass"
    assert "ADS1115 (I2C 0x48, ±6.144 V range)" in by("handshake").result
    assert by("rate").status == "pass"
    assert by("rate").result.startswith("100.0 Hz")
    assert by("drops").status == "pass"
    assert (by("corrupt").status, by("corrupt").result) == ("pass", "None")
    for i in ("ch-co2", "ch-flow", "ch-pneumo"):
        assert by(i).status == "pass"
    assert overall(r) == "pass"


def test_flags_drops_corrupt_lines_and_noise_on_a_bad_link():
    _, by = check("noisy", 60)
    assert by("drops").status != "pass"
    assert by("corrupt").status == "fail"
    assert by("ch-flow").status == "warn"
    assert re.match(r"^Noisy: noise 1\.\d+ L/min", by("ch-flow").result)
    assert "ground" in by("ch-flow").fix


def test_fails_a_co2_channel_stuck_at_the_rail():
    r, by = check("co2Disconnect")
    assert by("ch-co2").status == "fail"
    assert by("ch-co2").result.startswith("Stuck at the top")
    assert "pull-up" in by("ch-co2").fix
    assert by("ch-flow").status == "pass"
    assert overall(r) == "fail"


def test_says_what_to_do_when_the_adc_is_at_another_i2c_address():
    _, by = check("noData", 5, adc_errors=5, i2c_scan="0x49", info={"FW": "1.0.0", "ADC": "ADS1115", "CH": "3", "RATE": "100", "ADDR": "0x48"})
    assert by("adc").status == "fail"
    assert "answers at 0x49 but the firmware looks for 0x48" in by("adc").fix
    assert by("data").status == "fail"


def test_says_to_check_power_and_sda_scl_when_nothing_answers():
    _, by = check("noData", 5, adc_errors=3, i2c_scan="none")
    assert by("adc").fix.startswith("Nothing answers on the I2C bus")
    assert "SDA to A4 and SCL to A5" in by("adc").fix


def test_flags_a_missing_handshake_and_a_small_input_range():
    assert check("normal", 5, info=None)[1]("handshake").status == "warn"
    assert check("normal", 5, info={**INFO, "FSR": "4.096"})[1]("fsr").status == "warn"


def test_estimates_white_noise_sigma_from_second_differences():
    seed = 1

    def gauss():
        nonlocal seed
        seed = (seed * 16807) % 2147483647
        u = seed / 2147483647
        seed = (seed * 16807) % 2147483647
        v = seed / 2147483647
        return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)

    x = [math.sin(i / 200) + 0.002 * gauss() for i in range(20000)]
    d2 = [abs(x[i + 2] - 2 * x[i + 1] + x[i]) for i in range(len(x) - 2)]
    assert 0.0018 < noise_volts(d2) < 0.0022


def test_writes_a_plain_text_report():
    r, _ = check("co2Disconnect", 5)
    t = report_text(r, 5, datetime(2026, 11, 5, 5, 0, 0, tzinfo=timezone.utc))
    assert t.startswith("CO2-Response hardware check, 2026-11-05T05:00:00.000Z, 5 s\nOverall: FAIL")
    assert "[FAIL] CO2 sensor (A0)" in t
