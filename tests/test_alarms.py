import dataclasses

from conftest import DEFAULT_ENCODERS

from co2response.alarms.engine import AlarmEngine
from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline
from co2response.protocol import parse_line
from co2response.settings import DEFAULT_ALARMS, sanitize_alarms
from co2response.sim.core import SimulatorCore


def make(**s):
    """One warning limit at 50 mmHg and no stop limit, unless the test says otherwise."""
    log = []
    e = AlarmEngine(dataclasses.replace(DEFAULT_ALARMS, **{"high_mm_hg": 50, "stop_mm_hg": None, **s}), log.append)
    return e, log


# ---------- EtCO2 limits


def test_waits_for_the_trigger_delay_before_alarming():
    e, _ = make(high_mm_hg=50, delay_breaths=2)
    e.on_breath(52, 1000)
    assert e.get("co2High") is None
    e.on_breath(48, 5000)  # back below resets the count
    e.on_breath(53, 9000)
    assert e.get("co2High") is None
    e.on_breath(54, 13000)
    r = e.get("co2High")
    assert (r.state, r.priority, r.since, r.value) == ("active", "high", 13000, 54)


def test_clears_only_below_limit_minus_hysteresis():
    e, _ = make(high_mm_hg=50, hysteresis_mm_hg=2, delay_breaths=1)
    e.on_breath(55, 0)
    e.on_breath(49, 4000)  # inside the hysteresis band: still active
    assert e.get("co2High").state == "active"
    e.on_breath(48.5, 8000)
    assert e.get("co2High").state == "active"
    e.on_breath(47.9, 12000)
    r = e.get("co2High")
    assert (r.state, r.resolved_at, r.peak) == ("resolved", 12000, 55)


def test_latches_after_the_condition_clears_until_acknowledged():
    e, log = make(delay_breaths=1)
    e.on_breath(60, 0)
    e.on_breath(40, 4000)
    assert e.get("co2High").state == "resolved"
    e.tick(600000)  # time passing does not clear it
    assert e.get("co2High").state == "resolved"
    e.acknowledge(700000)
    assert e.get("co2High") is None
    assert [x.kind for x in log] == ["start", "resolved", "cleared"]


def test_re_alarms_if_co2_rises_again_while_the_old_alarm_is_latched():
    e, _ = make(delay_breaths=2)
    e.on_breath(60, 0)
    e.on_breath(60, 4000)
    e.on_breath(40, 8000)
    assert e.get("co2High").state == "resolved"
    e.on_breath(60, 12000)
    assert e.get("co2High").state == "resolved"  # delay applies again
    e.on_breath(60, 16000)
    r = e.get("co2High")
    assert (r.state, r.since) == ("active", 16000)


def test_raises_a_medium_alarm_below_the_low_limit():
    e, _ = make(low_mm_hg=25, delay_breaths=2)
    e.on_breath(20, 0)
    e.on_breath(19, 4000)
    r = e.get("co2Low")
    assert (r.state, r.priority, r.peak) == ("active", "medium", 19)
    e.on_breath(26, 8000)  # within hysteresis of 25 + 2
    assert e.get("co2Low").state == "active"
    e.on_breath(28, 12000)
    assert e.get("co2Low").state == "resolved"


def test_ignores_breaths_without_co2_and_the_live_waveform_when_keyed_on_etco2():
    e, _ = make(delay_breaths=1)
    e.on_breath(float("nan"), 0)
    e.on_live_co2(80, 0)
    e.on_live_co2(80, 60000)
    assert e.list() == []


def test_high_alarm_message_prints_limits_without_decimals():
    e, _ = make(high_mm_hg=50, delay_breaths=1)
    e.on_breath(52.4, 0)
    assert e.get("co2High").message == "EtCO2 HIGH 52 mmHg > 50"


# ---------- live CO2 source


def test_live_source_needs_the_value_past_the_limit_for_delay_sec():
    e, _ = make(source="live", high_mm_hg=50, delay_sec=5)
    for t in range(0, 4901, 100):
        e.on_live_co2(55, t)
    assert e.get("co2High") is None
    e.on_live_co2(55, 5000)
    assert e.get("co2High").state == "active"
    e.on_live_co2(49, 5100)  # inside hysteresis
    assert e.get("co2High").state == "active"
    e.on_live_co2(47, 5200)
    assert e.get("co2High").state == "resolved"


