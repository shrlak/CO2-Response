import math
import re
from datetime import datetime

import pytest
from conftest import DEFAULT_ENCODERS, same

from co2response.alarms.engine import AlarmEngine
from co2response.channels import DEFAULT_CHANNELS, clone_channels
from co2response.jsfmt import parse_iso_ms
from co2response.processing.metrics import minute_ventilation, respiratory_rate
from co2response.processing.pipeline import DEFAULT_PROCESSING, Pipeline
from co2response.protocol import parse_line
from co2response.settings import DEFAULT_ALARMS
from co2response.sim.core import SimulatorCore
from co2response.storage.library import SessionLibrary
from co2response.storage.recorder import SessionRecorder
from co2response.storage.session_file import (
    COLUMNS,
    Range,
    SessionMeta,
    SessionParser,
    breaths_csv,
    clean_text,
    csv_escape,
    events_csv,
    export_session_parts,
    parse_session_text,
    session_file_name,
    split_csv_line,
    summarize,
)

T0 = parse_iso_ms("2026-11-05T05:00:00.000Z")


def record_session(lib: SessionLibrary, seconds: int, stop: bool = True):
    """Record ``seconds`` of a simulated CO2 ramp through the real recorder."""
    channels = clone_channels(DEFAULT_CHANNELS)
    channels["channels"]["co2"]["calibration"] = {"type": "linear", "gain": 2.02, "offset": -0.01}
    core = SimulatorCore(DEFAULT_ENCODERS, scenario="co2Ramp", seed=11, ramp_mm_hg_per_min=20)
    pipe = Pipeline(channels, DEFAULT_PROCESSING)
    wall = [T0]
    rec = SessionRecorder(lib, clock=lambda: wall[0])

    def log(e):
        rec.event(e.t, f"ALARM_{e.kind.upper()} {e.id or ''} {e.priority or ''} {e.message}")

    import dataclasses

    alarms = AlarmEngine(dataclasses.replace(DEFAULT_ALARMS, high_mm_hg=45, stop_mm_hg=None), log)
    rec.start(SessionMeta("TEST #01", T0, "test", "Simulator", "sim", 100, channels, DEFAULT_PROCESSING, DEFAULT_ALARMS), T0)
    rec.event(T0, "RECORDING_STARTED source=Simulator")
    breaths, co2 = [], []

    def use(p):
        if p.breath:
            breaths.append(p.breath)
            rec.breath(p.breath.etco2, p.breath.fico2, respiratory_rate(breaths), p.breath.vt_ml, minute_ventilation(breaths, p.breath.t_end), p.breath.peak_insp_lpm, p.breath.quality, p.breath.period_sec, p.breath.vte_ml)
            alarms.on_breath(p.breath.etco2, wall[0])
        rec.sample(wall[0], p.t_ms, p.seq, p.volts, p.co2_mmhg, p.co2_pct, p.flow_lpm, p.pneumo_pa, p.pneumo_lpm)
        co2.append(p.co2_mmhg)

    for i in range(seconds * 100):
        if i == 1000:
            rec.event(wall[0], 'MARK step #2, "fast"')
        for line in core.next_sample():
            d = parse_line(line)
            if d.kind != "data":
                continue
            p = pipe.process(d.t_ms, d.volts, d.seq)
            if p is not None:  # None while the spike filter fills
                use(p)
        wall[0] += 10
    for p in pipe.flush():
        use(p)
    out = rec.stop() if stop else None
    return rec, out, breaths, co2


@pytest.fixture
def lib(tmp_path):
    return SessionLibrary(tmp_path / "sessions")


# ------------------------------------------------------------------ helpers


def test_escapes_splits_and_cleans_free_text():
    assert csv_escape("plain") == "plain"
    assert csv_escape('a,b "c"') == '"a,b ""c"""'
    assert split_csv_line('1,"a,b ""c""",3') == ["1", 'a,b "c"', "3"]
    assert clean_text("run #2\nnext") == "run no.2 next"


def test_names_files_by_study_code_and_time_only():
    assert session_file_name("Subj 07 / run#2", datetime(2026, 11, 5, 3, 4, 5)) == "co2r_Subj-07-run-2_20261105-030405.csv"
    assert re.fullmatch(r"co2r_session_20260101-000000\.csv", session_file_name("", datetime(2026, 1, 1)))


