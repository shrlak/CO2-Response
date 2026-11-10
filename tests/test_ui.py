"""The desktop window, driven headless on a fake clock (QT_QPA_PLATFORM=offscreen).

These cover what a person does: start the simulator, read the numbers, see and
acknowledge alarms, calibrate, record, import, review, run the hardware check.
"""

import dataclasses

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
    e.update_settings(alarms=dataclasses.replace(e.settings.alarms, high_mm_hg=50, stop_mm_hg=None))
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
    assert text.startswith("# CO2-Response session,v4") and "MARK Event 1" in text and "RECORDING_STOPPED" in text

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
    advance(win, 19)
    assert win.engine.snapshot().status_detail == "Replay finished"
    assert len(win.engine.breaths) >= 30
    # the recording's markers are replayed, so the test is split and fitted as it was live
    assert [m.label for m in win.engine.markers] == ["Baseline", "CO2 rebreathing started", "End of test"]
    assert win.engine.snapshot().test.phase == "ended"


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
    assert m.tiles["rr"].value == "6"
    assert 60 <= int(m.tiles["etco2"].value) <= 66
    assert e.alarms.is_active("co2High")
    assert m.sim.set_labels["rr"].text() == "6" and m.sim.mon_labels["rr"].text() == "6"
    assert m.sim.sit_buttons["hypoventilation"].isChecked() and not m.sim.start_box.isVisible()

    # the speed slider runs simulated time faster; it is not a change to the situation
    m.sim.sliders["speed"].slider.setValue(slider_index(SLIDERS["speed"], 5))
    assert e.settings.sim.speed == 5 and not e.settings.sim.adjusted
    win._tick()
    assert "5× speed" in win.header.status.text()

    # a slider changes the running simulator; the monitor follows (at 5×, in a few seconds)
    m.sim.sliders["rr"].slider.setValue(slider_index(SLIDERS["rr"], 20))
    assert e.settings.sim.rr == 20 and e.settings.sim.adjusted
    win._tick()
    assert "(adjusted)" in m.sim.current.text()
    advance(win, 6)
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


def test_test_keys_run_a_rebreathing_test_and_a_click_takes_a_breath_out(win):
    m, e = win.monitor, win.engine
    e.connect("simulator")
    advance(win, 12)
    tr = m.trend
    assert tr.k_baseline.isEnabled() and tr.k_start.isEnabled() and not tr.k_end.isEnabled()
    assert "No test running" in tr.test_text.toolTip()
    tr.k_baseline.click()
    advance(win, 30)
    assert tr.test_text.toolTip().startswith("BASELINE 0:30")
    tr.k_start.click()
    e.set_sim(scenario="co2Ramp")
    advance(win, 60)
    assert tr.test_text.toolTip().startswith("REBREATHING 1:00") and tr.k_end.isEnabled() and not tr.k_start.isEnabled()
    tr.tabs.button(1).click()
    advance(win, 1)
    n_fit, n_skip = tr.resp_points.data.size, tr.resp_skip.data.size
    assert n_fit >= 5 and n_skip >= 5 and tr.resp_base.data.size >= 5
    # a click on a fitted breath takes it out of the fit; a click on its cross puts it back
    tr._point_clicked(tr.resp_points, tr.resp_points.points()[:1])
    advance(win, 0.3)
    assert tr.resp_out.data.size == 1 and "1 excluded by hand" in tr.meta.toolTip()
    tr._point_clicked(tr.resp_out, tr.resp_out.points()[:1])
    advance(win, 0.3)
    assert tr.resp_out.data.size == 0
    tr.k_end.click()
    advance(win, 2)
    assert tr.test_text.toolTip().startswith("TEST ENDED") and not tr.k_end.isEnabled()
    assert [mk.label for mk in e.markers] == ["Baseline", "Rebreathing", "End of test"]
    win.grab()


def test_trend_tab_plots_mv_and_rr_and_shows_both_co2_limits(win):
    m, e = win.monitor, win.engine
    e.connect("simulator")
    advance(win, 30)
    tr = m.trend
    assert tr.stop_line.isVisible() and tr.stop_line.value() == 65 and tr.high_line.value() == 60
    assert m.tiles["etco2"].limits[0] == "60 / 65"
    for series, text, lo, hi in (("mv", "MV TREND", 6.5, 8.5), ("rr", "RR TREND", 14, 16)):
        e.update_settings(trend_series=series)
        advance(win, 1)
        assert tr.tabs.button(0).text() == text
        assert not tr.high_line.isVisible() and not tr.stop_line.isVisible()
        ys = tr.points.getData()[1]
        assert len(ys) >= 4 and lo < ys[-1] < hi
        win.grab()
    e.update_settings(trend_series="etco2")
    advance(win, 1)
    assert tr.tabs.button(0).text() == "ETCO2 TREND" and tr.high_line.isVisible()