def test_live_source_restarts_the_delay_when_the_value_dips():
    e, _ = make(source="live", high_mm_hg=50, delay_sec=5)
    e.on_live_co2(55, 0)
    e.on_live_co2(40, 3000)
    e.on_live_co2(55, 4000)
    e.on_live_co2(55, 8000)
    assert e.get("co2High") is None
    e.on_live_co2(55, 9000)
    assert e.get("co2High").state == "active"


# ---------- acknowledge, silence and re-arm


def test_acknowledging_silences_audio_and_silence_re_arms():
    e, log = make(delay_breaths=1, silence_sec=120)
    e.on_breath(60, 0)
    e.acknowledge(1000)
    r = e.get("co2High")
    assert (r.state, r.acknowledged) == ("active", True)
    assert e.is_silenced(1000)
    assert e.silence_remaining_ms(61000) == 60000
    e.tick(120999)
    assert e.is_silenced(120999)
    e.tick(121000)
    assert not e.is_silenced(121000)
    assert [x.kind for x in log] == ["start", "acknowledged", "silenced", "rearmed"]


def test_never_silences_for_more_than_120_s():
    e, _ = make(silence_sec=600)
    e.silence(0)
    assert e.silence_remaining_ms(0) == 120000


def test_cancels_a_silence_on_request():
    e, _ = make()
    e.silence(0)
    e.unsilence(1000)
    assert not e.is_silenced(1000)


def test_sanitizes_stored_settings():
    s = sanitize_alarms(dataclasses.replace(DEFAULT_ALARMS, volume=0, silence_sec=999, delay_breaths=0))
    assert s.volume == 0.1
    assert s.silence_sec == 120
    assert s.delay_breaths == 1


# ---------- two tiers: a warning limit and a stop limit


def test_defaults_are_a_warning_at_60_and_a_stop_at_65():
    assert (DEFAULT_ALARMS.high_mm_hg, DEFAULT_ALARMS.stop_mm_hg) == (60, 65)
    # the stop limit can never sit below the warning limit
    assert sanitize_alarms(dataclasses.replace(DEFAULT_ALARMS, high_mm_hg=70, stop_mm_hg=65)).stop_mm_hg == 70


def test_warning_is_medium_priority_while_a_stop_limit_stands_behind_it():
    e, _ = make(high_mm_hg=60, stop_mm_hg=65, delay_breaths=2)
    e.on_breath(61, 0)
    e.on_breath(62, 4000)
    assert e.get("co2High").priority == "medium" and e.get("co2Stop") is None
    assert e.active_priority() == "medium"
    # without a stop limit the single limit stays high priority
    solo, _ = make(high_mm_hg=60, delay_breaths=1)
    solo.on_breath(61, 0)
    assert solo.get("co2High").priority == "high"


def test_stop_limit_alarms_on_the_live_co2_within_a_second_not_after_two_breaths():
    e, _ = make(high_mm_hg=60, stop_mm_hg=65, stop_delay_sec=0.5, delay_breaths=2)
    for i in range(100):  # CO2 at 66 mmHg, sampled at 100 Hz
        e.on_live_co2(66, i * 10)
        if e.is_active("co2Stop"):
            break
    r = e.get("co2Stop")
    assert r is not None and r.priority == "high" and r.since == 500
    assert r.message.startswith("STOP TEST: CO2 66 mmHg at the stop limit 65")
    assert e.get("co2High") is None  # the warning still waits for its two breaths


def test_stop_limit_ignores_a_blip_shorter_than_its_delay():
    e, _ = make(stop_mm_hg=65, stop_delay_sec=0.5)
    for i in range(30):
        e.on_live_co2(70 if i < 20 else 40, i * 10)  # 200 ms above, then back down
    for i in range(30, 60):
        e.on_live_co2(70 if i < 50 else 40, i * 10)  # again, timed from scratch
    assert e.get("co2Stop") is None


def test_stop_alarm_fires_on_a_single_breath_and_resolves_on_a_breath_back_under_the_limit():
    e, _ = make(high_mm_hg=60, stop_mm_hg=65, hysteresis_mm_hg=2, delay_breaths=2)
    e.on_breath(66, 0)
    assert e.is_active("co2Stop")
    # the capnogram drops on every inspiration: that alone does not resolve it
    for i in range(300):
        e.on_live_co2(2, 1000 + i * 10)
    assert e.is_active("co2Stop")
    e.on_breath(64, 5000)  # inside the hysteresis band
    assert e.is_active("co2Stop")
    e.on_breath(62, 9000)
    assert e.get("co2Stop").state == "resolved"


