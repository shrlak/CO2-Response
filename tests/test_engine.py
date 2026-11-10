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


def test_co2_ramp_raises_the_warning_then_the_stop_alarm_which_breaks_the_silence(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    e.set_sim(scenario="co2Ramp", ramp_max_mm_hg=68, drive=0.5)
    for _ in range(140):
        run(e, 1)
        if e.alarms.is_active("co2High"):
            break
    a = e.alarms.get("co2High")
    # the warning limit (60 mmHg): medium priority, because a stop limit stands behind it
    assert a is not None and a.state == "active" and a.message.startswith("EtCO2 HIGH") and a.priority == "medium"
    assert "medium" in e.audio.played and "high" not in e.audio.played
    assert e.alarm_starts and e.alarm_starts[-1][1] == "medium"
    e.acknowledge()
    s = e.snapshot()
    assert s.silence_ms > 0 and e.alarms.get("co2High").acknowledged
    # the stop limit (65 mmHg): high priority, and it sounds although the warning was silenced
    for _ in range(60):
        run(e, 1)
        if e.alarms.is_active("co2Stop"):
            break
    stop = e.alarms.get("co2Stop")
    assert stop is not None and stop.priority == "high" and stop.message.startswith("STOP TEST")
    assert e.snapshot().silence_ms == 0
    run(e, 1)
    assert "high" in e.audio.played
    assert e.snapshot().alarms[0].id == "co2Stop"


def test_one_limit_without_a_stop_tier_is_a_high_priority_alarm(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, high_mm_hg=50, stop_mm_hg=None))
    e.connect("simulator")
    e.set_sim(scenario="co2Ramp")
    for _ in range(120):
        run(e, 1)
        if e.alarms.is_active("co2High"):
            break
    assert e.alarms.get("co2High").priority == "high" and "high" in e.audio.played


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
    run(e, 70)
    res = e.co2_response()
    assert res.split and len(res.baseline) >= 6 and len(res.rebreathing) >= 6
    assert 36 <= res.baseline_etco2 <= 39
    assert max(p.etco2 for p in res.rebreathing) > res.baseline_etco2
    # the breaths in the first 30 s of rebreathing are drawn but not fitted
    assert res.skip_sec == 30 and 5 <= len(res.skipped) <= 9
    assert all(p.t >= m.t / 1000 + 30 for p in res.rebreathing)
    # each point is the breath's own minute ventilation, not the 60 s average
    by_key = {q.key: q for q in e.trend}
    p = res.rebreathing[-1]
    assert p.mv == by_key[p.key].mv_breath != by_key[p.key].mv
    e.update_settings(test=dataclasses.replace(e.settings.test, response_skip_sec=0))
    assert e.co2_response().skipped == [] and len(e.co2_response().rebreathing) > len(res.rebreathing)


def test_guided_test_baseline_rebreathing_end_and_the_time_limit(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, rebreathing_max_sec=60))
    e.connect("simulator")
    run(e, 20)
    assert e.snapshot().test.phase == "idle"
    e.start_baseline()
    run(e, 10)
    ts = e.snapshot().test
    assert ts.phase == "baseline" and not ts.stable and "settling" in ts.text and "NOT RECORDING" in ts.text
    run(e, 30)
    ts = e.snapshot().test
    assert ts.stable and ts.text.startswith("BASELINE 0:40 · steady (EtCO2 ±")
    # breaths before the Baseline key (settling in) are not part of the baseline
    assert all(p.t >= ts.window.baseline for p in e.co2_response().baseline)
    assert not e.pipeline.rebreathing

    e.start_rebreathing()
    e.set_sim(scenario="co2Ramp", ramp_mm_hg_per_min=6)
    assert e.pipeline.rebreathing  # the bag gas factor applies from here
    run(e, 50)
    ts = e.snapshot().test
    assert ts.phase == "rebreathing" and ts.text.startswith("REBREATHING 0:50 of 1:00")
    assert not e.alarms.is_active("testTime")
    run(e, 12)
    limit = e.alarms.get("testTime")
    assert limit is not None and limit.priority == "medium" and limit.message.startswith("REBREATHING TIME LIMIT: 1:0")

    e.end_test()
    assert not e.alarms.is_active("testTime") and not e.pipeline.rebreathing
    n = len(e.co2_response().rebreathing)
    e.set_sim(scenario="normal")
    run(e, 20)
    ts = e.snapshot().test
    assert ts.phase == "ended" and ts.text.startswith("TEST ENDED after 1:02")
    res = e.co2_response()
    assert len(res.rebreathing) == n  # recovery breaths are not fitted
    assert len(res.skipped) > 5
    assert [m.label for m in e.markers] == ["Baseline", "Rebreathing", "End of test"]
    # a new baseline opens the next test
    e.start_baseline()
    assert e.snapshot().test.phase == "baseline"


