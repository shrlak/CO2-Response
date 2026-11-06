from co2response.jsfmt import (
    iso_ms,
    js_json,
    js_num,
    js_round,
    to_exponential,
    to_fixed,
)
from co2response.protocol import (
    LineSplitter,
    SeqTracker,
    format_data_line,
    parse_line,
    xor_checksum,
)


def test_checksum_xors_every_byte_between_d_and_star():
    payload = ",1,10,1.0000,2.0000,3.0000"
    x = 0
    for ch in payload:
        x ^= ord(ch)
    assert xor_checksum(payload) == f"{x:02X}"
    assert format_data_line(1, 10, [1, 2, 3]) == f"D{payload}*{xor_checksum(payload)}"


def test_firmware_line_format_cases():
    # the same cases as firmware/test/test_line_format.cpp, so firmware and app agree byte for byte
    assert format_data_line(0, 0, [0.0, 0.0, 0.0]) == "D,0,0,0.0000,0.0000,0.0000*32"
    assert format_data_line(1042, 10420, [2.6316, 2.5, 2.5]) == "D,1042,10420,2.6316,2.5000,2.5000*02"
    assert format_data_line(65535, 4294967295, [4.9999, 0.0001, -0.5]) == "D,65535,4294967295,4.9999,0.0001,-0.5000*22"
    assert format_data_line(7, 123456, [1.23456, 3.14159, 0.00004]) == "D,7,123456,1.2346,3.1416,0.0000*01"


def test_parses_a_valid_data_line():
    line = format_data_line(1042, 10420, [2.6316, 2.5, 2.5])
    p = parse_line(line)
    assert p.kind == "data" and p.seq == 1042 and p.t_ms == 10420 and p.volts == [2.6316, 2.5, 2.5]


def test_accepts_crlf_and_lower_case_checksums():
    line = format_data_line(7, 70, [0.1, 0.2, 0.3])
    lower = line[:-2] + line[-2:].lower()
    assert parse_line(lower + "\r").kind == "data"


def test_rejects_a_bad_checksum():
    line = format_data_line(1, 10, [1, 2, 3])
    bad = line[:-2] + ("01" if line.endswith("00") else "00")
    p = parse_line(bad)
    assert p.kind == "bad" and p.reason == "checksum"


def test_rejects_a_missing_checksum():
    p = parse_line("D,1,10,1.0,2.0,3.0")
    assert p.kind == "bad" and p.reason == "checksum"


def test_rejects_a_corrupted_field_even_when_the_checksum_matches():
    payload = ",1,10,1.0x00,2.0000,3.0000"
    p = parse_line(f"D{payload}*{xor_checksum(payload)}")
    assert p.kind == "bad" and p.reason == "format"


def test_rejects_a_sequence_number_above_16_bits():
    payload = ",70000,10,1.0000"
    p = parse_line(f"D{payload}*{xor_checksum(payload)}")
    assert p.kind == "bad" and p.reason == "format"


def test_parses_info_error_and_ack_lines():
    p = parse_line("I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100")
    assert p.kind == "info" and p.fields == {"FW": "1.0.0", "ADC": "ADS1115", "CH": "3", "RATE": "100"}
    e = parse_line("E,ADC_NOT_FOUND")
    assert e.kind == "error" and e.code == "ADC_NOT_FOUND"
    a = parse_line("A,RATE,100")
    assert a.kind == "ack" and a.fields == ["RATE", "100"]


def test_blank_lines_are_empty_and_junk_is_bad():
    assert parse_line("   ").kind == "empty"
    p = parse_line("hello")
    assert p.kind == "bad" and p.reason == "format"


def test_seq_tracker_counts_dropped_samples():
    s = SeqTracker()
    for q in [1, 2, 3, 6, 7, 10]:
        s.push(q)
    assert s.dropped == 4
    assert s.received == 6
    assert abs(s.drop_rate - 4 / 10) < 1e-12


def test_seq_tracker_handles_16_bit_wraparound():
    s = SeqTracker()
    for q in [65534, 65535, 0, 1, 3]:
        s.push(q)
    assert s.dropped == 1


def test_seq_tracker_does_not_count_a_device_reset_as_a_loss():
    s = SeqTracker()
    s.push(40000, 400000)
    s.push(40001, 400010)
    s.push(0, 0)
    s.push(1, 10)
    assert s.dropped == 0


def test_seq_tracker_ignores_duplicates_and_out_of_order_samples():
    s = SeqTracker()
    for q in [10, 11, 11, 5, 6]:
        s.push(q)
    assert s.dropped == 0


def test_line_splitter_reassembles_lines_split_across_chunks():
    sp = LineSplitter()
    out = sp.push("D,1,1") + sp.push("0,1*AA\nI,FW") + sp.push(",1\n")
    assert out == ["D,1,10,1*AA", "I,FW,1"]


def test_javascript_compatible_formatting():
    assert to_fixed(2.53125, 4) == "2.5313"  # exact tie: away from zero, like toFixed
    assert to_fixed(-2.53125, 4) == "-2.5313"
    assert to_fixed(1.005, 2) == "1.00"  # 1.005 is really 1.00499999…
    assert to_fixed(-0.0, 2) == "0.00"
    assert js_round(2.5) == 3 and js_round(-2.5) == -2
    assert js_num(760.0) == "760" and js_num(0.005) == "0.005" and js_num(1e-7) == "1e-7" and js_num(1 / 3) == "0.3333333333333333"
    assert js_json({"a": 2.0, "b": [0.5, None, True], "c": "x"}) == '{"a":2,"b":[0.5,null,true],"c":"x"}'
    assert to_exponential(0.00123, 1) == "1.2e-3"
    assert iso_ms(1793682000010) == "2026-11-03T05:00:00.010Z"  # from node: new Date(…).toISOString()


# ------------------------------------------------------------------ shared fixtures


def _fixtures():
    import json

    from conftest import ROOT

    return json.loads((ROOT / "docs" / "protocol-fixtures.json").read_text(encoding="utf-8"))


def test_formats_every_shared_fixture_exactly():
    # the same vectors are checked by the Python acquisition package (python/tests) and
    # match the firmware's formatter, so all three produce byte-identical lines
    for f in _fixtures()["format"]:
        assert format_data_line(f["seq"], f["tMs"], f["volts"]) == f["line"]


def test_parses_every_shared_fixture():
    names = {"tMs": "t_ms"}
    for f in _fixtures()["parse"]:
        p = parse_line(f["line"])
        assert p.kind == f["kind"], f["line"]
        for k in ("seq", "tMs", "volts", "reason", "fields", "code"):
            if k in f:
                assert getattr(p, names.get(k, k)) == f[k], (f["line"], k)
