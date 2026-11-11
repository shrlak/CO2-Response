"""A Capnostream 35 as the CO2 source: decoding its ASCII lines, lining its CO2 up with
the board's flow, the simulated monitor end to end, the capture tool and the options."""

from __future__ import annotations

import dataclasses
import io
import math
import time

import pytest
from test_engine import make_engine, run

from co2response.processing.co2 import pct_to_mmhg
from co2response.protocol import format_data_line, parse_line
from co2response.settings import CapnoSettings, SettingsStore, sanitize_capno
from co2response.sources.capnostream import (
    NOT_ANSWERING,
    CapnoDecoder,
    CapnoMerge,
    CapnostreamSource,
    capture,
    mmhg_to_line_pct,
    split_lines,
    to_mmhg,
)

# ---------- decoding


def test_auto_decoder_finds_key_value_pairs_and_bare_numbers():
    d = CapnoDecoder()
    assert d.decode("CO2=37.5").co2 == [37.5]
    assert d.decode("37.5").co2 == [37.5]
    n = d.decode("ETCO2=38 FiCO2=0 RR=15 SpO2=98 PR=72 IPI=10").numbers
    assert n == {"etco2": 38, "fico2": 0, "rr": 15, "spo2": 98, "pr": 72, "ipi": 10}
    assert d.decode("EtCO2: 41, RR: 12").numbers == {"etco2": 41, "rr": 12}
    assert d.decode("ETCO2 39 RR 14").numbers == {"etco2": 39, "rr": 14}
    before = d.unknown
    d.decode("Capnostream 35 V1.07 ready")
    assert d.unknown == before + 1


def test_positional_decoder_and_units():
    d = CapnoDecoder("time,co2,etco2,rr,spo2,pr")
    out = d.decode("12:00:01, 37.5, 38, 15, 98, 72")
    assert out.co2 == [37.5] and out.numbers == {"etco2": 38, "rr": 15, "spo2": 98, "pr": 72}
    # a line of waveform samples after a header field
    many = CapnoDecoder("skip,co2*").decode("W\t1.0\t2.0\t3.0\t4.0")
    assert many.co2 == [1.0, 2.0, 3.0, 4.0]
    # CO2 in % or kPa comes out in mmHg
    assert CapnoDecoder(units="%", barometric_mm_hg=760).decode("CO2=5").co2 == [pytest.approx(38.0)]
    assert CapnoDecoder(units="kPa").decode("ETCO2=5.0").numbers["etco2"] == pytest.approx(37.5, abs=0.05)
    assert to_mmhg(40, "mmHg") == 40


def test_split_lines_on_any_line_ending():
    lines, rest = split_lines("a\r\nb\rc\nd")
    assert lines == ["a", "b", "c"] and rest == "d"


# ---------- lining the two streams up


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def board_line(seq: int, t_ms: float, flow: float = 10.0) -> str:
    return format_data_line(seq, t_ms, [NOT_ANSWERING, flow])


def test_merge_puts_the_monitors_co2_into_the_boards_lines():
    clk = Clock()
    m = CapnoMerge(CapnoDecoder(), clk, mmhg_to_line_pct(760, False))
    info = m.board_items(["I,FW,2.0.0,BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR,FLOW,SFM3304,CH,2,RATE,100"])
    assert info == ["I,FW,2.0.0,BOARD,UNO_R4,UNITS,1,CO2,CAPNOSTREAM35,FLOW,SFM3304,CH,2,RATE,100"]
    out: list = []
    # one second: the board sends 100 lines, the monitor 20 samples rising 0 → 38 mmHg
    for i in range(100):
        clk.t = 100.0 + i / 100
        if i % 5 == 0:
            m.capno_line(f"CO2={38 * i / 100:.2f}")
        out += m.board_items([board_line(i, 5000 + i * 10)])
    clk.t += 0.1
    m.capno_line("CO2=38")
    out += m.board_items([])
    data = [parse_line(x) for x in out]
    assert len(data) == 100 and all(d.kind == "data" for d in data)
    mm = [pct_to_mmhg(d.volts[0], 760) for d in data]
    assert mm[0] == pytest.approx(0, abs=0.5) and mm[50] == pytest.approx(19, abs=1.0) and mm[-1] == pytest.approx(37.6, abs=1.0)
    assert all(b >= a - 1e-6 for a, b in zip(mm, mm[1:]))  # in order, rising with the gas
    assert [d.volts[1] for d in data] == [10.0] * 100  # the flow is untouched


def test_merge_sends_not_answering_when_the_monitor_is_quiet_and_waits_only_so_long():
    clk = Clock()
    m = CapnoMerge(CapnoDecoder(), clk, mmhg_to_line_pct(760, False))
    m.capno_line("CO2=38")
    clk.t += 0.05
    assert m.board_items([board_line(1, 1000)]) == []  # waits for the CO2 of its moment
    clk.t += 0.7
    out = m.board_items([])
    assert len(out) == 1 and parse_line(out[0]).volts[0] == pytest.approx(5.0, abs=0.01)  # the last CO2, held
    clk.t += 2.0
    m.board_items([board_line(2, 3700)])
    clk.t += 0.7
    out = m.board_items([])
    assert parse_line(out[0]).volts[0] == NOT_ANSWERING
    assert m.fresh_numbers == {}


def test_merge_leaves_a_board_that_sends_volts_alone():
    clk = Clock()
    m = CapnoMerge(CapnoDecoder(), clk, mmhg_to_line_pct(760, False))
    m.board_items(["I,FW,1.2.0,ADC,ADS1115,CH,3,RATE,100"])
    line = format_data_line(1, 10, [2.6, 2.5, 2.5])
    assert m.board_items([line]) == [line] and not m.board_ok


