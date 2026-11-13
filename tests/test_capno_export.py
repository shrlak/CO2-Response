"""Adding a Capnostream 35's exported data to an Arduino recording (the offline path).

There is no real Capnostream export to test against, so these build exports from the
sample session in several plausible layouts (waveform and trend, CSV and tab separated,
elapsed and clock times, mmHg and %) and check that the merge puts them back together.
"""

import copy

import numpy as np
import pytest
from conftest import ROOT

from co2response.storage.capno_export import merge_capnostream_export, parse_capnostream_export
from co2response.storage.library import ChunkedSessionLibrary, SessionLibrary
from co2response.storage.report import session_response
from co2response.storage.session_file import clean_breaths, parse_session_text, read_session, session_markers, write_session

NAN = float("nan")


@pytest.fixture(scope="module")
def sample():
    return read_session(ROOT / "sample-data" / "synthetic_session.csv")


def waveform_export(s, shift=7.3, *, unit="mmHg", layout="csv", clock=False, preamble=True) -> str:
    """The sample's CO2 as a 20 Hz Capnostream export that starts ``shift`` seconds before
    the sample's flow (clock difference plus the sample line's 2.7 s)."""
    tc = np.arange(0, float(s.time_s[-1]) + shift + 5, 0.05)
    co2 = np.interp(tc - shift, s.time_s, s.co2_mmhg.astype(float), left=NAN, right=NAN)
    k = {"mmHg": 1.0, "%": 100 / 760, "kPa": 1 / 7.50062}[unit]
    sep = "," if layout == "csv" else "\t"
    head = ["Time" if clock else "Time (s)", f"CO2 ({unit})", f"EtCO2 ({unit})", "RR (bpm)", "SpO2 (%)"]
    out = []
    if preamble:  # lines above the header, with something that must not be copied
        out += [f"Patient ID{sep}TEST-ID-123", f"Exported{sep}2026-11-05"]
    out.append(sep.join(head))
    for t, v in zip(tc, co2):
        if clock:
            sec = (23 * 3600 + 59 * 60 + 30 + t) % 86400  # runs past midnight
            tt = f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:06.3f}"
        else:
            tt = f"{t:.2f}"
        cell = "---" if not np.isfinite(v) else f"{v * k:.2f}"
        out.append(sep.join([tt, cell, f"{38 * k:.1f}", "15", "98"]))
    return "\n".join(out) + "\n"


def trend_export(s, lag=2.7, offset=0.0) -> str:
    """The sample's per-breath EtCO2 as a 1 Hz trend: each value appears 0.4 s after the
    line delay, and the monitor started ``offset`` seconds before the flow."""
    rows = ["Time,EtCO2 (mmHg),RR (bpm),SpO2 (%),Pulse (bpm)"]
    shown = [(b.time_s + lag + 0.4 + offset, b.etco2) for b in s.breaths]
    for t in range(0, int(s.time_s[-1] + offset + lag + 5)):
        cur = [v for tt, v in shown if tt <= t]
        rows.append((f"{t},{cur[-1]:.0f}" if cur else f"{t},---") + ",15,98,72")
    return "\n".join(rows) + "\n"


def close_to_sample(m, sample, n=10):
    """The merged session's breaths are the sample's breaths (matched by time)."""
    ours, theirs = clean_breaths(m), clean_breaths(sample)
    assert abs(len(ours) - len(theirs)) <= 3 and len(ours) >= 40
    done = 0
    for b in theirs[5:]:
        a = min(ours, key=lambda x: abs(x.time_s - b.time_s))
        if abs(a.time_s - b.time_s) > 0.3:
            continue
        assert a.etco2 == pytest.approx(b.etco2, abs=0.6) and a.vt == pytest.approx(b.vt, rel=0.03)
        done += 1
        if done == n:
            break
    assert done == n
    return ours


def test_reads_a_waveform_export_and_skips_what_is_above_the_header(sample):
    e = parse_capnostream_export(waveform_export(sample))
    assert e.kind == "waveform" and e.rate_hz == pytest.approx(20, abs=0.1)
    assert e.columns["co2"] == "CO2" and e.t[0] == 0 and len(e.rr_t) == len(e.t)
    assert np.nanmax(e.co2) == pytest.approx(66.9, abs=0.5)  # mmHg, as written


