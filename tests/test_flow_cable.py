"""The SFM3300 through Sensirion's evaluation cable: the Nicolay frame protocol, the
stream decoder, a simulated cable end to end, and merging its flow into the board's lines."""

from __future__ import annotations

import math
import threading
import time

import pytest

from co2response.protocol import DataLine, InfoLine, format_data_line, parse_line
from co2response.settings import FlowCableSettings, sanitize_flow_cable
from co2response.sources.base import StatusEvent
from co2response.sources.flow_cable import (
    NOT_ANSWERING,
    CableInfo,
    FlowCableSource,
    FlowMerge,
    FlowSample,
    FrameError,
    StreamDecoder,
    bench_check,
    crc8,
    flow_slm,
    parse_answer,
    product_name,
    request,
)

# ---------- the frame protocol


def test_crc_matches_the_protocol_documents_examples():
    # Nicolay SP_290322000_cp, function 5: request 01 05 00 31, answer 01 05 02 55 AA 7D
    assert crc8(bytes([0x01, 0x05, 0x00])) == 0x31
    assert crc8(bytes([0x01, 0x05, 0x02, 0x55, 0xAA])) == 0x7D
    assert request(5) == bytes([0x01, 0x05, 0x00, 0x31])


def frame(function: int, data: bytes, address: int = 1) -> bytes:
    body = bytes([address, function, len(data)]) + data
    return body + bytes([crc8(body)])


def test_answers_are_checked():
    assert parse_answer(bytes([0x01, 0x05, 0x02, 0x55, 0xAA, 0x7D]), 5) == b"\x55\xaa"
    with pytest.raises(FrameError, match="checksum"):
        parse_answer(bytes([0x01, 0x05, 0x02, 0x55, 0xAA, 0x7E]), 5)
    with pytest.raises(FrameError, match="busy"):
        parse_answer(frame(0x85, b"\x04"), 5)
    with pytest.raises(FrameError, match="length"):
        parse_answer(bytes([0x01, 0x05, 0x03, 0x55]), 5)


def test_flow_product_and_scaling():
    assert flow_slm((12345).to_bytes(4, "little", signed=True)) == pytest.approx(12.345)
    assert flow_slm((-250000).to_bytes(4, "little", signed=True)) == pytest.approx(-250.0)
    assert math.isnan(flow_slm((0x7FFFFFFF).to_bytes(4, "little")))  # sensor not readable
    assert product_name(((0x18ABD << 8) | 0x01).to_bytes(4, "little")) == "SFM3300-D"
    assert product_name(((0x18CEB << 8)).to_bytes(4, "little")) == "SFM3400-D"


def packet(slm: float, pressure: bool = True) -> bytes:
    p = int(round(slm * 1000)).to_bytes(4, "little", signed=True)
    if pressure:
        p += (8192).to_bytes(2, "little")
    return p + b"\xff\x03"


@pytest.mark.parametrize("pressure", [True, False])
def test_stream_decoder_finds_the_packets_and_resyncs(pressure):
    d = StreamDecoder()
    flows = [0.0, 1.5, -2.25, 30.0, -45.5, 0.125]
    data = b"\x37\x99" + b"".join(packet(f, pressure) for f in flows)  # junk first
    out: list[float] = []
    for i in range(0, len(data), 5):  # in small pieces, as a serial port delivers them
        out += d.push(data[i : i + 5])
    assert d.length == (8 if pressure else 6)
    # the first packets are used to find the length; every one after comes out
    assert out == pytest.approx(flows[len(flows) - len(out) :]) and len(out) >= 3
    # a dropped byte: it finds its way back
    more = packet(1.0, pressure)[1:] + b"".join(packet(f, pressure) for f in (2.0, 3.0, 4.0, 5.0))
    out2 = d.push(more)
    assert out2[-1] == pytest.approx(5.0) and d.resyncs >= 1


# ---------- a simulated cable