def test_settings_forms_come_from_the_shared_field_lists(win):
    from co2response.forms import ALARM_FIELDS, PROCESSING_FIELDS, TEST_FIELDS

    e = win.engine
    win.open_settings("alarms")
    form = win.settings_screen.pages["alarms"].form
    assert set(form.controls) == {f.name for f in ALARM_FIELDS}
    # the stop limit: a switch, and its number while it is on
    form.switches["stop"].setChecked(False)
    assert e.settings.alarms.stop_mm_hg is None and not form.controls["stop"].isVisibleTo(form)
    form.switches["stop"].setChecked(True)
    assert e.settings.alarms.stop_mm_hg == 65
    form.controls["stop"].setText("62")
    form.controls["stop"]._commit()
    assert e.settings.alarms.stop_mm_hg == 62
    form.controls["high"].setText("64")
    form.controls["high"]._commit()
    assert (e.settings.alarms.high_mm_hg, e.settings.alarms.stop_mm_hg) == (64, 64)  # stop is never below the warning

    win.open_settings("display")
    proc = win.settings_screen.pages["display"].proc_form
    assert set(proc.controls) == {f.name for f in PROCESSING_FIELDS}
    proc.controls["deadband"].setText("0.1")  # shown in L/s, stored in L/min
    proc.controls["deadband"]._commit()
    assert e.settings.processing.deadband_lpm == pytest.approx(6)
    proc.controls["btps"].setChecked(True)
    assert e.settings.processing.volume_btps and e.pipeline.flow_factor > 1.05
    proc.controls["co2delay"].setText("800")
    proc.controls["co2delay"]._commit()
    assert e.pipeline.co2_delay_sec == pytest.approx(0.8)

    win.open_settings("data")
    page = win.settings_screen.pages["data"]
    assert set(page.test_form.controls) == {f.name for f in TEST_FIELDS}
    page.test_form.controls["weight"].setText("61.5")
    page.test_form.controls["weight"]._commit()
    assert e.settings.test.weight_kg == 61.5
    page.test_form.controls["weight"].setText("")
    page.test_form.controls["weight"]._commit()
    assert e.settings.test.weight_kg is None
    advance(win, 0.3)
    win.grab()


def test_review_shows_the_response_and_saves_the_report(win, tmp_path, monkeypatch):
    import pypdf
    from PySide6.QtWidgets import QFileDialog

    e = win.engine
    rec, _ = e.library.import_file(ROOT / "sample-data" / "synthetic_session.csv")
    e.library_changed()
    win.open_review(rec.id)
    rv = win.review
    from co2response.ui.widgets import Stat

    stats = {st.k.text(): st.v.text() for st in rv.findChildren(Stat)}
    assert stats["CO2 RESPONSE"].startswith("slope 0.29 L/min/mmHg, r 1.00") and stats["BASELINE"].startswith("EtCO2 38.0 mmHg")
    out = tmp_path / "report.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    from co2response.ui.settings.data import export_report

    export_report(win, rec.file_name, rv.parsed)
    pdf = pypdf.PdfReader(str(out))
    text = pdf.pages[0].extract_text()
    assert len(pdf.pages) == 2 and "Response slope" in text and "RESEARCH USE ONLY" in text


def test_a_long_recording_opens_in_steps_while_the_monitor_keeps_running(win, qtbot, monkeypatch):
    e = win.engine
    rec, _ = e.library.import_file(ROOT / "sample-data" / "synthetic_session.csv")
    monkeypatch.setattr(type(e.library), "BIG_SESSION_BYTES", 100_000)  # treat the sample as a long recording
    polls = []
    win.poll_timer.timeout.connect(lambda: polls.append(1))
    win.poll_timer.start()  # the monitor's own 20 ms timer
    win.open_review(rec.id)
    rv = win.review
    assert rv.parsed is None and polls == []  # not parsed in one go
    qtbot.waitUntil(lambda: rv.parsed is not None, timeout=30_000)
    win.poll_timer.stop()
    assert rv.parsed.n > 17900 and len(rv.plots) == 3 and not rv.opening.isVisible()
    assert len(polls) >= 1  # the monitor was polled while the file was being read
    # a file that has gone shows why instead of a blank screen
    e.library.path(rec).unlink()
    win.open_review(rec.id)
    qtbot.wait(300)
    assert win.review.parsed is None and not hasattr(win.review, "plots")