@pytest.mark.parametrize("kw", [{}, {"layout": "tab"}, {"unit": "%"}, {"unit": "kPa", "layout": "tab"}, {"clock": True}, {"preamble": False}], ids=str)
def test_the_merge_finds_the_shift_and_gives_the_same_breaths(sample, kw):
    # the monitor started 4.6 s before the Arduino: the hint that settles which breath is which
    res = merge_capnostream_export(sample, waveform_export(sample, **kw), start_offset_s=4.6)
    assert res.kind == "waveform" and res.method == "waveform" and res.r > 0.5
    assert res.shift_s == pytest.approx(7.3, abs=0.75) and res.coverage > 0.95  # the CO2 rise lags the expiration a little
    assert "TEST-ID-123" not in res.text  # nothing from above the header is copied
    m = parse_session_text(res.text)
    assert m.version == 4 and m.meta["capnostream_export"] == "waveform" and "Capnostream 35 export" in m.meta["source"]
    assert [label for _, label in session_markers(m.events)] == ["Baseline", "CO2 rebreathing started", "End of test"]
    close_to_sample(m, sample)
    r0, r1 = session_response(sample, skip_sec=30), session_response(m, skip_sec=30)
    assert r1.slope == pytest.approx(r0.slope, rel=0.05)


def test_the_flow_is_not_changed_and_the_co2_comes_from_the_monitor(sample):
    m = parse_session_text(merge_capnostream_export(sample, waveform_export(sample), start_offset_s=4.6).text)
    assert m.n == sample.n and np.allclose(m.v1, sample.v1, equal_nan=True)
    i = 9000
    assert float(m.co2_mmhg[i]) == pytest.approx(float(sample.co2_mmhg[i]), abs=1.0)


def test_a_given_shift_skips_the_search(sample):
    res = merge_capnostream_export(sample, waveform_export(sample, shift=40.0), shift_s=40.0)
    assert res.method == "given" and res.shift_s == 40.0
    close_to_sample(parse_session_text(res.text), sample)


def test_a_monitor_that_started_long_before_needs_a_wider_search(sample):
    text = waveform_export(sample, shift=200.0)
    with pytest.raises(ValueError, match="Could not line the Capnostream up"):
        merge_capnostream_export(sample, text, search_s=30)
    res = merge_capnostream_export(sample, text, start_offset_s=200 - 2.7, search_s=20)
    assert res.shift_s == pytest.approx(200.0, abs=0.75)


def test_regular_breathing_is_ambiguous_by_a_whole_breath_and_says_so(sample):
    res = merge_capnostream_export(sample, waveform_export(sample))  # no hint
    assert any("ambiguous" in w for w in res.warnings)
    assert abs(res.shift_s - 7.3) < 4.5  # within a breath of the truth, and told to the user


def test_a_breath_hold_makes_the_match_unique(sample):
    held = copy.deepcopy(sample)
    gap = (held.time_s > 100) & (held.time_s < 112)  # 12 s with no breathing: no flow, no CO2
    held.pneumo_lpm[gap] = 0
    held.co2_mmhg[gap] = 0
    res = merge_capnostream_export(held, waveform_export(held))
    assert not res.warnings and res.shift_s == pytest.approx(7.3, abs=0.75)


def test_unrelated_data_is_not_lined_up(sample):
    rng = np.random.default_rng(1)
    rows = ["Time (s),CO2 (mmHg)"] + [f"{t * 0.05:.2f},{20 + 15 * rng.random():.1f}" for t in range(8000)]
    with pytest.raises(ValueError, match="Could not line the Capnostream up"):
        merge_capnostream_export(sample, "\n".join(rows))


def test_a_trend_export_gives_each_breath_the_monitors_etco2(sample):
    res = merge_capnostream_export(sample, trend_export(sample))
    assert res.kind == "trend" and res.method == "assumed" and any("no CO2 waveform" in w for w in res.warnings)
    assert res.shift_s == pytest.approx(2.7)
    m = parse_session_text(res.text)
    ours = close_to_sample(m, sample)
    assert all(not np.isfinite(b.fico2) for b in ours)  # no FiCO2 from a trend
    assert session_response(m, skip_sec=30).slope == pytest.approx(session_response(sample, skip_sec=30).slope, rel=0.08)


