"""The Calibration screen's rules (``co2response/calibration_draft.py``), which the
desktop window and the browser page both draw."""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest

from co2response import calibration_draft as cd
from co2response.calibration_draft import CalDraft
from co2response.channels import DEFAULT_CHANNELS, validate_channels
from co2response.processing.calibration import apply_calibration
from co2response.settings import Settings


def draft(cid: str = "co2") -> CalDraft:
    return CalDraft(cid, DEFAULT_CHANNELS["channels"][cid])


class Engine:
    """As much of the monitor engine as saving a calibration uses."""

    def __init__(self) -> None:
        self.channels = copy.deepcopy(DEFAULT_CHANNELS)

    def set_channels(self, channels: dict) -> None:
        self.channels = validate_channels(channels)


def test_both_front_ends_draw_the_same_model():
    from pathlib import Path

    root = Path(cd.__file__).parent
    for front_end in ("ui/settings/calibration.py", "web/settings_view.py"):
        text = (root / front_end).read_text(encoding="utf-8")
        assert "calibration_draft" in text
        # the rules live in one place: neither screen fits, converts or integrates by itself
        for own_rule in ("fit_polynomial", "two_point_linear", "syringe_k1", "apply_calibration"):
            assert own_rule not in text, f"{front_end} has its own {own_rule}"


def test_a_new_draft_is_clean_and_an_edit_makes_it_dirty_until_saved_or_discarded():
    d = draft()
    assert not d.dirty and d.unit == "%"
    d.set_number("gain", 4)
    assert d.dirty and d.draft["calibration"]["gain"] == 4
    d.discard()
    assert not d.dirty
    d.set_number("gain", 4)
    e = Engine()
    assert d.save(e) == "CO2 (capnograph) calibration saved."
    assert not d.dirty and e.channels["channels"]["co2"]["calibration"]["gain"] == 4
    assert e.channels["channels"]["co2"]["calibratedAt"].endswith("Z")
    assert d.meta(lambda iso: "today").endswith("saved today") and d.note() == ""


def test_a_calibration_that_is_not_valid_is_refused_and_stays_a_draft():
    d = draft()
    d.update(calibration={"type": "table", "points": [[1, 0]]})  # one point is not a table
    e = Engine()
    with pytest.raises(ValueError, match="at least 2 points"):
        d.save(e)
    assert d.dirty and e.channels == DEFAULT_CHANNELS


def test_the_draft_follows_what_is_saved_elsewhere():
    d = draft("flow")
    d.set_zero(0.5)
    other = copy.deepcopy(DEFAULT_CHANNELS["channels"]["flow"])
    other["zeroOffset"] = 2.0  # imported, or reset
    assert d.follow(other) and d.draft["zeroOffset"] == 2.0 and not d.dirty
    assert not d.follow(other)


def test_defaults_keep_the_adc_input_the_channel_is_wired_to():
    d = draft("flow")
    d.update(index=3, zeroOffset=1.0)
    assert d.defaults() == cd.DEFAULTS_LOADED
    assert d.draft["index"] == 3 and d.draft["zeroOffset"] == DEFAULT_CHANNELS["channels"]["flow"]["zeroOffset"]


def test_changing_the_kind_of_calibration_keeps_the_curve():
    d = draft()
    d.update(calibration={"type": "linear", "gain": 2.5, "offset": -0.5})
    assert not d.switch_type("linear")
    for kind in ("polynomial", "table", "linear"):
        assert d.switch_type(kind) and d.draft["calibration"]["type"] == kind
        for v in (0, 1.3, 4.2):
            assert apply_calibration(d.draft["calibration"], v) == pytest.approx(2.5 * v - 0.5)


def test_polynomial_coefficients_are_typed_as_a_list():
    d = draft()
    d.switch_type("polynomial")
    d.set_coeffs("0.1, 2  0.03")
    assert d.draft["calibration"]["coeffs"] == [0.1, 2, 0.03] and d.coeffs_text() == "0.1, 2, 0.03"
    for bad in ("", "1, two", "nan"):
        with pytest.raises(ValueError, match="lowest order first"):
            d.set_coeffs(bad)
    assert d.draft["calibration"]["coeffs"] == [0.1, 2, 0.03]


