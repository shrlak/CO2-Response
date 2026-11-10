"""Importing a LabChart (PowerLab) text export.

There is no real CHOP export to test against, so these build one from the sample
session in LabChart's documented text layout and check the round trip.
"""

import math

import numpy as np
import pytest
from conftest import ROOT

from co2response.storage.labchart import labchart_to_session, looks_like_labchart, parse_labchart, pick_channels
from co2response.storage.library import ChunkedSessionLibrary, SessionLibrary
from co2response.storage.report import session_response
from co2response.storage.session_file import clean_breaths, parse_session_text, read_session, session_markers


@pytest.fixture(scope="module")
def sample():
    return read_session(ROOT / "sample-data" / "synthetic_session.csv")


def export(s, flow_unit="mL/s", flip=True, co2_unit="mmHg", rate_factor=1, sat=False, blocks=1, comments=True) -> str:
    """The sample session as a LabChart text export: flow with inspiration NEGATIVE (so
    the importer has to work the sign out), CO2 in mmHg, comments at the markers."""
    f = {"mL/s": 1000 / 60, "L/s": 1 / 60, "L/min": 1.0}[flow_unit] * (-1 if flip else 1)
    c = {"mmHg": 1.0, "%": 100 / 760, "kPa": 1 / 7.50062}[co2_unit]
    marks = dict((round(t, 2), label) for t, label in session_markers(s.events)) if comments else {}
    titles = ["Flow", "CO2"] + (["SAT"] if sat else []) + ["TcCO2"]
    units = [flow_unit, co2_unit] + (["%"] if sat else []) + ["mmHg"]
    head = [
        f"Interval=\t{0.01 / rate_factor:g} s",
        "ExcelDateTime=\t4.63312083333e+004\t11/05/26 05:00:00.000",
        "TimeFormat=\tStartOfBlock",
        "DateFormat=\t",
        "ChannelTitle=\t" + "\t".join(titles),
        "Range=\t" + "\t".join(["10.000 V"] * len(titles)),
        "UnitName=\t" + "\t".join(units),
    ]
    out = list(head)
    n = s.n
    per_block = math.ceil(n / blocks)
    t0 = 0.0
    for i in range(n):
        if i and i % per_block == 0:
            out += head  # a new block: its header again, and its clock starts from 0
            t0 = float(s.time_s[i])
        flow = float(s.pneumo_lpm[i])
        co2 = float(s.co2_mmhg[i])
        for r in range(rate_factor):
            t = float(s.time_s[i]) - t0 + r * 0.01 / rate_factor
            row = [f"{t:.4f}", f"{flow * f:.3f}", f"{co2 * c:.3f}"] + (["97.5"] if sat else []) + ["41.0"]
            label = marks.pop(round(float(s.time_s[i]), 2), None) if r == 0 else None
            out.append("\t".join(row) + (f"\t#* {label}" if label else ""))
    return "\r\n".join(out) + "\r\n"


def test_recognises_and_parses_a_labchart_export(sample):
    text = export(sample, sat=True)
    assert looks_like_labchart(text) and not looks_like_labchart((ROOT / "sample-data" / "synthetic_session.csv").read_text()[:4000])
    d = parse_labchart(text)
    assert d.interval_s == 0.01 and d.titles == ["Flow", "CO2", "SAT", "TcCO2"] and d.units == ["mL/s", "mmHg", "%", "mmHg"]
    assert len(d.t) == sample.n and d.t[0] == 0 and d.t[-1] == pytest.approx(sample.time_s[-1] - sample.time_s[0], abs=0.01)
    assert [label for _, label in d.comments] == ["Baseline", "CO2 rebreathing started", "End of test"]
    # the airway CO2, not the transcutaneous one; SAT is the oximeter
    assert pick_channels(d) == {"flow": 0, "co2": 1, "spo2": 2}
    assert abs(d.started_ms - 1_793_854_800_000) < 2000  # 2026-11-05 05:00 UTC, from the Excel date


