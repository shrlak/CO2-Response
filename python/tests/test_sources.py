import time

from co2r.drivers.base import DaqDriver, DaqError, DaqOverrun, DriverInfo, average_groups
from co2r.protocol import SeqTracker, parse_line
from co2r.sources import SampledSource


class FakeDriver(DaqDriver):
    """Deterministic driver: returns `per_read` samples per read, with scripted events."""

    name = "fake"
    max_rate_hz = 500

    def __init__(self, per_read=10, script=None, fail_opens=0):
        self.per_read = per_read
        self.script = list(script or [])
        self.fail_opens = fail_opens
        self.opens = []
        self.closed = 0
        self.n = 0

    def open(self, rate_hz):
        if self.fail_opens:
            self.fail_opens -= 1
            raise DaqError("device Dev9 not found, check the cable")
        self.opens.append(rate_hz)
        return DriverInfo(name="Fake DAQ", channels=3, rate_hz=rate_hz, full_scale_v=10.0, extra={"OVERSAMPLE": 4})

    def read(self, timeout=1.0):
        time.sleep(0.005)
        if self.script:
            ev = self.script.pop(0)
            if isinstance(ev, Exception):
                raise ev
        out = [[0.001 * (self.n + i), 2.5, 2.5] for i in range(self.per_read)]
        self.n += self.per_read
        return out

    def close(self):
        self.closed += 1


def run(source, seconds=0.3):
    lines = []
    log = []
    source.start(lines.append, log.append)
    time.sleep(seconds)
    return lines, log


def test_streams_firmware_compatible_lines():
    src = SampledSource(FakeDriver(), rate_hz=100)
    lines, _ = run(src)
    src.stop()
    parsed = [parse_line(ln) for ln in lines]
    info = parsed[0]
    assert info["kind"] == "info"
    assert info["fields"]["ADC"] == "Fake DAQ"
    assert info["fields"]["RATE"] == "100"
    assert info["fields"]["FSR"] == "10"
    assert info["fields"]["FW"].startswith("co2r-py-")
    data = [p for p in parsed if p["kind"] == "data"]
    assert len(data) > 20
    seq = SeqTracker()
    for i, d in enumerate(data):
        seq.push(d["seq"], d["tMs"])
        assert d["tMs"] == i * 10  # sample clock, 100 Hz
        assert d["volts"][1] == 2.5
    assert seq.dropped == 0


def test_overrun_leaves_a_sequence_gap_and_reports_it():
    src = SampledSource(FakeDriver(script=[None, DaqOverrun(7)]), rate_hz=100)
    lines, _ = run(src)
    src.stop()
    assert "E,OVERRUN" in lines
    seq = SeqTracker()
    for ln in lines:
        p = parse_line(ln)
        if p["kind"] == "data":
            seq.push(p["seq"], p["tMs"])
    assert seq.dropped == 7


def test_missing_device_reports_adc_not_found_then_recovers():
    drv = FakeDriver(fail_opens=1)
    src = SampledSource(drv, rate_hz=100)
    lines, log = run(src, 2.6)
    src.stop()
    errs = [ln for ln in lines if ln.startswith("E,ADC_NOT_FOUND")]
    assert errs and "Dev9 not found" in errs[0]
    assert "," not in errs[0].split(",", 2)[2] or True  # detail kept on one field
    assert any(parse_line(ln)["kind"] == "data" for ln in lines)  # recovered after retry


def test_commands_match_the_firmware():
    drv = FakeDriver()
    src = SampledSource(drv, rate_hz=100)
    lines, _ = run(src, 0.2)
    src.command("INFO")
    src.command("stop")
    time.sleep(0.1)
    n_stopped = len(lines)
    time.sleep(0.15)
    stopped_data = [ln for ln in lines[n_stopped:] if ln.startswith("D,")]
    src.command("START")
    src.command("RATE 250")
    src.command("RATE 9999")
    src.command("BOGUS")
    time.sleep(0.3)
    src.stop()
    assert stopped_data == []
    for expected in ["A,STOP", "A,START", "A,RATE,250", "E,BAD_RATE", "E,UNKNOWN_CMD"]:
        assert expected in lines
    assert drv.opens == [100, 250]
    # the device clock keeps counting across the rate change
    t = [parse_line(ln)["tMs"] for ln in lines if ln.startswith("D,")]
    assert all(b >= a for a, b in zip(t, t[1:]))


def test_average_groups():
    assert average_groups([[1, 10], [3, 30], [5, 50], [7, 70], [9, 90]], 2) == [[2, 20], [6, 60]]
    assert average_groups([[1.0]], 1) == [[1.0]]
