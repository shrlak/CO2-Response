"""The monitor fed by the Python acquisition package (python/co2r, ``co2r-daq serve``)
over WebSocket: the path an NI or LabJack DAQ takes, here with its simulated DAQ."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest
from conftest import ROOT
from test_engine import run_real

from co2response.alarms.audio import SilentAudio
from co2response.engine.monitor import MonitorEngine
from co2response.settings import SettingsStore

pytest.importorskip("websockets")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(*args: str):
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "python"), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "co2r", "serve", *args, "--ws-port", str(port)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    end = time.monotonic() + 20
    out = []
    while time.monotonic() < end:
        line = proc.stdout.readline()
        out.append(line)
        if "serving on" in line:
            return proc, f"ws://127.0.0.1:{port}"
        if proc.poll() is not None:
            break
    proc.kill()
    pytest.fail("co2r-daq did not start:\n" + "".join(out))


@pytest.fixture
def engine(tmp_path):
    e = MonitorEngine(store=SettingsStore(tmp_path / "cfg"), audio=SilentAudio(), port_lister=lambda: [], sessions_dir=tmp_path / "s", prevent_sleep=False)
    yield e
    e.disconnect()


def test_monitor_runs_on_the_python_acquisition_server(engine):
    proc, url = start_server("--driver", "simulated", "--seed", "1")
    try:
        e = engine
        e.update_settings(bridge_url=url)
        assert e.connect("bridge"), e.snapshot().status_detail
        # the server names its device in INFO
        assert run_real(e, 5, lambda: e.snapshot().source_label == "Simulated DAQ (Python)"), e.snapshot().source_label
        assert run_real(e, 25, lambda: len(e.good) >= 4)
        n = e.snapshot().numbers
        assert 14 <= round(n.rr) <= 16 and 36 <= round(n.etco2) <= 39 and 460 <= round(n.vt) <= 549, n

        # the monitor's commands reach the server, and the hardware check passes
        job = e.run_hardware_check(2)
        assert run_real(e, 8, lambda: job.done)
        by = {x.id: x for x in job.results}
        assert by["handshake"].status == "pass" and "co2r-py-" in by["handshake"].result
        assert by["drops"].status == "pass" and by["corrupt"].status == "pass"
    finally:
        proc.kill()
        proc.wait()


def test_a_daq_that_cannot_be_opened_is_named_in_the_alarm_and_the_check(engine):
    # with no NI-DAQmx library installed, the server keeps reporting why it cannot open the DAQ
    proc, url = start_server("--driver", "nidaq", "--device", "Dev9")
    try:
        e = engine
        e.update_settings(bridge_url=url)
        assert e.connect("bridge"), e.snapshot().status_detail
        assert run_real(e, 10, lambda: e.alarms.is_active("adcError"))
        msg = e.alarms.get("adcError").message
        assert msg.startswith("DAQ NOT FOUND: ") and "I2C" not in msg, msg
        job = e.run_hardware_check(3)
        assert run_real(e, 8, lambda: job.done)
        adc = next(x for x in job.results if x.id == "adc")
        assert adc.label == "DAQ device" and adc.status == "fail" and "co2r-daq list" in adc.fix
    finally:
        proc.kill()
        proc.wait()
