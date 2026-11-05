from co2r.protocol import SeqTracker, checksum, fixed4, format_data_line, info_line, parse_line


def test_formats_every_shared_fixture_exactly(fixtures):
    # same vectors as web/tests/protocol.test.ts and the firmware formatter
    for f in fixtures["format"]:
        assert format_data_line(f["seq"], f["tMs"], f["volts"]) == f["line"]


def test_parses_shared_fixtures(fixtures):
    for f in fixtures["parse"]:
        p = parse_line(f["line"])
        assert p["kind"] == f["kind"], f["line"]
        for k in ("seq", "tMs", "volts", "reason", "fields", "code"):
            if k in f:
                assert p[k] == f[k], (f["line"], k)


def test_rounds_halves_away_from_zero_like_javascript():
    assert fixed4(0.03125) == "0.0313"
    assert fixed4(-0.03125) == "-0.0313"
    assert fixed4(-0.0) == "0.0000"
    assert fixed4(-0.00001) == "-0.0000"
    assert format(0.03125, ".4f") == "0.0312"  # why plain formatting is not used


def test_checksum_covers_bytes_between_d_and_star():
    payload = ",1,10,1.0000,2.0000,3.0000"
    x = 0
    for c in payload:
        x ^= ord(c)
    assert checksum(payload) == f"{x:02X}"


def test_info_line_keeps_pairs_parseable():
    line = info_line({"FW": "co2r-py-0.3.0", "ADC": "NI USB-6001, Dev1", "CH": 3})
    assert parse_line(line)["fields"] == {"FW": "co2r-py-0.3.0", "ADC": "NI USB-6001  Dev1", "CH": "3"}


def test_seq_tracker_counts_gaps_wraps_and_resets():
    s = SeqTracker()
    for q in [65534, 65535, 0, 1, 3]:
        s.push(q)
    assert s.dropped == 1
    r = SeqTracker()
    r.push(40000, 400000)
    r.push(40001, 400010)
    r.push(0, 0)  # device reset: clock went backwards
    r.push(1, 10)
    assert r.dropped == 0