def test_a_breath_can_be_taken_out_of_the_fit_by_hand_and_put_back(tmp_path):
    from co2response.storage.session_file import breaths_csv, clean_breaths

    e = make_engine(tmp_path)
    e.update_settings(test=dataclasses.replace(e.settings.test, response_skip_sec=0))
    e.connect("simulator")
    e.start_recording()
    run(e, 20)
    e.start_rebreathing()
    e.set_sim(scenario="co2Ramp")
    run(e, 40)
    res = e.co2_response()
    first, second = res.rebreathing[0], res.rebreathing[1]
    assert e.toggle_breath(first.key) and e.set_breath_excluded(second.key)
    assert not e.set_breath_excluded(second.key)  # already out
    out = e.co2_response()
    assert [p.key for p in out.excluded] == [first.key, second.key]
    assert len(out.rebreathing) == len(res.rebreathing) - 2
    assert e.toggle_breath(second.key)  # put back
    assert [p.key for p in e.co2_response().excluded] == [first.key]
    stop = e.stop_recording()
    text = stop.path.read_text()
    assert text.count("BREATH_EXCLUDED") == 2 and text.count("BREATH_INCLUDED") == 1
    parsed = e.library.parse(stop.rec.id)
    gone = [b for b in parsed.breaths if b.quality == "excluded"]
    assert len(gone) == 1 and gone[0].etco2 == pytest.approx(first.etco2, abs=0.06)
    assert gone[0] not in clean_breaths(parsed)
    assert ",excluded\n" in breaths_csv(parsed)
    # the breath's own length and expired volume are in the file
    b = clean_breaths(parsed)[-1]
    assert b.mv_breath == pytest.approx(b.vt / 1000 * 60 / b.period) and b.vte == pytest.approx(b.vt, rel=0.05)
    assert "excluded" in e.trend_csv()


def test_volume_mismatch_advisory_when_expired_volume_falls_short(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 40)
    s = e.snapshot()
    assert abs(s.numbers.volume_mismatch_pct) < 3 and s.numbers.vte == pytest.approx(s.numbers.vt, rel=0.03)
    assert not e.alarms.is_active("leak")
    # a pneumotach zero that has drifted by +9 L/min: inspiration over-reads, expiration under-reads
    c = e.channels
    c["channels"]["pneumo"]["zeroOffset"] = -45  # Pa (0.2 L/min per Pa)
    e.set_channels(c)
    run(e, 40)
    leak = e.alarms.get("leak")
    assert leak is not None and leak.priority == "advisory" and leak.message.startswith("VOLUME MISMATCH: expired volume")
    assert e.snapshot().numbers.volume_mismatch_pct > 20
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, leak_pct=0))
    run(e, 1)
    assert not e.alarms.is_active("leak")