def test_an_imported_export_gives_the_same_breaths_and_response(sample):
    text = labchart_to_session(export(sample), "patient-x.txt", "9.9")
    s = parse_session_text(text)
    assert s.version == 4 and s.study_code == "patient-x" and "LabChart import (Flow [mL/s], CO2 [mmHg]; 100 Hz)" in s.meta["source"]
    assert [label for _, label in session_markers(s.events)] == ["Baseline", "CO2 rebreathing started", "End of test"]
    ours, theirs = clean_breaths(s), clean_breaths(sample)
    assert abs(len(ours) - len(theirs)) <= 2
    # the same breaths, measured again from the exported (already calibrated) signals
    for a, b in zip(ours[5:15], theirs[5:15]):
        assert a.etco2 == pytest.approx(b.etco2, abs=0.3) and a.vt == pytest.approx(b.vt, rel=0.03) and a.period == pytest.approx(b.period, abs=0.05)
    r0, r1 = session_response(sample, skip_sec=30), session_response(s, skip_sec=30)
    assert r1.slope == pytest.approx(r0.slope, rel=0.05) and r1.baseline_mv == pytest.approx(r0.baseline_mv, rel=0.03)
    assert r1.window.start == pytest.approx(r0.window.start - float(sample.time_s[0]), abs=0.1)
    # the CO2 passes through unchanged, and inspiration came out positive although it was exported negative
    i = 5000
    assert float(s.co2_mmhg[i]) == pytest.approx(float(sample.co2_mmhg[i + 3]), abs=0.6)
    assert np.corrcoef(s.pneumo_lpm[100:5000], sample.pneumo_lpm[100:5000])[0, 1] > 0.95


@pytest.mark.parametrize("kw", [{"flow_unit": "L/s"}, {"flow_unit": "L/min", "flip": False}, {"co2_unit": "%"}, {"co2_unit": "kPa"}, {"blocks": 3}], ids=str)
def test_units_signs_and_blocks(sample, kw):
    s = parse_session_text(labchart_to_session(export(sample, comments=False, **kw)))
    good = clean_breaths(s)
    ref = clean_breaths(sample)
    assert abs(len(good) - len(ref)) <= 4
    assert good[8].etco2 == pytest.approx(ref[8].etco2, abs=0.5) and good[8].vt == pytest.approx(ref[8].vt, rel=0.04)
    assert s.duration_s == pytest.approx(sample.duration_s - float(sample.time_s[0]), abs=0.2)


def test_a_fast_recording_is_averaged_down_and_an_oximeter_channel_is_kept(sample):
    s = parse_session_text(labchart_to_session(export(sample, rate_factor=4, sat=True)))  # 400 Hz
    assert "400 Hz averaged to 100 Hz" in s.meta["source"] and s.meta["rate_hz"] == "100"
    assert abs(s.n - sample.n) <= 4 and clean_breaths(s)[8].vt == pytest.approx(clean_breaths(sample)[8].vt, rel=0.04)
    assert s.has_spo2 and float(np.nanmin(s.spo2)) == pytest.approx(97.5, abs=0.1) and '"enabled":true' in s.meta["spo2"]


def test_an_export_without_flow_or_co2_says_what_it_found():
    text = "Interval=\t0.01 s\nChannelTitle=\tECG\tSAT\nUnitName=\tmV\t%\n0\t0.1\t98\n0.01\t0.2\t98\n"
    with pytest.raises(ValueError, match=r"needs a flow channel .* and an airway CO2 channel .* Channels found: ECG \(mV\), SAT \(%\)"):
        labchart_to_session(text)
    with pytest.raises(ValueError, match="no data rows"):
        labchart_to_session("Interval=\t0.01 s\nChannelTitle=\tFlow\tCO2\nUnitName=\tmL/s\tmmHg\n")


@pytest.mark.parametrize("cls", [SessionLibrary, ChunkedSessionLibrary])
def test_the_library_imports_a_labchart_export_as_a_session(tmp_path, sample, cls):
    src = tmp_path / "Patient X.txt"
    src.write_text(export(sample), encoding="utf-8")
    lib = cls(tmp_path / "lib")
    rec, parsed = lib.import_file(src)
    assert rec.file_name == "Patient X.csv" and rec.status == "imported" and rec.source.startswith("Imported · LabChart import")
    assert rec.stats["breaths"] >= 40 and rec.stats["markers"] == 3
    again = lib.parse(rec.id)
    assert again.n == parsed.n and session_response(again).fit is not None
    # an ordinary text file is still refused
    junk = tmp_path / "notes.txt"
    junk.write_text("hello\n")
    with pytest.raises(ValueError):
        lib.import_file(junk)
    assert len(lib.list()) == 1
