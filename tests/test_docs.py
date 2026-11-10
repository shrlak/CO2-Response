"""The documentation stays true to the code where that can be checked."""

import re

from conftest import ROOT


def test_every_test_named_in_the_traceability_table_exists():
    text = (ROOT / "docs" / "requirements-traceability.md").read_text(encoding="utf-8")
    current, missing, named = None, [], 0
    for m in re.finditer(r"`((?:python/tests/)?test_\w+\.py)(?:::(\w+))?`|`::(\w+)`", text):
        file, name, more = m.groups()
        current = file or current
        path = ROOT / (current if current.startswith("python/") else f"tests/{current}")
        if not path.exists():
            missing.append(current)
        elif (name or more) and f"def {name or more}(" not in path.read_text(encoding="utf-8"):
            missing.append(f"{current}::{name or more}")
        named += 1
    assert named > 40 and missing == []


def test_documented_defaults_match_the_code():
    from co2response.settings import AlarmSettings, ProcessingOptions, TestSettings
    from co2response.storage.session_file import COLUMNS, FORMAT_VERSION

    a, t, p = AlarmSettings(), TestSettings(), ProcessingOptions()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    fmt = (ROOT / "docs" / "data-format.md").read_text(encoding="utf-8")
    assert f"warning at {a.high_mm_hg:g} mmHg and stop at {a.stop_mm_hg:g} mmHg" in readme
    assert f"the first {t.response_skip_sec:g} s of rebreathing are left out of the fit" in readme
    assert (p.co2_delay_ms, p.volume_btps, p.rebreathing_gas_factor) == (0, False, 1)  # "off (or neutral) by default"
    assert f"## File layout (format v{FORMAT_VERSION})" in fmt and ",".join(COLUMNS) in fmt
    assert f"the same {len(COLUMNS)} columns" in fmt and f"with {len(COLUMNS)} columns" in readme


def test_firmware_and_protocol_document_agree_on_the_watchdog():
    config = (ROOT / "firmware" / "CO2Response" / "config.h").read_text(encoding="utf-8")
    version = re.search(r'#define FW_VERSION "([\d.]+)"', config).group(1)
    timeout = re.search(r"#define WATCHDOG_TIMEOUT_MS (\d+)", config).group(1)
    protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    assert f"I,FW,{version},ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144,WD,{timeout}" in protocol
    sketch = (ROOT / "firmware" / "CO2Response" / "CO2Response.ino").read_text(encoding="utf-8")
    for token in ('"HB"', '"HB OFF"', "E,HOST_LOST", ",WD,"):
        assert token in sketch
