"""NI-DAQmx and LabJack drivers, tested against stand-ins for their libraries.

These check our side of the code (channel setup, oversampling, overflow handling,
cleanup). They cannot prove the real libraries behave the same; run
`co2r-daq check --driver nidaq` (or labjack) on the hardware for that."""
import sys
import time
import types

import pytest

from co2r.drivers.base import DaqError, DaqOverrun
from co2r.drivers.simulated import SimulatedDriver


# ------------------------------------------------------------------ fake nidaqmx
class FakeNiError(Exception):
    def __init__(self, msg, error_code):
        super().__init__(msg)
        self.error_code = error_code


def install_fake_nidaqmx(monkeypatch, read_script):
    calls = {"channels": [], "timing": None, "started": False, "closed": False}

    class Task:
        def __init__(self):
            self.ai_channels = types.SimpleNamespace(add_ai_voltage_chan=lambda name, terminal_config, min_val, max_val: calls["channels"].append((name, terminal_config, min_val, max_val)))
            self.timing = types.SimpleNamespace(cfg_samp_clk_timing=lambda rate, sample_mode, samps_per_chan: calls.__setitem__("timing", (rate, sample_mode, samps_per_chan)))

        def start(self):
            calls["started"] = True

        def read(self, number_of_samples_per_channel, timeout):
            calls.setdefault("reads", []).append(number_of_samples_per_channel)
            ev = read_script.pop(0)
            if isinstance(ev, Exception):
                raise ev
            return ev

        def stop(self):
            pass

        def close(self):
            calls["closed"] = True

    constants = types.SimpleNamespace(
        TerminalConfiguration=types.SimpleNamespace(RSE="RSE", NRSE="NRSE", DIFF="DIFF", PSEUDO_DIFF="PSEUDO_DIFF", DEFAULT="DEFAULT"),
        AcquisitionType=types.SimpleNamespace(CONTINUOUS="CONTINUOUS"),
    )
    mod = types.ModuleType("nidaqmx")
    mod.Task = Task
    mod.constants = constants
    monkeypatch.setitem(sys.modules, "nidaqmx", mod)
    monkeypatch.setitem(sys.modules, "nidaqmx.constants", constants)
    return calls


def test_nidaq_configures_channels_and_averages(monkeypatch):
    from co2r.drivers.nidaq import NiDaqmxDriver

    # two channels per read call: [[ch0...], [ch1...], [ch2...]] with 5 hardware samples each
    script = [
        [[1, 1, 1, 1, 3], [2, 2, 2, 2, 2], [0, 0, 0, 0, 4]],
        [[3, 5, 5, 5, 5], [2, 2, 2, 2, 2], [4, 8, 8, 8, 8]],
    ]
    calls = install_fake_nidaqmx(monkeypatch, script)
    d = NiDaqmxDriver(device="Dev2", channels=["ai0", "ai1", "ai2"], min_v=0, max_v=5, terminal="RSE", oversample=2)
    info = d.open(100)
    assert info.name == "NI Dev2" and info.channels == 3 and info.rate_hz == 100 and info.full_scale_v == 5
    assert calls["channels"] == [("Dev2/ai0", "RSE", 0, 5), ("Dev2/ai1", "RSE", 0, 5), ("Dev2/ai2", "RSE", 0, 5)]
    assert calls["timing"] == (200.0, "CONTINUOUS", 2000)  # 2x oversample, 10 s buffer
    assert calls["started"]
    first = d.read()
    assert first == [[1, 2, 0], [1, 2, 0]]  # 4 of 5 samples used, 1 carried over
    second = d.read()
    # carried sample (3,2,4) pairs with the first new one (3,2,4); then (5,2,8) twice
    assert second == [[3, 2, 4], [5, 2, 8], [5, 2, 8]]
    d.close()
    assert calls["closed"]


def test_nidaq_overflow_becomes_overrun_and_timeout_is_empty(monkeypatch):
    from co2r.drivers.nidaq import NiDaqmxDriver

    install_fake_nidaqmx(monkeypatch, [FakeNiError("samples no longer available", -200279), FakeNiError("Some or all of the samples requested have not yet been acquired (timeout)", -200284), FakeNiError("device removed", -50405)])
    d = NiDaqmxDriver(oversample=1)
    d.open(100)
    with pytest.raises(DaqOverrun):
        d.read()
    assert d.read() == []
    with pytest.raises(DaqError):
        d.read()