def test_stop_alarm_resolves_after_20_s_under_the_limit_when_no_breath_is_reported():
    e, _ = make(stop_mm_hg=65, hysteresis_mm_hg=2)
    for i in range(100):
        e.on_live_co2(70, i * 10)
    assert e.is_active("co2Stop")
    for i in range(1999):
        e.on_live_co2(30, 1000 + i * 10)
    assert e.is_active("co2Stop")
    for i in range(1999, 2010):
        e.on_live_co2(30, 1000 + i * 10)
    assert e.get("co2Stop").state == "resolved"


def test_a_new_high_alarm_cancels_the_silence_of_the_warning():
    e, log = make(high_mm_hg=60, stop_mm_hg=65, delay_breaths=1)
    e.on_breath(62, 0)
    e.acknowledge(1000)  # silences the warning for 120 s
    assert e.is_silenced(2000)
    e.on_breath(66, 5000)
    assert e.is_active("co2Stop") and not e.is_silenced(5000)
    assert [x.kind for x in log] == ["start", "acknowledged", "silenced", "unsilenced", "start"]
    assert [a.id for a in e.list()][:2] == ["co2Stop", "co2High"]
    # a medium alarm does not cancel a silence, and neither does the test alarm
    e.silence(6000)
    e.set_condition("noData", True, 7000, "medium", "NO DATA")
    e.set_condition("test", True, 7000, "high", "TEST")
    assert e.is_silenced(7000)


def test_turning_the_stop_limit_off_resolves_its_alarm():
    e, _ = make(stop_mm_hg=65)
    e.on_breath(70, 0)
    assert e.is_active("co2Stop")
    e.set_settings(dataclasses.replace(e.settings, stop_mm_hg=None), 1000)
    assert e.get("co2Stop").state == "resolved"


# ---------- technical alarms and ordering


def test_latches_technical_alarms_but_not_advisories():
    e, _ = make()
    e.set_condition("noData", True, 0, "medium", "NO DATA")
    e.set_condition("audioOff", True, 0, "advisory", "audio off")
    e.set_condition("noData", False, 1000, "medium", "")
    e.set_condition("audioOff", False, 1000, "advisory", "")
    assert e.get("noData").state == "resolved"
    assert e.get("audioOff") is None


def test_orders_active_alarms_then_latched_ones_then_advisories():
    e, _ = make(delay_breaths=1)
    e.set_condition("dropped", True, 0, "advisory", "x")
    e.set_condition("noData", True, 1, "medium", "x")
    e.set_condition("railCo2", True, 2, "medium", "x")
    e.set_condition("railCo2", False, 3, "medium", "x")
    e.on_breath(60, 4)
    # the latched railCo2 still needs acknowledging, so it outranks the advisory
    assert [a.id for a in e.list()] == ["co2High", "noData", "railCo2", "dropped"]
    assert e.active_priority() == "high"


# ---------- end to end


def test_co2_ramp_alarms_within_a_breath_or_two_of_crossing_the_limit():
    core = SimulatorCore(DEFAULT_ENCODERS, scenario="co2Ramp", ramp_mm_hg_per_min=15, etco2=38, seed=5)
    pipe = Pipeline(clone_channels(DEFAULT_CHANNELS), DEFAULT_PROCESSING)
    e, _ = make(high_mm_hg=50, delay_breaths=2)
    ets = []
    alarm_at = -1
    for _ in range(150 * 100):
        if alarm_at >= 0:
            break
        for line in core.next_sample():
            d = parse_line(line)
            if d.kind != "data":
                continue
            p = pipe.process(d.t_ms, d.volts, d.seq)
            if p is not None and p.breath:
                ets.append(p.breath.etco2)
                e.on_breath(p.breath.etco2, d.t_ms)
                if e.is_active("co2High"):
                    alarm_at = len(ets) - 1
    assert alarm_at > 0
    # the two breaths that triggered it were above the limit, the ones before were not
    assert ets[alarm_at] > 50
    assert ets[alarm_at - 1] > 50
    assert all(x <= 50.5 for x in ets[: alarm_at - 1])
    assert ets[alarm_at] < 54