def test_a_save_that_fails_is_shown_not_swallowed(tmp_path):
    e = make_engine(tmp_path)
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    (cfg / "settings.tmp").mkdir()  # the temporary file cannot be written
    e.update_settings(theme="light")
    assert e.store.save_error and "settings.json" in e.store.save_error
    e.tick()
    a = e.alarms.get("settingsSave")
    assert a is not None and a.priority == "advisory" and a.message.startswith("SETTINGS NOT SAVED (settings.json")
    assert any("could not be saved" in line.text for line in e.log)
    (cfg / "settings.tmp").rmdir()
    e.update_settings(theme="dark")
    e.tick()
    assert e.store.save_error is None and not e.alarms.is_active("settingsSave")


def test_subject_size_is_recorded_and_cleared_with_a_new_study_code(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(study_code="S-07", test=dataclasses.replace(e.settings.test, height_cm=172, weight_kg=64, age_years=23))
    e.connect("simulator")
    e.start_recording()
    run(e, 3)
    stop = e.stop_recording()
    parsed = e.library.parse(stop.rec.id)
    import json

    assert json.loads(parsed.meta["test"]) == {"responseSkipSec": 30, "heightCm": 172, "weightKg": 64, "ageYears": 23}
    # the next subject does not inherit the last one's size
    e.update_settings(study_code="S-08")
    assert (e.settings.test.height_cm, e.settings.test.weight_kg, e.settings.test.age_years) == (None, None, None)
    # nonsense is not kept
    e.update_settings(test=dataclasses.replace(e.settings.test, height_cm=-5, weight_kg="x"))
    assert (e.settings.test.height_cm, e.settings.test.weight_kg) == (None, None)
    assert make_engine(tmp_path).settings.study_code == "S-08"


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
    assert text.startswith("# CO2-Response session,v4")
    assert "MARK Event 1" in text and "MARK_RENAMED 1 drug given" in text and "RECORDING_STOPPED" in text
    lib = e.library
    assert [(r.status, r.stats["breaths"] > 3) for r in lib.list()] == [("complete", True)]

    rec2, parsed = lib.import_file(res.path, "reimported.csv")
    assert rec2.status == "imported" and len(lib.list()) == 2
    assert parsed.n == 3000
    assert breaths_csv(parsed).startswith("breath,time_s,wall_time_iso,etco2_mmHg")

    # replay it through the monitor with the file's calibration
    e.markers.clear()
    assert e.connect("replay", session=parsed, file_name="reimported.csv", speed=10)
    assert e.snapshot().using_file_calibration
    run(e, 5)
    s = e.snapshot()
    assert s.source_kind is None and s.status == "idle" and s.status_detail == "Replay finished"
    assert len(e.breaths) >= 6
    # the recording's marker is replayed too, with the label it was given
    assert [m.label for m in e.markers] == ["drug given"]


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

    def __init__(self, speed: float = 10, info: bytes = INFO) -> None:
        super().__init__(daemon=True)
        import pty
        import tty

        self.info = info

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
        self._write(self.info)
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
                    replies = [self.info.decode().strip()] if cmd == "INFO" else self.core.command(cmd)
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


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_heartbeat_is_sent_to_firmware_with_a_watchdog_and_switched_off_on_disconnect(tmp_path):
    """Firmware 1.1 sounds its own buzzer when the host's heartbeat stops; older firmware
    (no WD in its info line) is never sent one."""
    ard = FakeArduino(info=INFO.rstrip() + b",WD,3000\n")
    old = FakeArduino()

    def engine(dev, folder):
        ports = [PortInfo(dev.device, "Arduino Uno", 0x2341, 0x0043, "A1")]
        return MonitorEngine(store=SettingsStore(tmp_path / folder), audio=SilentAudio(), port_lister=lambda: ports, sessions_dir=tmp_path / "s", prevent_sleep=False)

    e, e_old = engine(ard, "a"), engine(old, "b")
    try:
        assert e.connect("serial", port=ard.device) and e_old.connect("serial", port=old.device)
        assert run_real(e, 8, lambda: ard.commands.count("HB") >= 3)
        assert e.info["WD"] == "3000"
        run_real(e_old, 2.5)
        assert "HB" not in old.commands and old.commands[:2] == ["INFO", "START"]
        # about one a second, not one per tick
        n0, t0 = ard.commands.count("HB"), time.monotonic()
        run_real(e, 2.2)
        assert 1 <= ard.commands.count("HB") - n0 <= 3, (ard.commands, time.monotonic() - t0)
        e.disconnect()
        time.sleep(0.3)
        assert ard.commands[-2:] == ["HB OFF", "STOP"]
    finally:
        e.shutdown()
        e_old.shutdown()
        ard.unplug()
        old.unplug()


# ------------------------------------------------------------------ the valve output


def valve_events(e) -> list[str]:
    return [line.text for line in e.log if line.text.startswith(("Valve to", "EMERGENCY STOP", "Marker: End"))]


def test_app_switches_the_valve_and_the_stop_alarm_turns_it_back(tmp_path):
    """With the valve switched from the app: to the bag at Start rebreathing, and back to
    room air by itself, with an end-of-test marker, as soon as the stop alarm starts."""
    e = make_engine(tmp_path)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, auto_valve=True))
    e.connect("simulator")
    run(e, 20)
    s = e.snapshot()
    assert s.valve == "air" and e.source.core.valve_to_bag is False
    e.start_recording()
    e.start_baseline()
    run(e, 5)
    e.set_sim(ramp_mm_hg_per_min=30, ramp_max_mm_hg=70, drive=0.5)
    e.start_rebreathing()
    run(e, 0.1)
    # the simulated valve is on the bag, and in the simulation that is what makes CO2 rise
    assert e.source.core.valve_to_bag and e.snapshot().valve == "bag" and e.settings.sim.ramp
    assert "valve to the bag" in e.snapshot().test.text
    stopped_at = None
    for _ in range(1200):
        run(e, 0.1)
        if e.alarms.get("co2Stop") is not None:
            stopped_at = e.clock.t
            break
    assert stopped_at is not None
    # within one pass of the alarm: valve to room air, test ended, CO2 no longer rising
    assert not e.source.core.valve_to_bag and e.snapshot().valve == "air" and not e.settings.sim.ramp
    run(e, 0.1)
    assert e.snapshot().test.phase == "ended" and e.markers[-1].label == "End of test (stop limit)"
    assert e.markers[-1].t - stopped_at <= 100
    assert valve_events(e) == ["Valve to the bag", "Valve to room air: automatic stop: stop limit", "Marker: End of test (stop limit)"]
    text = e.stop_recording().path.read_text(encoding="utf-8")
    assert "VALVE BAG" in text and "VALVE AIR (automatic stop: stop limit)" in text and "MARK End of test (stop limit)" in text
    run(e, 30)
    assert e.alarms.get("co2Stop").state == "resolved"  # CO2 came back down