# ------------------------------------------------------------------ recording


def test_writes_a_single_rectangular_csv_with_samples_breaths_and_events(lib):
    _, (rec, mem), breaths, _ = record_session(lib, 60)
    assert mem is None
    text = lib.text(rec.id)
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "# CO2-Response session,v4"
    header = next(line for line in lines if not line.startswith("#"))
    assert header == ",".join(COLUMNS)
    rows = lines[lines.index(header) + 1 :]
    assert len(rows) == 6000
    for r in rows:
        assert not r.startswith("#")
        assert len(split_csv_line(r)) == len(COLUMNS)
    # '#' never appears in a data row, so comment='#' readers are safe
    assert not any("#" in r for r in rows)

    s = parse_session_text(text)
    assert s.version == 4
    assert s.study_code == "TEST no.01"
    assert s.n == 6000
    assert len(s.breaths) == len(breaths)
    texts = [e.text for e in s.events]
    assert "RECORDING_STARTED source=Simulator" in texts
    assert 'MARK step no.2, "fast"' in texts
    assert any(t.startswith("ALARM_START co2High high") for t in texts)
    # time_s comes from the device clock: exactly 10 ms apart
    d = s.time_s[1:] - s.time_s[:-1]
    assert abs(d - 0.01).max() < 1e-6
    assert rec.status == "complete"
    assert rec.stats["samples"] == 6000 and rec.stats["breaths"] == len(breaths) and rec.stats["markers"] == 1


def test_replays_identically_from_the_file(lib):
    _, (rec, _), breaths, co2 = record_session(lib, 60)
    s = lib.parse(rec.id)
    assert s.channels["channels"]["co2"]["calibration"] == {"type": "linear", "gain": 2.02, "offset": -0.01}
    pipe = Pipeline(s.channels, DEFAULT_PROCESSING)
    replayed, outs = [], []
    for i in range(s.n):
        p = pipe.process(s.t_ms[i], [s.v0[i], s.v1[i], s.v2[i]], int(s.seq[i]))
        if p is None:
            continue
        outs.append(p)
    outs += pipe.flush()
    for p in outs:
        if p.breath:
            replayed.append(p.breath)
    # the replay is 3 samples behind (spike filter); row k of the file is output k
    for k, p in enumerate(outs):
        assert p.t_ms == s.t_ms[k]
        if co2[k] == co2[k]:
            assert abs(p.co2_mmhg - float(s.co2_mmhg[k])) <= 0.006
    assert same(replayed, breaths)
    # the breath columns in the file match what was computed live (rounded to 0.1)
    for b, live in zip(s.breaths, breaths):
        assert abs(b.etco2 - live.etco2) <= 0.05


def test_survives_a_crash(lib, tmp_path):
    rec, _, _, _ = record_session(lib, 5, stop=False)
    rec.poll(rec.clock() + 1000)  # one flush interval
    sid = rec.rec.id
    # a new app start finds the unfinished recording
    lib2 = SessionLibrary(tmp_path / "sessions")
    recovered = lib2.recover_interrupted(rec.rec.last_write_at + 20000)
    assert [r.id for r in recovered] == [sid]
    assert recovered[0].status == "interrupted"
    assert lib2.parse(sid).n == 500


def test_does_not_mark_a_recording_still_being_written_as_interrupted(lib):
    rec, _, _, _ = record_session(lib, 1, stop=False)
    assert lib.recover_interrupted(rec.rec.last_write_at + 1000) == []
    rec.stop()


def test_keeps_the_file_in_memory_when_the_folder_cannot_be_written(tmp_path):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    lib = SessionLibrary(blocker / "sessions")
    _, (rec, mem), _, _ = record_session(lib, 2)
    assert rec is None
    assert mem.startswith("# CO2-Response session,v4") and parse_session_text(mem).n == 200


# ------------------------------------------------------------------ parsing


