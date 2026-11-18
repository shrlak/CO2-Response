"""Recordings as CSV, and what can be done with them afterwards: replay with pause, play,
speed and stop; continuing a saved recording (resume) from its CSV; the in-depth
analysis; and the firmware for the parts the team ordered (SFM3300-250-D, SprintIR-R-20)."""

import math
from pathlib import Path

import numpy as np
import pytest

from co2response.alarms.audio import SilentAudio
from co2response.engine.monitor import MonitorEngine
from co2response.settings import SettingsStore
from co2response.storage.analysis import BREATH_COLUMNS, analysis_csv, analysis_text, analyze_session, breath_detail_csv, slope_stats
from co2response.storage.session_file import parse_session_text, read_session

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample-data" / "synthetic_session.csv"
T0 = 1_793_854_800_000


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(tmp_path, clock, name="cfg"):
    e = MonitorEngine(store=SettingsStore(tmp_path / name), audio=SilentAudio(), clock=clock, port_lister=lambda: [], sessions_dir=tmp_path / "sessions", prevent_sleep=False)
    e.update_settings(hardware="digital", study_code="T")
    return e


def run(e, clock, seconds):
    end = clock.t + seconds * 1000
    nxt = clock.t + 250
    while clock.t < end:
        clock.t += 20
        e.poll()
        if clock.t >= nxt:
            e.tick()
            nxt += 250


# ---------------------------------------------------------------- pause and play


def test_the_simulator_pauses_and_time_stands_still(tmp_path):
    clock = Clock()
    e = make(tmp_path, clock)
    assert not e.can_pause and not e.set_paused(True)  # nothing connected: nothing to pause
    e.connect("simulator")
    e.start_recording()
    run(e, clock, 30)
    assert e.can_pause and e.snapshot().can_pause
    assert e.set_paused(True) and e.snapshot().paused
    n, t, rows = len(e.trend), e.clock(), e.recorder.rows
    run(e, clock, 60)  # a minute on the wall: no data, no breaths, no alarms, the clock frozen
    assert len(e.trend) == n and e.clock() == t and e.recorder.rows == rows
    assert not e.alarms.is_active("noData") and not e.alarms.is_active("apnea")
    assert not e.toggle_pause()
    run(e, clock, 20)
    assert len(e.trend) > n
    res = e.stop_recording()
    s = read_session(res.path)
    texts = [x.text for x in s.events]
    assert "PAUSED" in texts and "UNPAUSED" in texts
    # the pause leaves no hole in the recording's time: time_s runs straight on
    assert float(np.diff(s.time_s).max()) < 0.05
    assert s.duration_s == pytest.approx(50, abs=1)


def test_a_live_device_cannot_be_paused(tmp_path):
    clock = Clock()
    e = make(tmp_path, clock)
    e.connect("simulator")
    e._source.kind = "serial"  # stands in for an Arduino: its data would keep coming
    assert not e.can_pause and not e.set_paused(True)


def test_a_replay_pauses_plays_changes_speed_and_stops(tmp_path):
    clock = Clock()
    e = make(tmp_path, clock)
    s = read_session(SAMPLE)
    e.connect("replay", session=s, file_name=SAMPLE.name)
    run(e, clock, 10)
    snap = e.snapshot()
    assert snap.source_kind == "replay" and snap.replay_speed == 1
    assert snap.replay_len_s == pytest.approx(180, abs=1) and 9 <= snap.replay_pos_s <= 11
    e.set_paused(True)
    run(e, clock, 30)
    assert 9 <= e.snapshot().replay_pos_s <= 11  # paused: it has not moved on
    e.set_paused(False)
    e.set_replay_speed(10)
    run(e, clock, 5)
    snap = e.snapshot()
    assert snap.replay_speed == 10 and 55 <= snap.replay_pos_s <= 65  # 5 s at 10x after the first 10 s
    breaths = len(e.trend)
    e.disconnect()  # stop
    assert e.snapshot().source_kind is None and not e.snapshot().paused and breaths >= 10


