"""The app's version and the board's firmware: shown, and checked against what the app expects."""

from __future__ import annotations

import re

from conftest import ROOT
from test_digital_board import digital_engine
from test_engine import run

from co2response import __version__, firmware

R4 = "I,FW,{},BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR-R,FLOW,SFM3300,CH,2,RATE,100,WD,3000"


def info(line: str) -> dict[str, str]:
    parts = line.split(",")[1:]
    return dict(zip(parts[::2], parts[1::2]))


def test_the_expected_versions_are_the_ones_in_the_firmware_folders():
    # change FW_VERSION in a config.h and this test asks for EXPECTED to follow
    for board, (version, folder) in firmware.EXPECTED.items():
        text = (ROOT / folder / "config.h").read_text()
        m = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', text)
        assert m, folder
        assert m.group(1) == version, f"{board}: config.h says {m.group(1)}, firmware.EXPECTED says {version}"


def test_versions_compare_as_numbers():
    assert firmware.parse_version("2.10.0") > firmware.parse_version("2.9.1")
    assert firmware.parse_version("sim-2.0.0") is None
    assert firmware.parse_version("co2r-py-0.3.0") is None


def test_each_board_is_checked_against_its_own_firmware():
    assert firmware.check(info(R4.format("2.3.0"))).state == "ok"
    old = firmware.check(info(R4.format("2.2.0")))
    assert old.state == "older" and "2.2.0" in old.message and "firmware/CO2ResponseR4" in old.message
    new = firmware.check(info(R4.format("2.4.0")))
    assert new.state == "newer" and "update the app" in new.message
    adc = firmware.check(info("I,FW,1.1.0,ADC,ADS1115,CH,3,RATE,100"))
    assert adc.board == "ADC" and adc.state == "older" and "firmware/CO2Response " in adc.message
    # the simulator and the Python DAQ drivers are not checked
    assert firmware.check(info("I,FW,sim-2.0.0,BOARD,UNO_R4,UNITS,1")) is None
    assert firmware.check(info("I,FW,co2r-py-0.3.0,ADC,NI USB-6001,CH,3")) is None
    assert firmware.check(None) is None


def test_the_header_text_has_the_app_version_and_then_the_firmware():
    assert firmware.short_version(None) == f"v{__version__}"
    assert firmware.short_version({"FW": "2.3.0"}) == f"v{__version__} · FW 2.3.0"
    assert firmware.version_line({"FW": "2.3.0"}) == f"CO2-Response {__version__} · firmware 2.3.0"


def test_old_firmware_on_the_board_is_reported_until_the_board_is_disconnected(tmp_path):
    e = digital_engine(tmp_path)
    e.connect("simulator")
    run(e, 2)
    assert not e.alarms.is_active("firmware")  # the simulator's own firmware is not checked
    e._on_line(R4.format("2.2.0"))
    e._on_line(R4.format("2.2.0"))  # the info line comes again (INFO): logged once
    run(e, 1)
    assert e.alarms.is_active("firmware")
    assert sum("OLDER THAN THIS APP EXPECTS" in x.text for x in e.log) == 1
    assert e.snapshot().info["FW"] == "2.2.0"
    # the board is flashed with the current firmware
    e._on_line(R4.format(firmware.EXPECTED["UNO_R4"][0]))
    run(e, 1)
    assert not e.alarms.is_active("firmware")
    e._on_line(R4.format("2.2.0"))
    run(e, 1)
    assert e.alarms.is_active("firmware")
    e.disconnect()
    assert not e.alarms.is_active("firmware")
