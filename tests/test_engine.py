"""The monitor engine end to end: sources → processing → alarms → recording.

Most tests run on a fake clock so minutes of data take a fraction of a second. The
serial test uses a real pseudo-terminal as the "Arduino", so pyserial, the reader
thread, unplugging and auto-reconnect are exercised for real.
"""

import dataclasses
import math
import os
import select
import sys
import threading
import time

import pytest
from conftest import DEFAULT_ENCODERS

from co2response.alarms.audio import SilentAudio
from co2response.engine.monitor import MonitorEngine
from co2response.settings import SettingsStore
from co2response.sim.core import SimulatorCore
from co2response.sources.serial_source import PortInfo

T0 = 1_793_854_800_000  # 2026-11-05T05:00:00Z


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_engine(tmp_path, clock=None, ports=None, **kw):
    return MonitorEngine(
        store=SettingsStore(tmp_path / "cfg"),
        audio=SilentAudio(),
        clock=clock or Clock(),
        port_lister=(lambda: list(ports)) if ports is not None else (lambda: []),
        sessions_dir=tmp_path / "sessions",
        prevent_sleep=False,
        **kw,
    )


def run(engine: MonitorEngine, seconds: float, step_ms: int = 20) -> None:
    """Advance the fake clock, polling every 20 ms and ticking 4 times a second."""
    clock = engine.clock
    end = clock.t + seconds * 1000
    next_tick = clock.t + 250
    while clock.t < end:
        clock.t += step_ms
        engine.poll()
        if clock.t >= next_tick:
            engine.tick()
            next_tick += 250


def ids(snap):
    return [a.id for a in snap.alarms]


def test_starts_disconnected(tmp_path):
    e = make_engine(tmp_path)
    s = e.snapshot()
    assert s.status == "idle"
    assert math.isnan(s.numbers.etco2) and s.stale["co2"]


def test_simulator_drives_the_numbers(tmp_path):
    e = make_engine(tmp_path)
    assert e.connect("simulator")
    run(e, 30)
    s = e.snapshot()
    assert s.status == "connected" and s.source_label == "Simulator"
    assert 14 <= round(s.numbers.rr) <= 16
    assert 36 <= round(s.numbers.etco2) <= 39
    assert 460 <= round(s.numbers.vt) <= 540
    assert 95 < s.measured_rate_hz < 105
    assert len(e.trend) >= 6
    # waveform values are queued for the display, in the chosen units
    w = e.take_waves()
    assert len(w["co2"]) > 1000 and max(v for v in w["co2"] if v == v) > 30


def test_test_alarm_sounds_high_then_medium_then_clears(tmp_path):
    e = make_engine(tmp_path)
    e.test_alarm()
    s = e.snapshot()
    assert s.alarms[0].id == "test" and s.alarms[0].priority == "high"
    assert e.audio.played == ["high"]
    run(e, 4.1)
    assert "medium" in e.audio.played
    run(e, 4)
    assert "test" not in ids(e.snapshot())