def test_without_the_setting_the_app_never_moves_the_valve_but_emergency_stop_does(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 5)
    assert not e.settings.alarms.auto_valve
    e.start_rebreathing()
    run(e, 1)
    assert not e.source.core.valve_to_bag and valve_events(e) == []
    assert e.snapshot().test.can_emergency
    # someone moved the valve output by another route: the emergency stop still returns it to room air
    e.source.send("VALVE 1")
    run(e, 0.5)
    assert e.source.core.valve_to_bag and e.snapshot().valve == "bag"
    e.emergency_stop()
    run(e, 0.2)
    assert not e.source.core.valve_to_bag and e.snapshot().test.phase == "ended" and not e.snapshot().test.can_emergency
    assert e.markers[-1].label == "End of test (emergency stop)"
    assert valve_events(e)[-3:] == ["Valve to room air: emergency stop", "EMERGENCY STOP: valve to room air", "Marker: End of test (emergency stop)"]


def test_apnea_and_disconnecting_return_the_valve_to_room_air(tmp_path):
    e = make_engine(tmp_path)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, auto_valve=True))
    e.connect("simulator")
    run(e, 10)
    e.start_rebreathing()
    run(e, 5)
    e.set_sim_switch("apnea", True)
    run(e, 26)
    assert e.alarms.is_active("apnea") and not e.source.core.valve_to_bag
    assert e.markers[-1].label == "End of test (apnea)"
    # a deliberate disconnect never leaves the valve on the bag
    e.set_sim_switch("apnea", False)
    e.start_rebreathing()
    core = e.source.core
    assert core.valve_to_bag
    e.disconnect()
    assert not core.valve_to_bag


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_valve_commands_reach_the_board_and_a_board_without_a_valve_is_left_alone(tmp_path):
    ard = FakeArduino(info=INFO.rstrip() + b",WD,3000,VALVE,1\n")
    plain = FakeArduino()

    def engine(dev, folder):
        ports = [PortInfo(dev.device, "Arduino Uno", 0x2341, 0x0043, "A1")]
        e = MonitorEngine(store=SettingsStore(tmp_path / folder), audio=SilentAudio(), port_lister=lambda: ports, sessions_dir=tmp_path / "s", prevent_sleep=False)
        e.update_settings(alarms=dataclasses.replace(e.settings.alarms, auto_valve=True))
        return e

    e, e_plain = engine(ard, "a"), engine(plain, "b")
    try:
        assert e.connect("serial", port=ard.device) and e_plain.connect("serial", port=plain.device)
        assert run_real(e, 8, lambda: e.valve_available()) and e.snapshot().valve == "air"
        run_real(e_plain, 2)
        assert not e_plain.valve_available() and e_plain.snapshot().valve is None
        e.start_rebreathing()
        e_plain.start_rebreathing()
        assert run_real(e, 3, lambda: "VALVE 1" in ard.commands)
        e.end_test()
        assert run_real(e, 3, lambda: "VALVE 0" in ard.commands)
        e.start_rebreathing()
        assert run_real(e, 3, lambda: ard.commands.count("VALVE 1") == 2)
        e.disconnect()  # room air first, then the heartbeat is switched off
        time.sleep(0.3)
        assert [c for c in ard.commands if c != "HB"][-3:] == ["VALVE 0", "HB OFF", "STOP"]
        run_real(e_plain, 0.5)
        assert not any(c.startswith("VALVE") for c in plain.commands)
        assert e_plain.emergency_stop() is not None  # still ends the test in the record
    finally:
        e.shutdown()
        e_plain.shutdown()
        ard.unplug()
        plain.unplug()