def test_lookup_table_rows():
    d = draft()
    d.switch_type("table")
    n = len(d.draft["calibration"]["points"])
    d.add_point()
    d.set_point(n, 1, 9.0)
    d.capture_point(0, 0.123456)
    pts = d.draft["calibration"]["points"]
    assert len(pts) == n + 1 and pts[n][1] == 9.0 and pts[0][0] == 0.1235
    d.set_point(99, 0, 1.0)  # a capture that finishes after its row was removed
    assert d.draft["calibration"]["points"] == pts
    while d.can_remove_point():
        d.remove_point(0)
    assert len(d.draft["calibration"]["points"]) == 2


def test_zero_now_makes_the_channel_read_its_zero_reference():
    d = draft()  # room air is 0.04 % CO2
    said = d.zero_now(0.5)
    assert said.startswith("Zero offset set to") and said.endswith("Save to apply.")
    assert apply_calibration(d.draft["calibration"], 0.5) - d.draft["zeroOffset"] == pytest.approx(0.04)
    f = draft("flow")
    f.zero_now(0.7)
    assert apply_calibration(f.draft["calibration"], 0.7) - f.draft["zeroOffset"] == pytest.approx(0)


def test_zero_and_span_puts_a_line_through_the_two_points():
    d = draft()
    assert [ok for _, _, ok in d.fits("zerospan")] == [False]
    assert d.capture_ref("zerospan", 0, 0.50004) == 0.5
    d.set_ref("zerospan", 1, "volts", 3.0)
    d.set_ref("zerospan", 1, "ref", 5.04)
    assert d.fits("zerospan") == [(1, "Compute line", True)]
    said = d.fit("zerospan", 1)
    assert said.startswith("Line through 2 points: gain 2 %/V") and said == d.result["zerospan"]
    cal = d.draft["calibration"]
    assert cal["type"] == "linear" and cal["gain"] == pytest.approx(2) and d.draft["zeroOffset"] == 0
    assert apply_calibration(cal, 0.5) == pytest.approx(0.04) and apply_calibration(cal, 3.0) == pytest.approx(5.04)
    assert d.point_name("zerospan", 0) == "A · Room air"


def test_two_readings_at_the_same_voltage_cannot_be_fitted():
    d = draft("flow")
    d.set_ref("zerospan", 0, "volts", 1.0)
    d.set_ref("zerospan", 1, "volts", 1.0)
    d.result["zerospan"] = "an earlier fit"
    before = copy.deepcopy(d.draft)
    with pytest.raises(ValueError, match="same voltage"):
        d.fit("zerospan", "1")  # the browser sends the degree as text
    assert d.result["zerospan"] == "" and d.draft == before


def test_multi_point_fit_line_quadratic_and_table():
    d = draft("pneumo")
    d.add_ref()
    assert len(d.pts["fit"]) == 4 and d.can_remove_ref("fit") and not d.can_remove_ref("zerospan")
    for i, v in enumerate((0.5, 1.0, 2.0, 3.0)):
        d.set_ref("fit", i, "volts", v)
        d.set_ref("fit", i, "ref", 10 + 20 * v + 3 * v * v)
    assert [ok for _, _, ok in d.fits("fit")] == [True, True, True]
    assert d.fit("fit", 2).startswith("Quadratic fit through 4 points")
    assert d.draft["calibration"]["coeffs"] == pytest.approx([10, 20, 3], abs=1e-4)
    assert d.fit("fit", 1).startswith("Least-squares line through 4 points")
    assert "Largest error at the reference points" in d.result["fit"]
    assert d.fit("fit", "table").startswith("Lookup table from 4 points")
    assert [p[0] for p in d.draft["calibration"]["points"]] == [0.5, 1.0, 2.0, 3.0]
    d.set_ref("fit", 3, "volts", None)  # cleared: the point no longer counts
    d.remove_ref(0)
    assert len(d.ready("fit")) == 2 and [ok for _, _, ok in d.fits("fit")] == [True, False, True]