def test_nidaq_missing_library_is_a_clear_error(monkeypatch):
    from co2r.drivers.nidaq import NiDaqmxDriver

    monkeypatch.setitem(sys.modules, "nidaqmx", None)  # import fails
    ok, msg = NiDaqmxDriver.available()
    assert not ok and "nidaq" in msg
    with pytest.raises(DaqError):
        NiDaqmxDriver().open(100)


# ------------------------------------------------------------------ fake labjack ljm
def install_fake_ljm(monkeypatch, reads):
    calls = {"writes": {}, "stream": None, "stopped": False, "closed": False}
    ljm = types.ModuleType("labjack.ljm")
    ljm.openS = lambda t, c, i: 7
    ljm.getHandleInfo = lambda h: (7, 1, 470012345, 0, 0, 0)
    ljm.eWriteName = lambda h, name, v: calls["writes"].__setitem__(name, v)
    ljm.namesToAddresses = lambda n, names: ([int(x[3:]) * 2 for x in names], [3] * n)
    ljm.eStreamStart = lambda h, spr, n, addrs, rate: (calls.__setitem__("stream", (spr, n, list(addrs), rate)) or rate)
    ljm.eStreamRead = lambda h: (reads.pop(0), 0, 0)
    ljm.eStreamStop = lambda h: calls.__setitem__("stopped", True)
    ljm.close = lambda h: calls.__setitem__("closed", True)
    pkg = types.ModuleType("labjack")
    pkg.ljm = ljm
    monkeypatch.setitem(sys.modules, "labjack", pkg)
    monkeypatch.setitem(sys.modules, "labjack.ljm", ljm)
    return calls


def test_labjack_streams_and_deinterleaves(monkeypatch):
    from co2r.drivers.labjack import LabJackDriver

    calls = install_fake_ljm(monkeypatch, [[1, 2, 3, 3, 4, 5], [5, 6, 7, 7, 8, 9], [1, -9999.0, 1, 1, 1, 1]])
    d = LabJackDriver(channels=["AIN0", "AIN1", "AIN2"], range_v=10, oversample=2)
    info = d.open(100)
    assert info.name == "LabJack T7" and info.rate_hz == 100 and info.full_scale_v == 10
    spr, n, addrs, rate = calls["stream"]
    assert (n, addrs, rate) == (3, [0, 2, 4], 200.0)
    assert spr % 2 == 0
    assert calls["writes"]["AIN0_RANGE"] == 10 and calls["writes"]["AIN2_NEGATIVE_CH"] == 199
    assert d.read() == [[2, 3, 4]]  # (1,2,3) and (3,4,5) averaged
    assert d.read() == [[6, 7, 8]]
    with pytest.raises(DaqOverrun):
        d.read()  # -9999 marks skipped scans
    d.close()
    assert calls["stopped"] and calls["closed"]


# ------------------------------------------------------------------ simulator
def test_simulated_breathes_at_15_per_minute_within_0_to_5_volts():
    d = SimulatedDriver(seed=1)
    d.open(100)
    samples = [d.sample(i) for i in range(6000)]  # 60 s
    flows = [s[1] for s in samples]
    rises = sum(1 for a, b in zip(flows, flows[1:]) if a <= 2.6 < b)
    assert 14 <= rises <= 16
    assert all(0 <= v <= 5 for s in samples for v in s)
    co2_pct_peak = max(s[0] for s in samples) * 2
    assert 4.8 < co2_pct_peak < 5.4  # about 38 mmHg at 760 mmHg


def test_simulated_read_is_paced_in_real_time():
    d = SimulatedDriver(seed=1)
    d.open(200)
    t0 = time.monotonic()
    n = 0
    while time.monotonic() - t0 < 0.5:
        n += len(d.read(timeout=0.1))
    assert 80 <= n <= 120
    d.close()