# ---------------------------------------------------------------- resuming a recording


def test_a_saved_recording_resumes_from_its_csv_and_carries_on(tmp_path):
    clock = Clock()
    e = make(tmp_path, clock)
    e.connect("simulator")
    e.start_recording()
    run(e, clock, 15)
    e.start_baseline()
    run(e, clock, 45)
    first = e.stop_recording()
    e.disconnect()
    original = first.path.read_text(encoding="utf-8")
    s1 = read_session(first.path)
    n1 = len(s1.breaths)
    assert n1 >= 10

    # a day later, on a fresh engine: the file is uploaded and resumed
    clock.t += 24 * 3600 * 1000
    e2 = make(tmp_path, clock, "cfg2")
    rec = e2.resume_session(parse_session_text(original), first.path.name)
    assert rec is not None and e2.recorder.active and e2.recorder.file_name.endswith("_resumed.csv")
    # the earlier session is back on the monitor: its breaths, its marker, the test phase
    assert len(e2.trend) == n1 and [m.label for m in e2.markers] == ["Baseline"]
    assert e2.test_state().phase == "baseline"
    assert e2.trace_t and e2.trend[-1].t < e2.clock() / 1000
    assert e2.snapshot().resumed_from == first.path.name
    with pytest.raises(ValueError):
        e2.resume_session(s1, first.path.name)  # one recording at a time

    e2.connect("simulator")
    run(e2, clock, 20)
    e2.start_rebreathing()
    run(e2, clock, 40)
    # the response graph now spans both parts: baseline breaths from the file, rebreathing new
    res = e2.co2_response()
    assert res.split and len(res.baseline) >= 5
    done = e2.stop_recording()
    s2 = read_session(done.path)

    assert first.path.read_text(encoding="utf-8") == original  # the earlier file is untouched
    assert s2.meta.get("resumed_from") == first.path.name
    assert s2.n > s1.n and len(s2.breaths) > n1
    # time_s carries on 1 s after the earlier part, and breath numbers carry on too
    t = np.asarray(s2.time_s)
    gap = np.diff(t).max()
    assert 0.9 < gap < 1.2 and t[0] == s1.time_s[0]
    ns = [b.n for b in s2.breaths]
    assert ns == sorted(ns) and len(set(ns)) == len(ns)
    texts = [x.text for x in s2.events]
    assert any(x.startswith("RECORDING_RESUMED from=") for x in texts)
    assert "MARK Baseline" in texts and "MARK Rebreathing" in texts
    # and the resumed file can be resumed again, replayed and analysed like any other
    a = analyze_session(s2, done.path.name)
    assert a.get("recording", "times resumed") == 1
    e3 = make(tmp_path, clock, "cfg3")
    e3.resume_session(s2, done.path.name)
    assert e3.recorder.file_name.startswith(first.path.stem + "_resumed") and e3.recorder.file_name != done.path.name


def test_an_older_file_resumes_as_the_current_format(tmp_path):
    clock = Clock()
    e = make(tmp_path, clock)
    s = read_session(SAMPLE)
    e.resume_session(s, SAMPLE.name)
    e.connect("simulator")
    run(e, clock, 10)
    out = read_session(e.stop_recording().path)
    assert out.version == 4 and out.n > s.n and out.breaths[0].n == s.breaths[0].n


# ---------------------------------------------------------------- the in-depth analysis