class FakeCable:
    """The evaluation cable at the other end of a serial port."""

    def __init__(self, stream: bool = True, flow=lambda t: 20 * math.sin(2 * math.pi * t / 4)) -> None:
        self.stream_ok = stream
        self.flow = flow
        self.out = bytearray()
        self.streaming = False
        self.t0 = time.monotonic()
        self.next_packet = self.t0
        self.lock = threading.Lock()
        self.closed = False
        self.asked: list[int] = []

    # serial.Serial's interface, as used
    def write(self, data: bytes) -> int:
        with self.lock:
            if self.streaming:
                self.streaming = False  # any byte stops the stream
                return len(data)
            if len(data) >= 4 and crc8(data[:-1]) == data[-1]:
                fn = data[1]
                self.asked.append(fn)
                if fn == 5:
                    self.out += frame(5, b"\x55\xaa")
                elif fn == 10:
                    self.out += frame(10, ((0x18ABD << 8) | 0x01).to_bytes(4, "little"))
                elif fn == 15:
                    self.out += frame(15, (123456).to_bytes(4, "little"))
                elif fn == 16:
                    f = int(self.flow(time.monotonic() - self.t0) * 1000)
                    self.out += frame(16, f.to_bytes(4, "little", signed=True))
                elif fn == 30 and self.stream_ok:
                    self.streaming = True
                    self.next_packet = time.monotonic()
                elif fn != 30:
                    self.out += frame(fn | 0x80, b"\x01")
        return len(data)

    def _produce(self) -> None:
        now = time.monotonic()
        while self.streaming and self.next_packet <= now:
            self.out += packet(self.flow(self.next_packet - self.t0))
            self.next_packet += 0.002

    @property
    def in_waiting(self) -> int:
        with self.lock:
            self._produce()
            return len(self.out)

    def read(self, n: int = 1) -> bytes:
        end = time.monotonic() + 0.02
        while time.monotonic() < end:
            with self.lock:
                self._produce()
                if self.out:
                    b = bytes(self.out[:n])
                    del self.out[:n]
                    return b
            time.sleep(0.001)
        return b""

    def reset_input_buffer(self) -> None:
        with self.lock:
            self.out.clear()

    def cancel_read(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def collect(src, seconds: float) -> list:
    items: list = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        items += src.poll()
        time.sleep(0.01)
    return items


@pytest.mark.parametrize("stream", [True, False])
def test_the_cable_end_to_end(stream):
    cable = FakeCable(stream=stream)
    src = FlowCableSource("FAKE", opener=lambda _p: cable)
    src.start()
    try:
        items = collect(src, 1.6 if not stream else 0.4)
    finally:
        src.stop()
    infos = [i for i in items if isinstance(i, CableInfo)]
    samples = [i for i in items if isinstance(i, FlowSample)]
    assert infos and infos[0].sensor == "SFM3300-D" and infos[0].serial == "123456"
    assert infos[0].mode == ("stream" if stream else "poll")
    assert len(samples) > 20
    assert all(-20.5 <= s.slm <= 20.5 for s in samples)
    assert all(b.t >= a.t for a, b in zip(samples, samples[1:]))
    assert cable.closed


def test_a_silent_port_is_a_readable_error():
    class Silent(FakeCable):
        def write(self, data):
            return len(data)

    src = FlowCableSource("COM9", opener=lambda _p: Silent())
    with pytest.raises(RuntimeError, match="does not answer"):
        src.start()


def test_bench_check_prints_the_flow():
    lines: list[str] = []
    assert bench_check("FAKE", 1.2, log=lines.append, opener=lambda _p: FakeCable()) == 0
    assert any("SFM3300-D" in s for s in lines) and any("L/min" in s for s in lines)


# ---------- merging into the board's lines


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_merge_puts_the_mean_flow_of_each_interval_into_the_lines():
    clk = Clock()
    m = FlowMerge(clk, sign=1)
    out: list = []
    out += m.board_items(["I,FW,2.2.0,BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR-R,FLOW,CABLE,CH,2"])
    info = parse_line(out[0])
    assert isinstance(info, InfoLine) and info.fields["FLOW"] == "SFM_CABLE"
    for k in range(50):  # 0.5 s of 100 Hz lines and 500 Hz cable samples
        clk.t = 100.0 + k * 0.01
        m.cable_items([FlowSample(clk.t - 0.01 + j * 0.002, 10.0 + k) for j in range(1, 6)])
        out += m.board_items([format_data_line(k, 5000 + k * 10, [4.5, NOT_ANSWERING])])
    lines = [p for p in map(parse_line, out[1:]) if isinstance(p, DataLine)]
    assert len(lines) >= 45
    # each line carries its own interval's flow, and the CO2 is untouched
    for p in lines[1:]:
        assert p.volts[0] == pytest.approx(4.5)
        assert p.volts[1] == pytest.approx(10.0 + p.seq, abs=0.01)


def test_merge_sign_and_not_answering():
    clk = Clock()
    m = FlowMerge(clk, sign=-1)
    m.cable_items([FlowSample(clk.t, 12.0)])
    clk.t += 0.01
    # the line waits for a cable sample from its own moment, then goes on
    assert m.board_items([format_data_line(1, 1000, [0.04, 0.0])]) == []
    m.cable_items([FlowSample(clk.t + 0.001, 12.0)])
    (line,) = m.release()
    assert parse_line(line).volts[1] == pytest.approx(-12.0)  # type: ignore[union-attr]
    # no flow from the cable: the line still goes on, after HOLD_MAX_S, as "not answering"
    clk.t += 2.0
    assert m.board_items([format_data_line(2, 3000, [0.04, 0.0])]) == []
    clk.t += 0.5
    (late,) = m.release()
    assert parse_line(late).volts[1] == NOT_ANSWERING  # type: ignore[union-attr]
    # a disconnect is reported
    assert m.cable_items([StatusEvent("error", "gone")]) == [StatusEvent("error", "gone")]


def test_settings_are_kept_sane():
    s = sanitize_flow_cable(FlowCableSettings(enabled=True, port="  COM5 ", sign=7))
    assert s.port == "COM5" and s.sign == 1
    assert sanitize_flow_cable(FlowCableSettings(sign=-1)).sign == -1


# ---------- end to end through the monitor engine


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="needs a pseudo-terminal")
def test_the_engine_takes_the_flow_from_the_cable(tmp_path, monkeypatch):
    import os
    import pty
    import tty

    from test_engine import run_real

    import co2response.engine.monitor as monitor_mod
    from co2response.alarms.audio import SilentAudio
    from co2response.engine.monitor import MonitorEngine
    from co2response.settings import SettingsStore
    from co2response.sources.serial_source import PortInfo

    master, slave = pty.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    device = os.ttyname(slave)
    alive = True
    INFO_R4 = b"I,FW,2.2.0,BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR-R,FLOW,CABLE,CH,2,RATE,100\n"

    def board() -> None:
        """The Uno R4 board without a flow sensor: CO2 and a flow of 9999."""
        os.write(master, INFO_R4)
        t0 = time.monotonic()
        seq = 0
        while alive:
            due = int((time.monotonic() - t0) * 100)
            while seq < due:
                try:
                    os.write(master, (format_data_line(seq, seq * 10, [5.0, NOT_ANSWERING]) + "\n").encode())
                except OSError:
                    return
                seq += 1
            try:
                if b"INFO" in os.read(master, 1024):  # the app's commands
                    os.write(master, INFO_R4)
            except OSError:
                pass
            time.sleep(0.005)

    th = threading.Thread(target=board, daemon=True)
    th.start()
    cable = FakeCable(flow=lambda t: 15.0)
    monkeypatch.setattr(monitor_mod, "FlowCableSource", lambda port: FlowCableSource(port, opener=lambda _p: cable))
    store = SettingsStore(tmp_path / "cfg")
    s = store.load_settings()
    s.hardware = "digital"
    s.flow_cable = FlowCableSettings(enabled=True, port="FAKE", sign=-1)
    store.save_settings(s)
    e = MonitorEngine(store=store, audio=SilentAudio(), port_lister=lambda: [PortInfo(device, "Arduino UNO R4", 0x2341, 0x0069, "R4")], sessions_dir=tmp_path / "s", prevent_sleep=False)
    try:
        assert e.connect("serial", port=device), e.snapshot().status_detail
        assert run_real(e, 5, lambda: math.isfinite(e.snapshot().numbers.flow))
        snap = e.snapshot()
        assert snap.numbers.flow == pytest.approx(-15.0, abs=0.01)  # sign -1 applied
        assert snap.info.get("FLOW") == "SFM_CABLE"
        assert any("SFM3300-D" in x.text for x in e.log)
    finally:
        alive = False
        e.disconnect()
        th.join(1)
        os.close(master)
        os.close(slave)