def test_a_trend_export_from_a_monitor_started_earlier_with_the_offset_given(sample):
    res = merge_capnostream_export(sample, trend_export(sample, offset=20.0), start_offset_s=20.0)
    close_to_sample(parse_session_text(res.text), sample)
    assert merge_capnostream_export(sample, trend_export(sample, offset=20.0), shift_s=22.7).method == "given"


def test_a_headerless_file_needs_its_layout_or_is_one_column_of_samples(sample):
    co2 = np.interp(np.arange(0, 180, 0.05) - 2.7, sample.time_s, sample.co2_mmhg.astype(float), left=0, right=0)
    text = "\n".join(f"{t * 0.05 + 100:.2f};{v:.1f};38" for t, v in enumerate(co2))
    with pytest.raises(ValueError, match="fields="):
        parse_capnostream_export(text)
    e = parse_capnostream_export(text, fields="time,co2,etco2")
    assert e.kind == "waveform" and e.t[0] == 0 and e.rate_hz == pytest.approx(20, abs=0.1)
    one = parse_capnostream_export("\n".join(f"{v:.1f}" for v in e.co2))
    assert one.kind == "waveform" and any("single column" in n for n in one.notes)


def test_several_samples_on_a_line_and_times_given_to_the_second():
    lines = [f"{t},{','.join(str(30 + i) for i in range(20))}" for t in range(5)]
    e = parse_capnostream_export("\n".join(lines), fields="time,co2*")
    assert e.kind == "waveform" and len(e.co2) == 100 and e.t[1] == pytest.approx(0.05) and e.t[20] == pytest.approx(1.0)
    ties = "Time,CO2 (mmHg)\n" + "\n".join(f"{t // 20},{30 + t % 7}" for t in range(100)) + "\n"
    e = parse_capnostream_export(ties)  # 20 rows share each second
    assert e.rate_hz == pytest.approx(20, abs=0.1) and e.kind == "waveform"


def test_a_clear_message_when_the_export_cannot_be_used():
    with pytest.raises(ValueError, match="no CO2 or EtCO2 column. Columns found: TIME, SPO2"):
        parse_capnostream_export("Time,SpO2\n0,98\n1,97\n")
    low = "Time,CO2\n" + "\n".join(f"{t * 0.05},{4 + (t % 10) / 10}" for t in range(200))
    with pytest.raises(ValueError, match="look like % or kPa"):
        parse_capnostream_export(low)
    assert parse_capnostream_export(low, units="%").co2.max() == pytest.approx(4.9 * 7.6, abs=0.1)
    with pytest.raises(ValueError, match="too slowly for a waveform"):
        parse_capnostream_export("Time,CO2\n" + "\n".join(f"{t},{35 + t % 3}" for t in range(30)))
    with pytest.raises(ValueError, match="empty"):
        parse_capnostream_export("  \n")


@pytest.mark.parametrize("cls", [SessionLibrary, ChunkedSessionLibrary])
def test_the_library_stores_the_merged_session_next_to_the_recording(tmp_path, sample, cls):
    lib = cls(tmp_path / "lib")
    src = tmp_path / "arduino.csv"
    write_session(sample, src)
    rec, _ = lib.import_file(src)
    new, parsed, res = lib.merge_capnostream(rec.id, waveform_export(sample), "Study A capno.txt", start_offset_s=4.6)
    assert new.id != rec.id and new.status == "imported" and new.source.startswith("Imported · ") and "Capnostream 35 export" in new.source
    assert new.file_name.endswith(".csv") and len(lib.list()) == 2 and res.shift_s == pytest.approx(7.3, abs=0.75)
    assert new.stats["breaths"] >= 40 and lib.parse(new.id).n == parsed.n
    with pytest.raises(ValueError):
        lib.merge_capnostream("nope", waveform_export(sample), "x.txt")
    assert len(lib.list()) == 2
