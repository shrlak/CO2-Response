"""The test report: the response worked out from a session file, and the PDF."""

import io
import json
import math

import pytest
from conftest import ROOT

from co2response.storage.pdf import Pdf, text_width
from co2response.storage.report import build_report, report_pdf, report_text, response_stats, result_lines, session_response
from co2response.storage.session_file import parse_session_text, read_session

SAMPLE = ROOT / "sample-data" / "synthetic_session.csv"


@pytest.fixture(scope="module")
def sample():
    return read_session(SAMPLE)


def edited(extra_events: str = "", **header) -> str:
    """The sample session with events added to its last row and header values changed."""
    text = SAMPLE.read_text()
    for old, new in header.items():
        assert old in text
        text = text.replace(old, new)
    return text.replace("RECORDING_STOPPED", f"{extra_events}RECORDING_STOPPED") if extra_events else text


def test_response_of_the_sample_session(sample):
    res = session_response(sample)
    # the sample: Baseline at 20 s, rebreathing at 60 s, End of test at 170 s, 30 s left out
    assert res.window.baseline == pytest.approx(20.03) and res.window.start == pytest.approx(60.03) and res.window.end == pytest.approx(170.03)
    assert res.skip_sec == 30 and res.split
    assert 8 <= len(res.baseline) <= 11 and 37 < res.baseline_etco2 < 39 and 7 < res.baseline_mv < 8
    assert len(res.rebreathing) >= 15 and len(res.skipped) >= 8 and res.excluded == []
    assert all(60.03 + 30 <= p.t < 170.03 for p in res.rebreathing)
    assert 0.2 < res.slope < 0.4 and res.r > 0.99
    # the two partial breaths the noise screen rejected are nowhere in the analysis
    rejected = {b.n for b in sample.breaths if b.quality == "artifact"}
    assert len(rejected) == 2 and not rejected & {p.key for p in res.baseline + res.rebreathing + res.skipped}
    assert response_stats(res)[0][1].startswith("slope 0.29 L/min/mmHg, r 1.00")
    # no skip: the breaths in the first 30 s join the fit
    assert len(session_response(sample, skip_sec=0).rebreathing) > len(res.rebreathing)


def test_report_matches_the_analysis_loader(sample):
    """The app's report and the pandas loader are separate code; they must agree."""
    pytest.importorskip("pandas")
    import sys

    sys.path.insert(0, str(ROOT / "python"))
    from co2r.session import co2_response, load_session

    theirs = co2_response(load_session(str(SAMPLE)))
    ours = session_response(sample)
    assert theirs["slope"] == pytest.approx(ours.slope, rel=1e-6) and theirs["r"] == pytest.approx(ours.r, rel=1e-6)
    assert theirs["x_intercept"] == pytest.approx(ours.x_intercept, rel=1e-6)
    assert (theirs["n"], theirs["baseline_n"], theirs["skipped_n"]) == (len(ours.rebreathing), len(ours.baseline), len(ours.skipped))
    assert theirs["baseline_mv_lpm"] == pytest.approx(ours.baseline_mv, rel=1e-6)


def test_report_lines_and_text(sample):
    r = build_report(sample, "co2r_SAMPLE.csv", app_version="9.9", generated_ms=1_793_854_800_000)
    lines = dict(result_lines(r))
    assert lines["Test phases"] == "baseline from 0:20; rebreathing from 1:00; ended at 2:50 (1:50 of rebreathing)"
    assert lines["Baseline"].startswith("EtCO2 38.0 mmHg, MV 7.4")
    assert lines["Response slope"].endswith("L/min per mmHg") and "n = " in lines["Correlation r"]
    assert "2 rejected by the noise screen" in lines["Breaths"] and "not fitted (first 30 s of rebreathing, including the 4 calibration breaths and after the end of the test)" in lines["Breaths"]
    assert lines["Alarms"].startswith("2 alarms")
    assert "Normalized slope" not in lines  # no height or weight in the sample
    used = [q.used for q in r.rows]
    assert used.count("rejected (artifact)") == 2 and used.count("fitted") == len(r.response.rebreathing) and "baseline" in used and "-" in used
    assert ("EtCO2 limits", "warning 60 mmHg, stop 65 mmHg") in r.setup
    text = report_text(r)
    assert text.startswith("CO2 response report: co2r_SAMPLE.csv\n") and "Response slope: 0.29" in text


def test_report_with_subject_size_and_breaths_excluded_by_hand(sample):
    fitted = [p.key for p in session_response(sample).rebreathing]
    a, b = int(fitted[0]), int(fitted[1])
    s = parse_session_text(edited(f"BREATH_EXCLUDED {a} manual | BREATH_EXCLUDED {b} manual | BREATH_INCLUDED {b} | ", **{'"heightCm":null,"weightKg":null,"ageYears":null': '"heightCm":170,"weightKg":60,"ageYears":23'}))
    r = build_report(s)
    assert [p.key for p in r.response.excluded] == [a] and len(r.response.rebreathing) == len(fitted) - 1
    lines = dict(result_lines(r))
    assert "1 excluded by hand" in lines["Breaths"]
    slope = r.response.slope
    assert lines["Normalized slope"] == f"{slope * 1000 / 60:.1f} mL/min/mmHg per kg; {slope / math.sqrt(170 * 60 / 3600):.3f} L/min/mmHg per m2 (BSA 1.68 m2)"
    assert r.subject == {"heightCm": 170, "weightKg": 60, "ageYears": 23}
    assert next(q.used for q in r.rows if q.n == a) == "excluded"