def test_same_result_however_the_text_is_split(lib):
    _, (rec, _), _, _ = record_session(lib, 10)
    text = lib.text(rec.id)
    whole = parse_session_text(text)
    p = SessionParser()
    for i in range(0, len(text), 997):
        p.push(text[i : i + 997])
    pieces = p.finish()
    assert pieces.n == whole.n
    assert pieces.v1.tolist() == whole.v1.tolist()
    assert same(pieces.breaths, whole.breaths)
    assert same(pieces.events, whole.events)


V1 = "\n".join(
    [
        "# CO2-Response session,v1",
        "# study_code,OLD",
        "# started,2026-11-05T05:00:00.000Z",
        "wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,flow_lpm,pneumo_lpm,pneumo_pa,event",
        "2026-11-05T05:00:00.000Z,,,,,,,,,,CONNECTED Simulator",
        "2026-11-05T05:00:00.010Z,10,0,2.0000,2.5000,2.5000,38.00,0.00,0.00,0.0,",
        "2026-11-05T05:00:00.020Z,20,1,2.0100,2.5100,2.5100,38.19,0.40,0.40,2.0,",
        "2026-11-05T05:00:00.020Z,,,,,,,,,,BREATH etco2_mmHg=38.2 rr=15.0 vt_ml=500 mv_lpm=7.50 by=flow",
        '2026-11-05T05:00:01.000Z,,,,,,,,,,"MARK a, b"',
    ]
)


def test_reads_v1_files_and_converts_them_to_v2():
    s = parse_session_text(V1)
    assert s.version == 1
    assert s.n == 2
    assert s.time_s[1] == pytest.approx(0.02)
    nan = float("nan")
    from co2response.storage.session_file import BreathRec

    assert same(s.breaths, [BreathRec(0.02, 1, 38.2, nan, 15, 500, 7.5, nan, "ok", period_est=4.0)])  # screened on load; length from its RR
    assert [e.text for e in s.events] == ["CONNECTED Simulator", "MARK a, b"]
    v2 = parse_session_text("".join(export_session_parts(s)))
    assert v2.version == 4
    assert v2.meta["converted_from"] == "v1"
    assert v2.n == 2
    assert v2.breaths[0].etco2 == 38.2
    assert [e.text for e in v2.events] == ["CONNECTED Simulator", "MARK a, b"]


def test_rejects_files_that_are_not_sessions():
    with pytest.raises(ValueError, match="not a CO2-Response session"):
        parse_session_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError, match="no data header"):
        parse_session_text("# only comments\n")


# ------------------------------------------------------------------ export


def test_re_exports_losslessly_and_a_range_as_a_subset(lib):
    _, (rec, _), _, _ = record_session(lib, 30)
    s = lib.parse(rec.id)
    original = lib.text(rec.id)
    again_text = "".join(export_session_parts(s, app_version="test"))
    assert again_text == original  # byte for byte
    again = parse_session_text(again_text)
    assert again.n == s.n
    assert same(again.breaths, s.breaths)

    part = parse_session_text("".join(export_session_parts(s, Range(10, 20))))
    assert part.meta["exported_range_s"] == "10.000-20.000"
    assert part.n == 1001
    assert part.time_s[0] == pytest.approx(10)
    assert all(10 <= b.time_s <= 20 for b in part.breaths)
    assert all(10 <= e.time_s <= 20 for e in part.events)


def test_writes_breath_and_event_tables(lib):
    _, (rec, _), breaths, _ = record_session(lib, 30)
    s = lib.parse(rec.id)
    b = breaths_csv(s).rstrip("\n").split("\n")
    assert b[0] == "breath,time_s,wall_time_iso,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm,period_s,vte_ml,mv_breath_lpm,quality"
    assert len(b) == len(breaths) + 1
    e = events_csv(s).rstrip("\n").split("\n")
    assert e[0] == "time_s,wall_time_iso,type,event"
    assert any(",MARK," in line for line in e)
    summ = summarize(s)
    assert (summ.samples, summ.breaths, summ.markers, summ.dropped, summ.rate_hz) == (3000, len(breaths), 1, 0, 100)