def test_the_analysis_of_the_sample_session():
    s = read_session(SAMPLE)
    a = analyze_session(s, SAMPLE.name)
    assert a.get("recording", "samples") == s.n
    assert a.get("recording", "sample rate (measured)") == pytest.approx(100, abs=1)
    assert a.get("quality", "breaths") == len(s.breaths)
    # the simulator's breathing: about 15 /min, Ti/Ttot about 1/3, VT 500 mL at baseline
    assert a.get("phase: baseline", "RR mean") == pytest.approx(15, abs=1)
    assert a.get("phase: baseline", "Ti/Ttot mean") == pytest.approx(1 / 3, abs=0.05)
    assert a.get("phase: baseline", "VT mean") == pytest.approx(500, abs=20)
    # the response agrees with the report's, with a confidence interval around it
    slope = a.get("response", "slope")
    assert 0.2 < slope < 0.4
    assert a.get("response", "slope 95 % CI low") < slope < a.get("response", "slope 95 % CI high")
    assert a.get("response", "EtCO2 rise during rebreathing") > 0
    assert 0 <= a.get("set-up", "CO2 delay after expiration starts") < 1.0
    assert abs(a.get("set-up", "flow bias (net flow over a breath)")) < 1.0

    table = analysis_csv(a).splitlines()
    assert table[0] == "section,metric,value,unit,note" and len(table) > 60
    detail = breath_detail_csv(a).splitlines()
    assert detail[0] == ",".join(BREATH_COLUMNS) and len(detail) == len(s.breaths) + 1
    assert "RESPONSE" in analysis_text(a)


def test_the_analysis_finds_a_sample_line_delay_and_a_pump_bias(tmp_path):
    """A sidestream CO2 sensor 1 s behind the flow, and a sample pump drawing 0.2 L/min
    through the flow sensor: both show up in the analysis."""
    s = read_session(SAMPLE)
    k = 100  # 1 s at 100 Hz
    s.co2_mmhg = np.concatenate([np.full(k, s.co2_mmhg[0]), s.co2_mmhg[:-k]])
    s.pneumo_lpm = s.pneumo_lpm + 0.2
    a = analyze_session(s)
    base = analyze_session(read_session(SAMPLE))
    d0 = base.get("set-up", "CO2 delay after expiration starts")
    assert a.get("set-up", "CO2 delay after expiration starts") == pytest.approx(d0 + 1.0, abs=0.15)
    assert a.get("set-up", "flow bias (net flow over a breath)") == pytest.approx(base.get("set-up", "flow bias (net flow over a breath)") + 0.2, abs=0.05)


def test_slope_statistics():
    xs = [40.0 + i for i in range(10)]
    ys = [2.0 * x - 70 for x in xs]
    slope, se, ci, r2, n = slope_stats(xs, ys)
    assert slope == pytest.approx(2) and se == pytest.approx(0, abs=1e-9) and r2 == pytest.approx(1) and n == 10
    assert all(math.isnan(v) for v in slope_stats([1.0, 2.0], [1.0, 2.0])[:4])


def test_analyze_on_the_command_line(tmp_path, capsys):
    from co2response.__main__ import main

    assert main(["--analyze", str(SAMPLE), "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "RESPONSE" in out and "wrote" in out
    assert (tmp_path / "synthetic_session_analysis.csv").exists()
    assert (tmp_path / "synthetic_session_breath_detail.csv").exists()
    assert main(["--analyze", str(tmp_path / "missing.csv")]) == 1


# ---------------------------------------------------------------- the firmware for the parts ordered


def test_the_sfm3300_crc_and_scaling_match_the_firmware():
    """The SFM3000 family's CRC-8 (polynomial 0x31, initial 0x00) and flow = (raw - 32768) / 120."""

    def crc(data: bytes) -> int:
        c = 0x00
        for b in data:
            c ^= b
            for _ in range(8):
                c = ((c << 1) ^ 0x31) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        return c

    # Sensirion's own example for this CRC: 0xBEEF -> 0x92 is for initial 0xFF; with 0x00 it differs
    assert crc(b"\xbe\xef") != 0x92
    raw = 32768 + 120 * 30  # 30 slm
    assert (raw - 32768) / 120 == 30
    h = (ROOT / "firmware" / "CO2ResponseR4" / "sensirion.h").read_text()
    assert "(uint8_t)((crc << 1) ^ 0x31)" in h and "uint8_t crc = 0x00" in h
    sprint = (ROOT / "firmware" / "CO2ResponseR4" / "sprintir.h").read_text()
    assert '"M 2\\r\\n"' in sprint and '"G\\r\\n"' in sprint  # one field at 50 Hz; zero in fresh air
