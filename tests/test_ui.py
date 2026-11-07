"""The desktop window, driven headless on a fake clock (QT_QPA_PLATFORM=offscreen).

These cover what a person does: start the simulator, read the numbers, see and
acknowledge alarms, calibrate, record, import, review, run the hardware check.
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from conftest import ROOT  # noqa: E402
from test_engine import Clock  # noqa: E402

from co2response.alarms.audio import SilentAudio  # noqa: E402
from co2response.engine.monitor import MonitorEngine  # noqa: E402
from co2response.settings import SettingsStore  # noqa: E402


@pytest.fixture
def win(qtbot, tmp_path):
    from co2response.ui import theme
    from co2response.ui.main_window import MainWindow

    theme.current = theme.DARK
    eng = MonitorEngine(store=SettingsStore(tmp_path / "cfg"), audio=SilentAudio(), clock=Clock(), port_lister=lambda: [], sessions_dir=tmp_path / "sessions", prevent_sleep=False)
    w = MainWindow(eng)
    for t in (w.poll_timer, w.tick_timer, w.frame_timer):
        t.stop()  # the test moves time itself
    qtbot.addWidget(w)
    w.resize(1366, 800)
    w.show()
    yield w
    eng.shutdown()


def advance(w, seconds: float) -> None:
    """Move the fake clock forward, running the window's timers as they would fire."""
    e = w.engine
    end = e.clock.t + seconds * 1000
    while e.clock.t < end:
        e.clock.t += 20
        e.poll()
        if e.clock.t % 40 == 0:
            w.monitor.frame()
        if e.clock.t % 250 < 20:
            w._tick()


def test_starts_disconnected_with_a_clear_prompt(win):
    win._tick()
    assert win.monitor.overlay.isVisible()
    assert win.monitor.overlay.title.text() == "No signal source"
    assert "Not connected" in win.strip.msg.text()
    win.monitor.overlay.findChildren(type(win.monitor.overlay.choose))[2].click()  # Start simulator
    advance(win, 1)
    assert not win.monitor.overlay.isVisible()


def test_simulator_drives_waveforms_and_numbers(win):
    win.engine.connect("simulator")
    advance(win, 30)
    t = win.monitor.tiles
    assert 14 <= int(t["rr"].value) <= 16
    assert 36 <= int(t["etco2"].value) <= 39
    assert 460 <= int(t["vt"].value) <= 540
    assert "Simulator" in win.header.status.text()
    assert win.monitor.sweeps["co2"]._buf[~(win.monitor.sweeps["co2"]._buf != win.monitor.sweeps["co2"]._buf)].max() > 30
    assert win.monitor.trend.points.data.size >= 5
    win.grab()  # paints without errors


def test_test_alarm_shows_the_high_priority_banner(win):
    win.engine.test_alarm()
    win._tick()
    assert "TEST ALARM" in win.strip.msg.text()
    assert win.strip.pri.text() == "HIGH"
    assert win.header.pill.text() == "ALARM"


def test_co2_ramp_raises_etco2_high_and_acknowledge_silences(win):
    e = win.engine
    e.connect("simulator")
    e.set_sim(scenario="co2Ramp")
    for _ in range(120):
        advance(win, 1)
        if e.alarms.is_active("co2High"):
            break
    win._tick()
    assert "EtCO2 HIGH" in win.strip.msg.text()
    assert win.monitor.tiles["etco2"].alarm == "high"
    assert win.monitor.sweeps["co2"].in_alarm
    win.strip.action.click()  # ACKNOWLEDGE
    win._tick()
    assert win.header.pill.text().startswith("SILENCED")


def test_light_and_dark_themes(win):
    from PySide6.QtWidgets import QApplication

    from co2response.ui import theme

    win.engine.update_settings(theme="light")
    win._tick()
    assert theme.current is theme.LIGHT
    assert theme.LIGHT.bg in QApplication.instance().styleSheet()
    win.grab()
    win.engine.update_settings(theme="dark")
    win._tick()
    assert theme.current is theme.DARK


def test_manual_calibration_edit_preview_save(win):
    win.engine.connect("simulator")
    advance(win, 1)
    win.open_settings("calibration")
    card = win.settings_screen.pages["calibration"].cards["co2"]
    card.manual.gain.setText("4")
    card.manual.gain.editingFinished.emit()
    advance(win, 0.5)
    assert card.is_dirty and card.dirty.isVisible()
    assert "PREVIEW (UNSAVED)" in [t.k.text() for t in card.live.tiles.values()]
    card.save_btn.click()
    advance(win, 0.5)
    assert win.engine.channels["channels"]["co2"]["calibration"]["gain"] == 4
    assert not card.is_dirty
    assert "saved" in card.meta.text()


def test_two_point_capture_computes_a_line_from_live_readings(win):
    win.engine.connect("simulator")
    advance(win, 1)
    win.open_settings("calibration")
    card = win.settings_screen.pages["calibration"].cards["flow"]
    card.group.button(1).click()
    zs = card.zerospan
    from co2response.ui.settings.calibration import CaptureButton

    zs.findChildren(CaptureButton)[0].click()
    advance(win, 2.2)
    assert zs.pts[0]["volts"] == zs.pts[0]["volts"]  # captured (not NaN)
    zs._set(1, "volts", 3.25)
    zs.compute.click()
    assert zs.result.text().startswith("Line through 2 points")
    assert card.is_dirty and card.draft["calibration"]["type"] == "linear"