def test_the_bundled_sample_session_parses():
    from conftest import ROOT

    from co2response.storage.session_file import read_session, session_markers

    s = read_session(ROOT / "sample-data" / "synthetic_session.csv")
    summ = summarize(s)
    assert s.version == 4 and summ.samples > 17900 and summ.breaths >= 40 and summ.rejected >= 1
    # a whole rebreathing test: baseline, rebreathing through the warning to the stop limit, end
    assert [label for _, label in session_markers(s.events)] == ["Baseline", "CO2 rebreathing started", "End of test"]
    starts = [e.text.split()[1:3] for e in s.events if e.text.startswith("ALARM_START")]
    assert starts == [["co2High", "medium"], ["co2Stop", "high"]]
    assert all(math.isfinite(b.period) and math.isfinite(b.vte) for b in s.breaths)


# ------------------------------------------------------------------ library


def test_imports_lists_and_deletes(tmp_path):
    src = SessionLibrary(tmp_path / "a")
    _, (rec0, _), _, _ = record_session(src, 10)
    original = src.path(rec0)

    lib = SessionLibrary(tmp_path / "b")
    rec, _ = lib.import_file(original, "from-another-pc.csv")
    assert (rec.status, rec.file_name, rec.study_code) == ("imported", "from-another-pc.csv", "TEST no.01")
    assert rec.stats["samples"] == 1000
    assert [r.id for r in lib.list()] == [rec.id]
    assert lib.text(rec.id) == original.read_text()
    # a second import of the same name gets its own file
    rec2, _ = lib.import_file(original, "from-another-pc.csv")
    assert rec2.file_name == "from-another-pc-2.csv"
    lib.delete(rec.id)
    assert [r.id for r in lib.list()] == [rec2.id]
    assert not (tmp_path / "b" / "from-another-pc.csv").exists()
    # the index survives a restart
    assert [r.id for r in SessionLibrary(tmp_path / "b").list()] == [rec2.id]


def test_keeps_nothing_when_an_imported_file_is_not_a_session(tmp_path):
    bad = tmp_path / "people.csv"
    bad.write_text("name,age\nx,1\n")
    lib = SessionLibrary(tmp_path / "lib")
    with pytest.raises(ValueError, match="not a CO2-Response session"):
        lib.import_file(bad)
    assert lib.list() == []
    assert not (tmp_path / "lib" / "people.csv").exists()


def test_sample_script_reproduces_the_committed_sample_file(tmp_path):
    """The whole chain (simulator, protocol, filter, breaths, alarms, recorder, file
    format) must keep producing sample-data/synthetic_session.csv byte for byte."""
    import importlib.util

    from conftest import ROOT

    spec = importlib.util.spec_from_file_location("make_sample_session", ROOT / "scripts" / "make_sample_session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "s.csv"
    mod.make(out)

    def body(p):
        return re.sub(r"^# app_version,.*$", "", p.read_text(), count=1, flags=re.M)

    assert body(out) == body(ROOT / "sample-data" / "synthetic_session.csv")


def test_chunked_library_stores_sessions_as_small_parts(tmp_path):
    """The browser build's library: same behaviour, the file split into parts."""
    from co2response.storage.library import ChunkedSessionLibrary

    lib = ChunkedSessionLibrary(tmp_path / "s")
    lib.PART_SIZE = 50_000
    _, (rec, mem), breaths, _ = record_session(lib, 30)
    assert mem is None and rec.status == "complete"
    parts = sorted((tmp_path / "s" / rec.id).glob("*.part"))
    assert len(parts) > 5 and all(p.stat().st_size < 50_000 + 20_000 for p in parts)
    text = lib.text(rec.id)
    assert text.startswith("# CO2-Response session,v4")
    s = lib.parse(rec.id)
    assert s.n == 3000 and len(s.breaths) == len(breaths)
    # re-export is the same file, so the parts join up exactly
    assert "".join(export_session_parts(s, app_version="test")) == text

    rec2, _ = lib.import_text(text, rec.file_name)
    assert rec2.file_name.endswith("-2.csv") and rec2.stats["samples"] == 3000 and lib.text(rec2.id) == text
    with pytest.raises(ValueError, match="not a CO2-Response session"):
        lib.import_text("a,b\n1,2\n", "x.csv")
    assert len(lib.list()) == 2
    lib.delete(rec.id)
    assert [r.id for r in ChunkedSessionLibrary(tmp_path / "s").list()] == [rec2.id]