# ------------------------------------------------------------------ SpO2 (the safety channel)


def with_oximeter(e, **kw):
    e.update_settings(spo2=dataclasses.replace(e.settings.spo2, enabled=True, **kw))


def test_spo2_channel_reads_the_oximeter_and_is_recorded_and_replayed(tmp_path):
    e = make_engine(tmp_path)
    e.connect("simulator")
    run(e, 5)
    assert math.isnan(e.snapshot().numbers.spo2) and len(e._last.volts) == 3  # no oximeter: three values per line
    with_oximeter(e)  # switched on while running: the simulated oximeter appears on the fourth input
    run(e, 5)
    s = e.snapshot()
    assert len(e._last.volts) == 4 and s.numbers.spo2 == pytest.approx(98, abs=0.5)
    assert not e.alarms.is_active("spo2Low") and not e.alarms.is_active("spo2NoSignal")
    e.set_sim(spo2=94)
    e.start_recording()
    run(e, 20)
    assert e.snapshot().numbers.spo2 == pytest.approx(94, abs=0.5)
    res = e.stop_recording()
    parsed = e.library.parse(res.rec.id)
    assert parsed.has_spo2 and float(parsed.spo2[-1]) == pytest.approx(94, abs=0.5)
    assert float(parsed.v3[-1]) == pytest.approx(0.94, abs=0.01)  # 1 V = 100 % by default
    import json

    from co2response.storage.session_file import summarize

    assert json.loads(parsed.meta["spo2"])["enabled"] is True and summarize(parsed).spo2_min == pytest.approx(94, abs=1.5)
    from co2response.storage.report import build_report, result_lines

    assert dict(result_lines(build_report(parsed)))["SpO2"] == "lowest 94 %"
    # replay sends the fourth value again, so the replayed monitor shows SpO2 too
    e.disconnect()
    assert e.connect("replay", session=parsed, file_name="x.csv", speed=10)
    run(e, 1)
    assert len(e._last.volts) == 4 and e.snapshot().numbers.spo2 == pytest.approx(94, abs=1.5)
    # a session with no oximeter has empty columns and replays three values
    plain = make_engine(tmp_path / "plain")
    plain.connect("simulator")
    plain.start_recording()
    run(plain, 3)
    p2 = plain.library.parse(plain.stop_recording().rec.id)
    assert not p2.has_spo2 and "spo2" not in p2.meta and math.isnan(summarize(p2).spo2_min)