def test_report_of_a_session_with_no_markers_or_no_breaths():
    plain = parse_session_text("\n".join(line for line in edited().split("\n") if not line.endswith(("MARK Baseline", "MARK CO2 rebreathing started", "MARK End of test"))))
    lines = dict(result_lines(build_report(plain)))
    assert lines["Test phases"] == "no Rebreathing marker: every clean breath is fitted" and "Baseline" not in lines
    head = "\n".join(edited().split("\n")[:40])  # the header and a few samples: no breath yet
    empty = build_report(parse_session_text(head))
    assert dict(result_lines(empty))["Response slope"].startswith("not fitted: 0 breaths")
    assert report_pdf(empty).startswith(b"%PDF-1.4")  # still a valid, if short, report


def test_report_pdf_is_a_readable_document(sample):
    pypdf = pytest.importorskip("pypdf")
    data = report_pdf(build_report(sample, "co2r_SAMPLE.csv", app_version="9.9", generated_ms=1_793_854_800_000))
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    doc = pypdf.PdfReader(io.BytesIO(data))
    assert len(doc.pages) == 2 and doc.metadata.title == "CO2 response report SAMPLE"
    first = doc.pages[0].extract_text()
    for needle in ("Ventilatory response to CO2", "RESEARCH USE ONLY", "Response slope", "0.29", "Baseline", "EtCO2 (mmHg)", "CO2 rebreathing started", "How it was measured", "page 1 of 2", "CO2-Response 9.9"):
        assert needle in first, needle
    table = doc.pages[1].extract_text()
    assert "Every breath" in table and "rejected (artifact)" in table and "not fitted" in table and "page 2 of 2" in table
    # the same input gives the same bytes
    assert data == report_pdf(build_report(sample, "co2r_SAMPLE.csv", app_version="9.9", generated_ms=1_793_854_800_000))


def test_long_sessions_get_more_table_pages(sample):
    pypdf = pytest.importorskip("pypdf")
    r = build_report(sample)
    r.rows = r.rows * 4  # 192 breaths: 62 to a page
    assert len(pypdf.PdfReader(io.BytesIO(report_pdf(r))).pages) == 1 + 4


def test_pdf_writer_basics():
    pypdf = pytest.importorskip("pypdf")
    pdf = Pdf(title="T (1)", author="A\\B")
    pdf.text(72, 72, "Hello (world) ± 5 − 3 ≥ 2 · é", 12)
    pdf.text(300, 100, "right", 10, "bold", (1, 0, 0), "right")
    pdf.text(300, 120, "mono", 10, "mono", align="center")
    pdf.line(10, 10, 100, 100, 2, (0, 0, 1), (3, 2))
    pdf.rect(50, 50, 20, 10, fill=(0.5, 0.5, 0.5))
    pdf.circle(200, 200, 5, stroke=None, fill=(0, 1, 0))
    pdf.cross(220, 200, 4)
    pdf.page()
    pdf.text(72, 72, "second page")
    pdf.select(0)
    pdf.text(72, 700, "added to the first page afterwards")
    doc = pypdf.PdfReader(io.BytesIO(pdf.bytes()))
    assert len(doc.pages) == 2 and doc.metadata.title == "T (1)" and doc.metadata.author == "A\\B"
    first = doc.pages[0].extract_text()
    assert "Hello (world) ± 5 - 3 >= 2 · é" in first and "added to the first page afterwards" in first
    assert "second page" in doc.pages[1].extract_text()
    assert text_width("0000", 10, "mono") == 24 and 20 < text_width("0000", 10) < 24 < text_width("0000", 10, "bold") + 2
    assert Pdf().bytes().startswith(b"%PDF-1.4")  # an empty document is still a document


def test_report_from_the_command_line(tmp_path, capsys):
    from co2response.__main__ import main

    out = tmp_path / "r.pdf"
    assert main(["--report", str(SAMPLE), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Response slope: 0.29" in printed and f"wrote {out}" in printed
    assert out.read_bytes().startswith(b"%PDF-1.4")
    # the default name is next to the session file; --skip changes the fit
    copy = tmp_path / "s.csv"
    copy.write_text(SAMPLE.read_text())
    assert main(["--report", str(copy), "--skip", "0"]) == 0
    assert (tmp_path / "s.pdf").exists() and "not fitted (4 calibration breaths at the start of rebreathing and after the end of the test)" in capsys.readouterr().out
    bad = tmp_path / "people.csv"
    bad.write_text("name,age\nx,1\n")
    assert main(["--report", str(bad)]) == 1
    assert "Could not read" in capsys.readouterr().err
    assert main(["--list-ports"]) == 0


def test_header_of_a_new_recording_says_how_the_test_is_analysed(sample):
    assert json.loads(sample.meta["test"]) == {"responseSkipSec": 30, "calibrationBreaths": 4, "calibrationSec": 15, "heightCm": None, "weightKg": None, "ageYears": None}
    proc = json.loads(sample.meta["processing"])
    assert (proc["co2DelayMs"], proc["volumeBtps"], proc["rebreathingGasFactor"]) == (0, False, 1)