def test_record_import_review(win, tmp_path):
    e = win.engine
    e.connect("simulator")
    advance(win, 2)
    win.toggle_recording()
    advance(win, 5)
    win.monitor.mark()
    advance(win, 20)
    win.toggle_recording()
    rec = e.library.list()[0]
    text = e.library.path(rec).read_text()
    assert text.startswith("# CO2-Response session,v3") and "MARK Event 1" in text and "RECORDING_STOPPED" in text

    win.open_settings("data")
    page = win.settings_screen.pages["data"]
    page.refresh(e.snapshot(), force=True)
    assert page.table.rowCount() == 1 and page.table.item(0, 0).text() == rec.file_name
    copy = tmp_path / "reimported.csv"
    copy.write_text(text)
    page.import_files([copy])
    page.refresh(e.snapshot(), force=True)
    assert page.table.rowCount() == 2
    names = [page.table.item(i, 0).text() for i in range(2)]
    assert "reimported.csv" in names

    bad = tmp_path / "people.csv"
    bad.write_text("name,age\nx,1\n")
    page.import_files([bad])
    assert "Could not import people.csv" in win.toaster.text.text()

    imported = next(r for r in e.library.list() if r.file_name == "reimported.csv")
    win.open_review(imported.id)
    rv = win.review
    assert rv.parsed is not None and rv.parsed.n == 2500
    assert any("MARK Event 1" in rv.events.item(i).text() for i in range(rv.events.count()))
    rv.set_zoom(type(rv.full)(5, 15))
    assert rv.whole_btn.isEnabled() and all(b.isEnabled() for b in rv.range_btns)
    win.grab()
    rv._replay()
    assert e.snapshot().source_kind == "replay"


def test_hardware_check_from_the_diagnostics_page(win):
    win.engine.connect("simulator")
    advance(win, 2)
    win.open_settings("diagnostics")
    hc = win.settings_screen.pages["diagnostics"].check
    hc.run_btn.click()
    advance(win, 11)
    assert hc.verdict.isVisible()
    labels = {hc.table.item(i, 1).text(): hc.table.cellWidget(i, 0).findChild(type(hc.verdict)).text() for i in range(hc.table.rowCount())}
    assert labels["Sample rate"] == "PASS" and labels["Dropped samples"] == "PASS"


def test_every_screen_paints_in_both_themes(win):
    e = win.engine
    e.connect("simulator")
    advance(win, 3)
    for pref in ("dark", "light"):
        e.update_settings(theme=pref)
        win._tick()
        win.show_monitor()
        win.monitor.show_drawer()
        win.grab()
        win.monitor.drawer.hide()
        win.monitor.trend.tabs.button(1).click()
        advance(win, 1)
        win.grab()
        for tab in ("connection", "calibration", "alarms", "display", "data", "diagnostics"):
            win.open_settings(tab)
            advance(win, 0.3)
            win.grab()


def test_sample_session_replays_through_the_window(win):
    from co2response.storage.session_file import read_session

    s = read_session(ROOT / "sample-data" / "synthetic_session.csv")
    assert win.engine.connect("replay", session=s, file_name="synthetic_session.csv", speed=10)
    advance(win, 16)
    assert win.engine.snapshot().status_detail == "Replay finished"
    assert len(win.engine.breaths) >= 30


def test_simulation_panel_sets_the_monitor_and_graphs_enlarge(win):
    from co2response.sim.controls import SLIDERS, slider_index

    m, e = win.monitor, win.engine
    win._tick()
    assert not m.sim.isVisible()
    m.k_sim.click()
    assert m.sim.isVisible() and m.sim.start_box.isVisible()  # the simulator is not running yet
    m.sim.sit_buttons["hypoventilation"].click()  # starts it with the situation's values
    advance(win, 45)
    assert e.snapshot().source_kind == "simulator"
    assert m.tiles["rr"].value == "7"
    assert 54 <= int(m.tiles["etco2"].value) <= 58
    assert e.alarms.is_active("co2High")
    assert m.sim.set_labels["rr"].text() == "7" and m.sim.mon_labels["rr"].text() == "7"
    assert m.sim.sit_buttons["hypoventilation"].isChecked() and not m.sim.start_box.isVisible()

    # a slider changes the running simulator; the monitor follows
    m.sim.sliders["rr"].slider.setValue(slider_index(SLIDERS["rr"], 20))
    assert e.settings.sim.rr == 20 and e.settings.sim.adjusted
    win._tick()
    assert "(adjusted)" in m.sim.current.text()
    advance(win, 25)
    assert m.tiles["rr"].value == "20"
    m.sim.switches["apnea"].setChecked(True)
    advance(win, 25)
    assert e.alarms.is_active("apnea")

    # enlarging a graph hides the others; Esc brings them back
    m.sweeps["co2"].expand.click()
    assert m.focus == "co2" and m.sweeps["co2"].isVisible()
    assert not m.sweeps["flow"].isVisible() and not m.trend.isVisible()
    win.grab()
    win._escape()
    assert m.focus is None and m.sweeps["flow"].isVisible() and m.trend.isVisible()
    m.trend.expand.click()
    assert m.focus == "trend" and not m.rows.isVisible() and m.trend.isVisible()
    m.trend.expand.click()
    assert m.focus is None and m.rows.isVisible()
    m.k_sim.click()
    assert not m.sim.isVisible()
    win.open_settings("connection")
    win.open_simulation()
    assert win.stack.currentWidget() is m and m.sim.isVisible()