def test_low_spo2_alarm_after_apnea_and_it_returns_the_valve_to_room_air(tmp_path):
    e = make_engine(tmp_path)
    with_oximeter(e, low_pct=92, low_delay_sec=5)
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, auto_valve=True, apnea_sec=0))  # the apnea alarm off, to see SpO2 act alone
    e.connect("simulator")
    run(e, 10)
    e.start_rebreathing()
    run(e, 5)
    e.set_sim_switch("apnea", True)
    run(e, 30)
    # the saturation holds for 20 s of apnea, then falls: 98 -> 92 takes 15 s more, and the alarm 5 s after that
    assert e.snapshot().numbers.spo2 > 91 and not e.alarms.is_active("spo2Low")
    run(e, 15)
    a = e.alarms.get("spo2Low")
    assert a is not None and a.priority == "high" and a.message.startswith("SpO2 LOW 8") and a.message.endswith("% < 92")
    assert not e.source.core.valve_to_bag and e.markers[-1].label == "End of test (low SpO2)"
    # breathing again: it recovers and the alarm resolves 1 % above the limit
    e.set_sim_switch("apnea", False)
    run(e, 20)
    assert e.alarms.get("spo2Low").state == "resolved" and e.snapshot().numbers.spo2 > 96
    # the limit can be switched off
    with_oximeter(e, low_pct=0)
    e.set_sim(spo2=75)
    run(e, 20)
    assert e.alarms.get("spo2Low").state == "resolved"


def test_an_oximeter_that_gives_no_reading_is_reported_not_shown_as_a_number(tmp_path):
    e = make_engine(tmp_path)
    with_oximeter(e)
    e.connect("simulator")
    run(e, 3)
    assert e.snapshot().numbers.spo2 == pytest.approx(98, abs=0.5)
    # the probe comes off: the oximeter's output drops to 0 V, which is not a saturation
    e.source.core.encoders = e.source.core.encoders._replace(spo2=lambda pct: 0.0)
    run(e, 6)
    assert math.isnan(e.snapshot().numbers.spo2) and not e.alarms.is_active("spo2Low")
    run(e, 6)
    assert e.alarms.get("spo2NoSignal").priority == "advisory"
    # a wrong scaling shows the same way rather than as a wrong number, and the right one clears it
    with_oximeter(e, gain_pct_per_v=20)
    run(e, 3)
    assert e.snapshot().numbers.spo2 == pytest.approx(98, abs=0.5) and not e.alarms.is_active("spo2NoSignal")
    # switched off: no number, no notice
    e.update_settings(spo2=dataclasses.replace(e.settings.spo2, enabled=False))
    run(e, 12)
    assert math.isnan(e.snapshot().numbers.spo2) and not e.alarms.is_active("spo2NoSignal") and len(e._last.volts) == 3