def test_co2_ramp_raises_the_high_alarm_and_acknowledge_silences_it(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.set_sim(scenario="co2Ramp")
    for _ in range(120):
        run(e, 1)
        if e.alarms.is_active("co2High"):
            break
    a = e.alarms.get("co2High")
    assert a is not None and a.state == "active" and a.message.startswith("EtCO2 HIGH")
    assert "high" in e.audio.played
    assert e.alarm_starts and e.alarm_starts[-1][1] == "high"
    e.acknowledge()
    s = e.snapshot()
    assert s.silence_ms > 0 and e.alarms.get("co2High").acknowledged


def test_rebreathing_marker_sets_the_baseline_apart(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 30)
    assert e.rebreathing_start() is None
    whole = e.co2_response()
    assert not whole.split and len(whole.rebreathing) >= 6
    e.add_marker("Event")
    assert e.rebreathing_start() is None
    m = e.add_marker()
    e.rename_marker(m.id, "Rebreathing")
    assert e.rebreathing_start() == m.t / 1000
    e.set_sim(scenario="co2Ramp")
    run(e, 40)
    res = e.co2_response()
    assert res.split and len(res.baseline) >= 6 and len(res.rebreathing) >= 6
    assert 36 <= res.baseline_etco2 <= 39
    assert max(a for a, _ in res.rebreathing) > res.baseline_etco2


def test_apnea_and_no_data_alarms(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 10)
    e.set_sim(scenario="apnea")
    run(e, 26)
    assert e.alarms.is_active("apnea") and e.alarms.get("apnea").priority == "high"
    e.set_sim(scenario="noData")
    run(e, 4)
    assert e.alarms.is_active("noData")
    # an intentional disconnect is not a technical fault
    e.disconnect()
    assert not e.alarms.is_active("noData") and e.snapshot().status == "idle"


def test_unplugged_co2_sensor_raises_a_rail_alarm(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.set_sim(scenario="co2Disconnect")
    run(e, 4)
    assert e.alarms.is_active("railCo2")
    assert e.snapshot().stale["co2"]


def test_record_stop_import_review_export(tmp_path):
    from co2response.storage.session_file import breaths_csv

    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 5)
    rec = e.start_recording()
    assert rec is not None and e.snapshot().recording.active
    run(e, 10)
    m = e.add_marker()
    e.rename_marker(m.id, "drug given")
    run(e, 20)
    res = e.stop_recording()
    assert res.path is not None and res.path.exists() and res.path.parent == tmp_path / "sessions"
    assert res.file_name.startswith("co2r_STUDY-01_") and res.file_name.endswith(".csv")
    text = res.path.read_text()
    assert text.startswith("# CO2-Response session,v3")
    assert "MARK Event 1" in text and "MARK_RENAMED 1 drug given" in text and "RECORDING_STOPPED" in text
    lib = e.library
    assert [(r.status, r.stats["breaths"] > 3) for r in lib.list()] == [("complete", True)]

    rec2, parsed = lib.import_file(res.path, "reimported.csv")
    assert rec2.status == "imported" and len(lib.list()) == 2
    assert parsed.n == 3000
    assert breaths_csv(parsed).startswith("breath,time_s,wall_time_iso,etco2_mmHg")

    # replay it through the monitor with the file's calibration
    assert e.connect("replay", session=parsed, file_name="reimported.csv", speed=10)
    assert e.snapshot().using_file_calibration
    run(e, 5)
    s = e.snapshot()
    assert s.source_kind is None and s.status == "idle" and s.status_detail == "Replay finished"
    assert len(e.breaths) >= 6


def test_settings_and_calibration_persist(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(theme="light", study_code="PIG-3", alarms=dataclasses.replace(e.settings.alarms, high_mm_hg=45, volume=0))
    c = e.channels
    c["channels"]["co2"]["calibration"]["gain"] = 2.5
    e.set_channels(c)
    e2 = make_engine(tmp_path)
    assert (e2.settings.theme, e2.settings.study_code, e2.settings.alarms.high_mm_hg) == ("light", "PIG-3", 45)
    assert e2.settings.alarms.volume == 0.1  # sanitized: never silent
    assert e2.channels["channels"]["co2"]["calibration"]["gain"] == 2.5
    e2.reset_channels()
    assert make_engine(tmp_path).channels["channels"]["co2"]["calibration"]["gain"] == 2


def test_capture_average_and_hardware_check(tmp_path):
    e = make_engine(tmp_path)
    got = []
    e.capture_average("flow", 2, lambda r, err: got.append((r, err)))
    run(e, 2.1)
    assert got[0][0] is None and "No data" in got[0][1]

    e.connect("simulator")
    run(e, 3)
    e.capture_average("co2", 2, lambda r, err: got.append((r, err)))
    job = e.run_hardware_check(5)
    run(e, 5.5)
    r, err = got[1]
    assert err is None and r.n == 200 and 0 < r.volts < 5
    assert job.done
    by = {x.id: x.status for x in job.results}
    assert by["rate"] == "pass" and by["drops"] == "pass" and by["ch-co2"] == "pass"


def test_crash_recovery_on_next_start(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.start_recording()
    run(e, 3)  # written to disk once a second; then the app "crashes"
    later = Clock(e.clock.t + 60_000)
    e2 = make_engine(tmp_path, clock=later)
    recovered = e2.init()
    assert [r.status for r in recovered] == ["interrupted"]
    assert e2.library.parse(recovered[0].id).n >= 200


# ------------------------------------------------------------------ the Arduino over serial

INFO = b"I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144\n"


class FakeArduino(threading.Thread):
    """A pseudo-terminal that behaves like the firmware: boot banner, commands, and
    data lines (``speed`` times faster than real time)."""

    def __init__(self, speed: float = 10) -> None:
        super().__init__(daemon=True)
        import pty
        import tty

        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.device = os.ttyname(self.slave)
        self.core = SimulatorCore(DEFAULT_ENCODERS, seed=7)
        self.speed = speed
        self.commands: list[str] = []
        self.alive = True
        self.start()

    def _write(self, b: bytes) -> None:
        """Send whole lines. A full pty (nobody reading) drops the line, as a UART
        overrun loses data. But a short write is finished, never left as a cut line:
        a cut line fails its checksum, and the hardware check would count it as
        corrupt. macOS's small pty buffer makes short writes common under load."""
        view = memoryview(b)
        deadline = time.monotonic() + 0.5
        while view:
            try:
                n = os.write(self.master, view)
            except BlockingIOError:
                n = 0
            except OSError:
                return
            if n == 0 and len(view) == len(b):
                return  # nothing written: the whole line is lost
            view = view[n:]
            if view:
                if time.monotonic() > deadline:
                    return  # the reader went away mid-line
                select.select([], [self.master], [], 0.01)

    def run(self) -> None:
        self._write(INFO)
        t0 = time.monotonic()
        sent = 0
        buf = b""
        while self.alive:
            r, _, _ = select.select([self.master], [], [], 0.005)
            if r:
                try:
                    buf += os.read(self.master, 1024)
                except OSError:
                    buf = b""
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    cmd = raw.decode().strip()
                    self.commands.append(cmd)
                    replies = [INFO.decode().strip()] if cmd == "INFO" else self.core.command(cmd)
                    self._write("".join(x + "\n" for x in replies).encode())
            due = int((time.monotonic() - t0) * 100 * self.speed)
            while sent < due:
                sent += 1
                self._write("".join(x + "\n" for x in self.core.next_sample()).encode())

    def unplug(self) -> None:
        self.alive = False
        self.join()
        os.close(self.master)
        os.close(self.slave)


def run_real(engine: MonitorEngine, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    next_tick = 0.0
    while time.monotonic() < end:
        engine.poll()
        if time.monotonic() >= next_tick:
            engine.tick()
            next_tick = time.monotonic() + 0.25
        if until and until():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_arduino_over_serial_connect_check_unplug_auto_reconnect(tmp_path):
    ard = FakeArduino()
    ports = [PortInfo(ard.device, "Arduino Uno", 0x2341, 0x0043, "A1")]
    e = MonitorEngine(
        store=SettingsStore(tmp_path / "cfg"), audio=SilentAudio(), port_lister=lambda: list(ports), sessions_dir=tmp_path / "s", prevent_sleep=False
    )
    try:
        assert e.find_known_port().device == ard.device  # the only Arduino plugged in
        assert e.connect("serial", port=ard.device), e.snapshot().status_detail
        assert e.snapshot().source_label == f"Arduino Uno ({ard.device})"
        assert run_real(e, 10, lambda: math.isfinite(e.snapshot().numbers.rr))
        s = e.snapshot()
        assert 14 <= round(s.numbers.rr) <= 16 and 36 <= round(s.numbers.etco2) <= 39
        assert ard.commands[:2] == ["INFO", "START"]
        assert s.info["ADC"] == "ADS1115"

        job = e.run_hardware_check(1)
        assert run_real(e, 5, lambda: job.done)
        by = {x.id: x for x in job.results}
        assert by["handshake"].status == "pass" and "ADS1115 (I2C 0x48, ±6.144 V range)" in by["handshake"].result
        assert by["corrupt"].status == "pass"

        # unplug: device-disconnected alarm, then waits for the same board
        ard.unplug()
        ports.clear()
        assert run_real(e, 5, lambda: e.alarms.is_active("deviceLost"))
        s = e.snapshot()
        assert s.status == "error" and s.awaiting_reconnect and "plugged back in" in s.status_detail

        # plug it back in: it comes back under a new device name and reconnects by itself
        ard = FakeArduino()
        ports.append(PortInfo(ard.device, "Arduino Uno", 0x2341, 0x0043, "A1"))
        assert run_real(e, 8, lambda: e.snapshot().status == "connected")
        assert e.alarms.get("deviceLost").state == "resolved"  # latched until acknowledged
        assert run_real(e, 10, lambda: len(e.breaths) >= 2)

        # one-click reconnect after a manual disconnect
        e.disconnect()
        assert e.snapshot().known_port == f"Arduino Uno ({ard.device})"
        assert e.reconnect_known_port()
        assert e.snapshot().status == "connected"
    finally:
        e.shutdown()
        ard.unplug()
