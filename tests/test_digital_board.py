"""The Uno R4 board with digital sensors (SFM3304-D flow, SprintIR-6S CO2), the waveform
fixes, the MV-vs-EtCO2 graph's labels, the terminal monitor and the fast whole test."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from conftest import ROOT
from test_engine import make_engine, run

from co2response.channels import DEFAULT_DIGITAL_CHANNELS, is_digital, single_flow
from co2response.engine.monitor import simulator_encoders
from co2response.processing.metrics import co2_response, response_labels
from co2response.protocol import parse_line
from co2response.settings import SettingsStore
from co2response.sim.core import NOT_ANSWERING, SimulatorCore


def digital_engine(tmp_path, **kw):
    e = make_engine(tmp_path, **kw)
    e.update_settings(hardware="digital")
    return e


def data(core: SimulatorCore, seconds: float):
    out = []
    for _ in range(int(seconds * core.rate_hz)):
        for line in core.next_sample():
            p = parse_line(line)
            if p.kind == "data":
                out.append(p.volts)
    return out


# ---------- the simulated board


def test_digital_board_says_what_it_is_and_sends_units():
    core = SimulatorCore(simulator_encoders(None, digital=True), -9999, 9999, digital=True)
    info = parse_line(core.command("INFO")[0])
    assert info.fields["UNITS"] == "1" and info.fields["BOARD"] == "UNO_R4" and info.fields["CH"] == "2"
    rows = data(core, 8)
    co2 = [r[0] for r in rows]
    flow = [r[1] for r in rows]
    assert all(len(r) == 2 for r in rows)
    # % CO2 (0 to about 5 %), L/min both ways (inspiration positive)
    assert 4.0 < max(co2) < 6.0 and min(co2) < 0.5
    assert max(flow) > 20 and min(flow) < -20
    # the SprintIR reports 20 times a second, so the value is held for 5 samples at 100 Hz
    changes = sum(1 for a, b in zip(co2, co2[1:]) if a != b)
    assert changes <= len(co2) / 5 + 1


def test_a_sensor_that_does_not_answer_is_sent_as_9999():
    core = SimulatorCore(simulator_encoders(None, digital=True), -9999, 9999, digital=True)
    core.model.set_params(co2_disconnected=True, flow_disconnected=True)
    rows = data(core, 1)
    assert rows[-1] == [NOT_ANSWERING, NOT_ANSWERING]


def test_board_commands_are_answered():
    core = SimulatorCore(simulator_encoders(None, digital=True), -9999, 9999, digital=True)
    assert core.command("ALARM 2") == ["A,ALARM,2"]
    assert core.command("PUMP 1") == ["A,PUMP,1"]
    assert core.command("VALVE 1") == ["A,VALVE,1"]


# ---------- the engine on the digital board


def test_engine_reads_the_digital_board_with_one_flow_sensor(tmp_path):
    e = digital_engine(tmp_path)
    e.connect("simulator")
    run(e, 30)
    s = e.snapshot()
    assert s.hardware == "digital" and s.single_flow
    assert is_digital(e.pipeline.channels) and single_flow(e.pipeline.channels)
    n = s.numbers
    assert n.etco2 == pytest.approx(38, abs=1.5)
    assert n.rr == pytest.approx(15, abs=0.6)
    assert n.vt == pytest.approx(500, rel=0.05)
    assert n.mv == pytest.approx(7.5, rel=0.08)
    # the one flow sensor is both the flow waveform and the breath/volume channel
    assert n.flow == pytest.approx(n.pneumo_flow, abs=1e-9)
    assert not [a for a in s.alarms if a.state == "active" and a.priority != "advisory"]


def test_an_unplugged_digital_sensor_raises_a_not_answering_alarm(tmp_path):
    e = digital_engine(tmp_path)
    e.connect("simulator")
    run(e, 8)
    e.set_sim_switch("co2_disconnected", True)
    run(e, 5)
    s = e.snapshot()
    rail = e.alarms.get("railCo2")
    assert rail is not None and "NOT ANSWERING" in rail.message
    assert s.stale["co2"]


def test_each_board_keeps_its_own_calibration(tmp_path):
    e = digital_engine(tmp_path)
    c = e.channels
    assert is_digital(c)
    c["channels"]["flow"]["calibration"]["gain"] = 1.05
    e.set_channels(c)
    store = SettingsStore(tmp_path / "cfg")
    assert store.load_saved_channels("digital")["channels"]["flow"]["calibration"]["gain"] == 1.05
    assert store.load_saved_channels("analog") is None
    e.update_settings(hardware="analog")
    assert not is_digital(e.channels)
    e.update_settings(hardware="digital")
    assert e.channels["channels"]["flow"]["calibration"]["gain"] == 1.05
    e.reset_channels()
    assert e.channels == DEFAULT_DIGITAL_CHANNELS


def test_switching_the_board_restarts_a_running_simulator_as_the_other_board(tmp_path):
    e = digital_engine(tmp_path)
    e.connect("simulator")
    run(e, 2)
    assert e.snapshot().info["CH"] == "2"
    e.update_settings(hardware="analog")
    run(e, 2)
    s = e.snapshot()
    assert s.hardware == "analog" and not s.single_flow and s.info["CH"] == "3"


def test_button_reed_switch_and_alarm_output(tmp_path):
    e = digital_engine(tmp_path)
    e.connect("simulator")
    sent: list[str] = []
    send = e.source.send
    e.source.send = lambda c: (sent.append(c), send(c))
    run(e, 3)
    # the valve handle turned to the bag: rebreathing starts; back to room air: the test ends
    e._on_line("A,REED,1")
    assert e.snapshot().test.phase == "rebreathing"
    run(e, 2)
    e._on_line("A,REED,0")
    assert e.snapshot().test.phase == "ended"
    # the button outside a test places a marker
    n = len(e.markers)
    e._on_line("A,BUTTON")
    assert len(e.markers) == n + 1
    # during rebreathing it is an emergency stop
    run(e, 1)
    e.start_rebreathing()
    run(e, 1)
    e._on_line("A,BUTTON")
    assert e.snapshot().test.phase == "ended"
    # the board's LED and buzzer follow the alarms
    e.set_sim(etco2=70)
    run(e, 15)
    assert "ALARM 2" in sent
    e.set_sim(etco2=38)
    run(e, 30)
    assert sent[-1 if sent[-1].startswith("ALARM") else -2].startswith("ALARM") and "ALARM 0" in sent


# ---------- the waveforms


def test_the_sweep_gets_one_sample_in_speed_so_it_moves_at_the_real_time_pace(tmp_path):
    e = digital_engine(tmp_path)
    e.update_settings(sim=e.settings.sim.__class__(speed=10))
    e.connect("simulator")
    e.take_waves()
    run(e, 2)
    w = e.take_waves()
    # 2 s at 10x is 2000 samples; the sweep gets about 200, one screen in the real-time pace
    assert 150 <= len(w["co2"]) <= 230
    assert e.wave_compression == 10
    e.set_sim(speed=1)
    assert e.wave_compression == 1


# ---------- the response graph


def test_response_labels_read_like_the_lab_figures():
    pts = [(i, 38 + 0.1 * (i % 3), 7 + 0.1 * (i % 2)) for i in range(6)]
    pts += [(10 + i, 45 + i, 14 + 1.2 * i + (0.5 if i % 2 else -0.5)) for i in range(10)]
    res = co2_response(pts, 10)
    fit, base = response_labels(res)
    assert fit.startswith("Response slope = 1.") and "(L/min)/mmHg · r = 0.9" in fit
    assert base.startswith("Baseline Ave = 38.") and "mmHg · MV = 7.05 L/min" in base


def test_breath_to_breath_variability_scatters_the_response(tmp_path):
    from co2response.headless import run_fast_test

    e, csv, pdf = run_fast_test("normal", tmp_path / "s", quiet=True)
    res = e.co2_response()
    assert csv is not None and csv.exists() and pdf is not None and pdf.read_bytes()[:5] == b"%PDF-"
    assert res.fit is not None and len(res.rebreathing) >= 15
    assert 0.2 < res.slope < 0.8 and 0.5 < res.r < 0.99  # a real-looking cloud, not a perfect line
    assert len(res.baseline) >= 4


def test_without_variability_every_breath_lies_on_the_line(tmp_path):
    from co2response.headless import run_fast_test

    e, _, _ = run_fast_test("normal", tmp_path / "s", quiet=True, variability=0)
    res = e.co2_response()
    assert res.fit is not None and res.r > 0.97


# ---------- the terminal monitor


def test_braille_canvas_and_graphs_draw():
    from co2response.tui.canvas import Canvas, SweepBuffer, big_digits, draw_response, draw_sweep

    cv = Canvas(10, 3)
    cv.line(0, 0, 19, 11, "red")
    lines = cv.lines()
    assert len(lines) == 3 and all(len(t.plain) == 10 for t in lines)
    assert any("⠀" < ch <= "⣿" for ch in lines[0].plain)

    sw = SweepBuffer(100)
    sw.push([math.sin(i / 5) * 40 for i in range(150)])
    cv = Canvas(40, 6)
    draw_sweep(cv, sw, -60, 60, "cyan", ticks=[-60, 0, 60], signed=True)
    assert "+60" in cv.render().plain

    pts = [(i, 38, 7 + 0.2 * (i % 3)) for i in range(5)] + [(10 + i, 45 + i, 12 + i) for i in range(8)]
    cv = Canvas(70, 20)
    draw_response(cv, co2_response(pts, 10), title="SUBJECT X")
    text = cv.render().plain
    assert "Response slope = 1.000" in text and "Baseline Ave = 38.00 mmHg" in text and "etCO2 (mmHg)" in text
    assert "●" in text and "○" in text and "SUBJECT X" in text
    assert big_digits("38")[0] == "███ ███"

    # the trend is a line graph with a dot on every breath
    from co2response.tui.canvas import draw_trend

    cv = Canvas(60, 12)
    draw_trend(cv, [0, 10, 20, 30], [7.0, 8.0, 7.5, 9.0], 0, 40, 12, "green", "MV (L/min) per breath")
    text = cv.render().plain
    assert text.count("●") == 4 and any("\u2800" < ch <= "\u28ff" for ch in text)


def test_terminal_monitor_runs_a_whole_test(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from co2response.tui.app import GraphPanel, MonitorApp, Tile

    monkeypatch.setenv("CO2R_HOME", str(tmp_path))
    monkeypatch.setenv("CO2R_HARDWARE", "digital")

    async def go():
        app = MonitorApp(situation="test_normal", speed=50)
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause(5)
            e = app.engine
            assert e.snapshot().hardware == "digital"
            assert len(e.trend) > 20
            graph = app.query_one(GraphPanel).render()
            assert "Response slope" in str(graph) or e.co2_response().fit is None
            assert app.query_one("#w-pneumo").display is False  # one flow sensor: one flow waveform
            # every number in block digits of one size, as large as EtCO2's
            from co2response.tui.app import on_screen

            tiles = {t.id: t for t in app.query(Tile) if on_screen(t)}
            assert set(tiles) == {"t-etco2", "t-rr", "t-flow", "t-vt", "t-mv"}
            heights = {k: len(str(t.render()).strip("\n").splitlines()) for k, t in tiles.items()}
            assert len(set(heights.values())) == 1 and min(heights.values()) >= 5, heights
            assert "█" in str(tiles["t-mv"].render()) and "█" in str(tiles["t-rr"].render())
            await pilot.press("v")
            await pilot.pause(0.4)
            assert "[reverse] ETCO2 TREND [/" in app.query_one(GraphPanel).border_title  # the tab showing, highlighted
            await pilot.press("v", "v")
            await pilot.pause(0.4)
            assert "EtCO2" in str(app.query_one(GraphPanel).render())  # the breath table
            await pilot.press("s")
            await pilot.pause(0.3)
            await pilot.press("escape")
            await pilot.press("q")

    asyncio.run(go())


# ---------- command line and firmware


def test_command_line_fast_test_and_situations(tmp_path, capsys, monkeypatch):
    from co2response.__main__ import main

    monkeypatch.setenv("CO2R_HOME", str(tmp_path / "home"))
    assert main(["--list-situations"]) == 0
    assert "test_normal" in capsys.readouterr().out
    assert main(["--fast-test", "brisk", "--sessions", str(tmp_path / "s"), "--variability", "10"]) == 0
    out = capsys.readouterr().out
    assert "Response slope" in out and "whole simulated test in" in out
    assert list((tmp_path / "s").glob("*.pdf"))


def test_fast_test_prints_on_a_console_that_cannot_encode_braille(tmp_path, monkeypatch):
    # a Windows console on code page 1252 (as on the release build machine)
    import io
    import sys

    from co2response.__main__ import main

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setenv("CO2R_HOME", str(tmp_path / "home"))
    assert main(["--fast-test", "normal", "--sessions", str(tmp_path / "s")]) == 0
    sys.stdout.flush()
    assert "Response slope".encode() in raw.getvalue()


def test_both_firmwares_format_lines_the_same_way():
    a = (ROOT / "firmware" / "CO2Response" / "line_format.h").read_text()
    b = (ROOT / "firmware" / "CO2ResponseR4" / "line_format.h").read_text()
    assert a == b


def test_r4_firmware_matches_the_wiring():
    cfg = (ROOT / "firmware" / "CO2ResponseR4" / "config.h").read_text()
    for pin in ("BUTTON_PIN 2", "REED_PIN 3", "ALARM_LED_PIN 7", "BUZZER_PIN 8", "HEATER_PIN 9", "PUMP_PIN 10", "SFM_I2C_ADDRESS 0x2E", "CO2_BAUD 9600"):
        assert f"#define {pin}" in cfg, pin
    ino = (ROOT / "firmware" / "CO2ResponseR4" / "CO2ResponseR4.ino").read_text()
    # the info line tells the app which board it is
    assert "UNITS,1" in ino and "A,BUTTON" in ino and "A,REED,1" in ino and "ALARM" in ino
    assert Path(ROOT / "firmware" / "r4" / "platformio.ini").exists()


def test_the_response_curve_bends_with_the_data_and_ignores_an_outlier():
    from co2response.processing.metrics import lowess_curve

    xs = [40 + 0.5 * i for i in range(40)]
    ys = [5 + 0.05 * (x - 40) ** 2 for x in xs]  # a response that steepens
    ys[20] += 15  # one erratic breath
    cx, cy = lowess_curve(xs, ys)
    assert len(cx) == 48 and cx[0] == xs[0] and cx[-1] == xs[-1]
    truth = [5 + 0.05 * (x - 40) ** 2 for x in cx]
    assert max(abs(a - b) for a, b in zip(cy, truth)) < 2.0  # follows the bend, not the outlier
    slopes = [(cy[i + 1] - cy[i]) / (cx[i + 1] - cx[i]) for i in range(len(cx) - 1)]
    assert slopes[-1] > 2 * slopes[5]  # steeper at the top: a curve, not a line
    assert lowess_curve([38] * 6, [7] * 6) == ([], [])  # no spread in EtCO2: no curve
    assert lowess_curve([38, 39, 40], [7, 8, 9]) == ([], [])  # too few breaths
    # breath-to-breath scatter does not make it wiggle: it changes direction at most once
    import random

    rnd = random.Random(3)
    sx = [40 + 0.4 * i for i in range(60)]
    sy = [8 + 0.5 * (x - 40) + rnd.gauss(0, 2.5) for x in sx]
    gx, gy = lowess_curve(sx, sy)
    d = [b - a for a, b in zip(gy, gy[1:])]
    turns = sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0))
    assert turns <= 1 and gy[-1] > gy[0] + 5


def test_update_pulls_a_repository_copy_or_reinstalls_from_github(monkeypatch, capsys):
    import pathlib

    from co2response.__main__ import REPO_URL, update

    calls: list = []
    assert update(run=lambda cmd: calls.append(cmd) or 0) == 0
    assert calls == [["git", "-C", str(ROOT), "pull", "--ff-only"]]  # this checkout has .git
    calls.clear()
    real_exists = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False if self.name == ".git" else real_exists(self))
    assert update(run=lambda cmd: calls.append(cmd) or 0) == 0
    assert len(calls) == 2 and "--force-reinstall" in calls[0] and calls[0][-1].endswith(f"@ git+{REPO_URL}")
    assert "co2response" in calls[1][-1] and "--force-reinstall" not in calls[1]
    assert "Updated" in capsys.readouterr().out