# ---------- the simulator with a simulated Capnostream


def capno_engine(tmp_path, **kw):
    e = make_engine(tmp_path)
    e.update_settings(hardware="digital", capno=CapnoSettings(enabled=True, port="sim", **kw))
    return e


def test_simulated_capnostream_end_to_end(tmp_path):
    e = capno_engine(tmp_path)
    e.connect("simulator")
    run(e, 12)
    early = [a for a in e.alarms.list() if a.id == "railCo2"]
    assert not early  # no "not answering" while its first CO2 is still in the sample line
    run(e, 30)
    s = e.snapshot()
    assert s.info["CO2"] == "CAPNOSTREAM35"
    n = s.numbers
    assert n.etco2 == pytest.approx(38, abs=1.5) and n.rr == pytest.approx(15, abs=0.6)
    assert n.vt == pytest.approx(500, rel=0.05) and n.mv == pytest.approx(7.5, rel=0.08)
    # its 2.7 s sample line is taken off: the CO2 rises just after expiration starts
    assert e.pipeline.co2_delay_sec == pytest.approx(2.7)
    assert 0 < n.co2_lag_sec < 0.6
    assert s.capno == {"etco2": 38, "rr": 15, "spo2": 98, "pr": 72}
    assert not [a for a in s.alarms if a.state == "active" and a.priority != "advisory"]
    # the monitor's data stops: CO2 reads --- and the alarm names the Capnostream
    e.set_sim_switch("co2_disconnected", True)
    run(e, 6)
    a = e.alarms.get("railCo2")
    assert a is not None and a.message.startswith("CAPNOSTREAM NOT ANSWERING") and e.snapshot().stale["co2"]


def test_a_whole_test_with_the_capnostream_gives_the_same_response(tmp_path):
    slopes = {}
    for name, capno in (("board", False), ("capno", True)):
        e = make_engine(tmp_path / name)
        e.update_settings(hardware="digital", capno=CapnoSettings(enabled=capno, port="sim"))
        e.set_sim(speed=10)
        e.apply_situation("test_normal")
        e.set_sim(variability_pct=0)
        run(e, 30)
        res = e.co2_response()
        assert res.fit is not None, name
        slopes[name] = res.slope
    assert slopes["capno"] == pytest.approx(slopes["board"], rel=0.15)


def test_turning_it_off_restarts_the_simulator_without_it(tmp_path):
    e = capno_engine(tmp_path)
    e.connect("simulator")
    run(e, 3)
    assert e.capnostream is not None
    e.update_settings(capno=dataclasses.replace(e.settings.capno, enabled=False))
    run(e, 3)
    assert e.capnostream is None and e.snapshot().capno is None
    assert e.snapshot().info.get("CO2") == "SPRINTIR"


# ---------- reading a real port, and the capture tool


def test_capnostream_source_reads_lines_from_a_port():
    src = CapnostreamSource("loop://", 9600)
    src.start()
    try:
        src._ser.write(b"CO2=37.5\r\nETCO2=38 RR=15\r\n")
        got: list = []
        end = time.monotonic() + 2
        while len(got) < 2 and time.monotonic() < end:
            got += src.poll()
            time.sleep(0.02)
        assert got == ["CO2=37.5", "ETCO2=38 RR=15"]
    finally:
        src.stop()


class FakePort:
    """A serial port that gives text at one baud rate and noise at the others."""

    def __init__(self, baud: int, good: int = 19200) -> None:
        self.good = baud == good
        self.in_waiting = 0

    def read(self, n: int) -> bytes:
        time.sleep(0.01)
        return b"CO2=37.5\r\n" if self.good else bytes([0xFF, 0x13, 0xA7])

    def close(self) -> None:
        pass


def test_capture_finds_the_baud_rate_and_what_the_monitor_sends(monkeypatch):
    import co2response.sources.capnostream as cs

    clock = [0.0]

    def fake_monotonic() -> float:
        clock[0] += 0.05
        return clock[0]

    monkeypatch.setattr(cs.time, "monotonic", fake_monotonic)
    buf = io.StringIO()
    r = capture("COM9", seconds=2, out=buf, opener=FakePort, log=lambda *a: None)
    assert r["baud"] == 19200 and r["co2_samples"] > 10 and r["unknown_lines"] == 0
    assert buf.getvalue().startswith("# Capnostream capture: COM9, 19200 baud") and "CO2=37.5" in buf.getvalue()


# ---------- the options


def test_command_line_saves_the_capnostream_options(tmp_path, monkeypatch, capsys):
    from co2response.__main__ import main

    monkeypatch.setenv("CO2R_HOME", str(tmp_path))
    assert main(["--capnostream", "COM4", "--capno-baud", "19200", "--capno-fields", "time,co2,etco2", "--capno-units", "%", "--capno-delay", "3000", "--list-situations"]) == 0
    c = SettingsStore(tmp_path).load_settings().capno
    assert (c.enabled, c.port, c.baud, c.fields, c.units, c.delay_ms) == (True, "COM4", 19200, "time,co2,etco2", "%", 3000)
    assert "Capnostream as the CO2 source: on: COM4" in capsys.readouterr().out
    main(["--capnostream", "off", "--capno-fields", "auto", "--list-situations"])
    c = SettingsStore(tmp_path).load_settings().capno
    assert not c.enabled and c.port == "COM4" and c.fields == ""


def test_settings_are_kept_in_range():
    c = sanitize_capno(CapnoSettings(baud=5, units="ppm", delay_ms=-3, port="  COM4 "))
    assert c.baud == 300 and c.units == "mmHg" and c.delay_ms == 0 and c.port == "COM4"  # clamped to the nearest limit
    assert math.isfinite(c.delay_ms)