def test_a_syringe_stroke_gives_the_k1_that_reads_the_syringe_volume():
    d = draft("pneumo")
    listeners = []

    def on_sample(fn):
        listeners.append(fn)
        return lambda: listeners.remove(fn)

    assert not d.stroke_stop()  # nothing started
    d.stroke_start(on_sample)
    assert d.stroke_running and d.stroke_live().startswith("Integrating")
    idx = int(d.draft["index"])
    cal = d.draft["calibration"]
    # 3 L pushed in 1.5 s at a steady 120 L/min through a pneumotach whose true k1 is 0.3
    pa = 120 / 0.3
    volts_for = next(v / 1000 for v in range(0, 5000) if apply_calibration(cal, v / 1000) - d.draft["zeroOffset"] >= pa)
    for n in range(151):
        volts = [0.0, 0.0, 0.0]
        volts[idx] = volts_for
        listeners[0](SimpleNamespace(volts=volts, t_sec=n * 0.01))
    assert d.stroke_stop() and not d.stroke_running and not listeners
    a1, _, secs = d.stroke
    assert secs == pytest.approx(1.5) and a1 == pytest.approx(pa * 1.5, rel=0.01)
    k1, why = d.new_k1()
    assert why == "" and k1 == pytest.approx(0.3, rel=0.01) and d.can_apply_k1()
    assert [k for k, _ in d.stroke_stats()] == ["∫ΔP dt", "Stroke time", "Volume with current k1", "New k1"]
    assert d.apply_k1_text().startswith("Apply k1 = 0.3")
    assert "applied to the draft" in d.apply_k1() and d.flow_model()["k1"] == pytest.approx(0.3, rel=0.01) and d.dirty
    d.set_volume(50)
    assert d.volume == 10  # a syringe is 0.1 to 10 L


def test_a_stroke_with_no_pressure_has_no_k1():
    d = draft("pneumo")
    d.stroke_start(lambda fn: lambda: None)
    assert not d.stroke_stop() and d.stroke is None
    assert math.isnan(d.new_k1()[0]) and not d.can_apply_k1() and d.apply_k1() == "" and d.stroke_stats() == []


def test_live_readings_preview_the_draft():
    s = SimpleNamespace(numbers=SimpleNamespace(volts=[1.0, 2.0, 3.0]), settings=Settings())
    d = draft()
    raw, value, third = d.live(s)
    assert raw == ("Raw", "1.0000 V") and value[0] == "Calibrated" and third[0] == "As mmHg" and third[1].endswith("mmHg")
    d.set_number("gain", d.draft["calibration"]["gain"] * 2)
    assert d.live(s)[1][0] == "Preview (unsaved)" and d.live(s)[1][1] != value[1]
    assert draft("pneumo").live(s)[2][0] == "Flow" and draft("flow").live(s)[2][0] == "Saved calibration"
    nothing = SimpleNamespace(numbers=SimpleNamespace(volts=[]), settings=Settings())
    assert d.live(nothing)[0] == ("Raw", "-- V") and d.live(nothing)[1][1].startswith("--")


def test_what_the_screens_offer_for_each_channel():
    assert [m for m, _ in cd.methods("co2")] == ["manual", "zerospan", "fit"]
    assert cd.methods("co2")[1][1] == "Zero & span" and cd.methods("flow")[1][1] == "Two-point"
    assert cd.methods("pneumo")[-1] == ("syringe", "3 L syringe")
    assert "room air" in cd.zero_button("co2") and "no flow" in cd.zero_button("pneumo")
    for cid in cd.NAMES:
        assert len(cd.zerospan_steps(cid)) == 3
    assert cd.export_name().startswith("co2r-calibration-") and '"exportedAt"' in cd.export_text(DEFAULT_CHANNELS)
