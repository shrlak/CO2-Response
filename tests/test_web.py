"""The GitHub Pages build, in a real browser (Chromium via Playwright).

Builds the site, serves it locally, and drives it like a person would. Pyodide comes
from its CDN, so these tests need the network. They run when Playwright is installed
(CI's "web" job installs it); otherwise they are skipped.

    pip install playwright && playwright install chromium
    pytest tests/test_web.py
"""

from __future__ import annotations

import functools
import http.server
import os
import re
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from conftest import ROOT  # noqa: E402

BOOT_MS = 120_000

FAKE_SERIAL = r"""
(() => {
  // A fake Arduino behind the Web Serial API, so the real serial code runs without hardware.
  const enc = new TextEncoder();
  const xor = (s) => { let x = 0; for (let i = 0; i < s.length; i++) x ^= s.charCodeAt(i); return x.toString(16).toUpperCase().padStart(2, '0'); };
  const INFO = 'I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144\n';
  class FakePort extends EventTarget {
    constructor() { super(); this.commands = []; this.present = true; this.opens = 0; this.ctl = null; this.timer = null; this.n = 0; this.t0 = 0; }
    getInfo() { return { usbVendorId: 0x2341, usbProductId: 0x0043 }; }
    async open() {
      if (!this.present) throw new DOMException('The device has been lost.', 'NetworkError');
      this.opens++; this.n = 0; this.t0 = performance.now();
      this.readable = new ReadableStream({
        start: (ctl) => { this.ctl = ctl; ctl.enqueue(enc.encode(INFO)); this.timer = setInterval(() => this.pump(), 20); },
        cancel: () => this.stopTimer(),
      });
      this.writable = new WritableStream({
        write: (chunk) => {
          const cmd = new TextDecoder().decode(chunk).trim();
          this.commands.push(cmd);
          if (/^INFO$/i.test(cmd)) this.ctl?.enqueue(enc.encode(INFO));
          if (/^START$/i.test(cmd)) this.ctl?.enqueue(enc.encode('A,START\n'));
        },
      });
    }
    stopTimer() { if (this.timer) clearInterval(this.timer); this.timer = null; }
    pump() {
      const due = Math.floor((performance.now() - this.t0) / 10);
      let out = '';
      while (this.n < due) {
        const t = this.n / 100, x = t % 4;
        let flow, co2;
        if (x < 4 / 3) { flow = 35 * Math.sin((Math.PI * x) / (4 / 3)); co2 = 0.2; }
        else { const e = x - 4 / 3; flow = -60 * Math.exp(-e / 0.53) * (1 - Math.exp(-e / 0.06)); co2 = e < 0.3 ? (38 * e) / 0.3 : 38; }
        const v = [((co2 / 760) * 100) / 2, (flow + 100) / 40, (flow / 0.2 + 500) / 200].map((y) => y.toFixed(4));
        const payload = `,${this.n & 0xffff},${this.n * 10},${v.join(',')}`;
        out += `D${payload}*${xor(payload)}\n`;
        this.n++;
      }
      if (out) { try { this.ctl?.enqueue(enc.encode(out)); } catch (e) {} }
    }
    unplug() {
      this.stopTimer(); this.present = false;
      try { this.ctl?.error(new DOMException('The device has been lost.', 'NetworkError')); } catch (e) {}
      fire('disconnect');
    }
    replug() { this.present = true; fire('connect'); }
    async close() { this.stopTimer(); }
  }
  const port = new FakePort();
  const serial = new EventTarget();
  let granted = false;
  serial.requests = 0;
  serial.requestPort = async () => { serial.requests++; granted = true; return port; };
  serial.getPorts = async () => (granted && port.present ? [port] : []);
  const fire = (type) => { const ev = new Event(type); Object.defineProperty(ev, 'target', { value: port }); serial.dispatchEvent(ev); };
  Object.defineProperty(navigator, 'serial', { value: serial, configurable: true });
  window.__fake = { port, serial };
})();
"""


@pytest.fixture(scope="module")
def site_url(tmp_path_factory):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.build(tmp_path_factory.mktemp("site"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_EXECUTABLE")
        try:
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 800}, accept_downloads=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    yield pg
    ctx.close()


def boot(page, url: str) -> None:
    page.goto(url)
    page.wait_for_selector(".monitor-screen", timeout=BOOT_MS)


def tile(page, name: str):
    return page.locator(f"[data-testid=tile-{name}] .val")


def numbers_ok(page) -> None:
    sync_api.expect(tile(page, "rr")).to_have_text(re.compile(r"^1[4-6]$"), timeout=40_000)
    sync_api.expect(tile(page, "etco2")).to_have_text(re.compile(r"^3[6-9]$"))
    sync_api.expect(tile(page, "vt")).to_have_text(re.compile(r"^(4[6-9]\d|5[0-4]\d)$"))


def test_simulator_alarms_and_themes(page, site_url):
    boot(page, site_url + "?sim")
    numbers_ok(page)
    sync_api.expect(page.locator(".header")).to_contain_text("Simulator")
    page.get_by_role("button", name=re.compile("Arm audio")).first.click()
    page.locator('[data-act="drawer-open"]').click()
    page.locator(".drawer").get_by_role("button", name="Test alarm").click()
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("TEST ALARM")
    sync_api.expect(page.locator(".alarm-slot")).to_have_class(re.compile("high"))
    # lower the warning limit below the simulated 38 mmHg: EtCO2 HIGH after two breaths.
    # It is medium priority, because the stop limit (65) stands behind it.
    sync_api.expect(page.locator("[data-testid=tile-etco2] .limits .hi")).to_have_text("60 / 65")
    page.fill("#drawer-high", "30")
    page.press("#drawer-high", "Enter")
    page.locator(".drawer").get_by_role("button", name="Done").click()
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("EtCO2 HIGH", timeout=30_000)
    sync_api.expect(page.locator("[data-testid=tile-etco2]")).to_have_class(re.compile("alarm-medium"))
    page.locator(".alarm-slot .slot-btn").click()
    sync_api.expect(page.locator(".pill")).to_contain_text("SILENCED")
    # now the stop limit, also below 38 mmHg: STOP TEST within a second or so, high
    # priority, and the silence of the warning is cancelled
    page.locator('[data-act="drawer-open"]').click()
    page.fill("#drawer-stop", "35")
    page.press("#drawer-stop", "Enter")
    page.locator(".drawer").get_by_role("button", name="Done").click()
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("STOP TEST", timeout=15_000)
    sync_api.expect(page.locator("[data-testid=tile-etco2]")).to_have_class(re.compile("alarm-high"))
    sync_api.expect(page.locator(".pill")).to_contain_text("ALARM")
    # the stop tier can be switched off; its number row goes with it
    page.locator('[data-act="drawer-open"]').click()
    page.locator("#drawer-stopon").uncheck()
    sync_api.expect(page.locator("#drawer-stop")).to_have_count(0)
    page.locator(".drawer").get_by_role("button", name="Done").click()
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-display").click()
    page.get_by_role("button", name="Light").click()
    sync_api.expect(page.locator("html")).to_have_attribute("data-theme", "light")
    for tab in ("connection", "calibration", "alarms", "data", "diagnostics"):
        page.locator(f"#nav-{tab}").click()
        page.wait_for_timeout(300)
    assert page.errors == []


def test_rebreathing_marker_splits_the_response_plot(page, site_url):
    boot(page, site_url + "?sim")
    numbers_ok(page)
    page.locator("#tab-response").click()
    sync_api.expect(page.locator("#trend-meta")).to_contain_text("mark “Rebreathing”", timeout=20_000)
    page.keyboard.press("m")
    page.fill("#mark-label", "Rebreathing")
    page.locator('[data-act="mark-save-btn"]').click()
    # breaths after the marker are fitted (once the first 30 s have passed); those before it are the baseline
    sync_api.expect(page.locator("#trend-meta")).to_contain_text(re.compile(r"slope .* · baseline 3[6-9]\.\d mmHg"), timeout=70_000)
    sync_api.expect(page.locator("#trend-meta")).to_contain_text("outside the fit")
    assert page.errors == []


def test_test_keys_trends_and_taking_a_breath_out_by_hand(page, site_url):
    boot(page, site_url + "?sim")
    numbers_ok(page)
    bar = page.locator("#testbar")
    sync_api.expect(bar).to_contain_text("No test running")
    sync_api.expect(bar.get_by_role("button", name="End test")).to_be_disabled()
    bar.get_by_role("button", name="Start baseline").click()
    sync_api.expect(bar).to_contain_text(re.compile(r"BASELINE 0:0\d"))
    sync_api.expect(bar).to_contain_text("NOT RECORDING")
    # the trend tab can plot MV and RR as well as EtCO2
    page.select_option("#trend-series", "mv")
    sync_api.expect(page.locator("#tab-trend")).to_have_text("MV trend")
    sync_api.expect(page.locator("#trend-meta")).to_contain_text("MV (L/min, 60 s average)")
    page.select_option("#trend-series", "rr")
    sync_api.expect(page.locator("#tab-trend")).to_have_text("RR trend")
    page.select_option("#trend-series", "etco2")
    # some baseline, then rebreathing with no skip so the fit starts at once
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-data").click()
    page.fill("#test-skip", "0")
    page.press("#test-skip", "Enter")
    page.fill("#test-weight", "60")
    page.press("#test-weight", "Enter")
    page.get_by_role("button", name="← Back to monitor").click()
    page.wait_for_timeout(8000)
    bar.get_by_role("button", name="Start rebreathing").click()
    sync_api.expect(bar).to_contain_text(re.compile(r"REBREATHING 0:\d\d of 5:00"))
    page.locator("#tab-response").click()
    sync_api.expect(page.locator("#trend-meta")).to_contain_text(re.compile(r"slope .* n \d+ · baseline"), timeout=40_000)
    # a click on a drawn breath takes it out of the fit; clicking its cross puts it back
    hit = page.evaluate("""() => {
        const c = document.getElementById('trend-canvas');
        const [x, y] = c.dataset.points.split(';').pop().split(',').map(Number);
        const r = c.getBoundingClientRect();
        return [r.left + x, r.top + y];
    }""")
    page.mouse.click(hit[0], hit[1])
    sync_api.expect(page.locator("#trend-meta")).to_contain_text("1 excluded by hand")
    bar.get_by_role("button", name="End test").click()
    sync_api.expect(bar).to_contain_text("TEST ENDED after")
    page.mouse.click(hit[0], hit[1])
    sync_api.expect(page.locator("#trend-meta")).not_to_contain_text("excluded by hand")
    assert page.errors == []


def test_record_import_review_and_persistence(page, site_url, tmp_path):
    boot(page, site_url + "?sim")
    numbers_ok(page)
    page.locator('[data-act="record"]').click()
    sync_api.expect(page.locator(".header .rec")).to_contain_text("REC")
    page.wait_for_timeout(1000)
    page.keyboard.press("m")
    sync_api.expect(page.locator("#mark-label")).to_be_visible()
    page.locator('[data-act="mark-keep"]').click()
    page.wait_for_timeout(5000)
    with page.expect_download() as dl:
        page.locator('[data-act="record"]').click()
    d = dl.value
    assert re.fullmatch(r"co2r_STUDY-01_\d{8}-\d{6}\.csv", d.suggested_filename)
    saved = tmp_path / "reimported.csv"
    d.save_as(saved)
    text = saved.read_text()
    assert text.startswith("# CO2-Response session,v4") and "MARK Event 1" in text and "RECORDING_STOPPED" in text

    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-data").click()
    table = page.locator("table.sessions")
    sync_api.expect(table.locator("tbody tr")).to_have_count(1)
    sync_api.expect(table).to_contain_text("Recorded")
    page.set_input_files('input[data-change="import-files"]', str(saved))
    sync_api.expect(table.locator("tbody tr")).to_have_count(2)
    sync_api.expect(table).to_contain_text("reimported.csv")
    bad = tmp_path / "people.csv"
    bad.write_text("name,age\nx,1\n")
    page.set_input_files('input[data-change="import-files"]', str(bad))
    sync_api.expect(page.locator(".toast")).to_contain_text("Could not import people.csv")

    table.locator("tr", has_text="reimported.csv").get_by_role("button", name="Open").click()
    sync_api.expect(page.locator(".review")).to_contain_text("reimported.csv")
    sync_api.expect(page.locator(".review .stats")).to_contain_text("Breaths")
    sync_api.expect(page.locator(".review .loglist")).to_contain_text("MARK Event 1")
    assert page.locator(".review-chart canvas").count() == 3
    with page.expect_download() as dl2:
        page.get_by_role("button", name="Breaths (.csv)", exact=True).click()
    assert dl2.value.suggested_filename == "reimported_breaths.csv"
    # the test report, as a PDF made in the browser
    sync_api.expect(page.locator(".review .stats")).to_contain_text("CO2 response")
    with page.expect_download() as dl3:
        page.get_by_role("button", name="CO2 response report (.pdf)", exact=True).click()
    assert dl3.value.suggested_filename == "reimported_report.pdf"
    pdf = tmp_path / "report.pdf"
    dl3.value.save_as(pdf)
    assert pdf.read_bytes().startswith(b"%PDF-1.4") and pdf.read_bytes().rstrip().endswith(b"%%EOF")

    # sessions survive a reload (kept in the browser's IndexedDB)
    page.wait_for_timeout(1500)
    page.reload()
    page.wait_for_selector(".monitor-screen", timeout=BOOT_MS)
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-data").click()
    sync_api.expect(page.locator("table.sessions tbody tr")).to_have_count(2)
    assert page.errors == []


def test_calibration_edit_preview_save(page, site_url):
    boot(page, site_url + "?sim")
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-calibration").click()
    card = page.locator("section.card.co2")
    sync_api.expect(card).to_contain_text("CO2 (capnograph)")
    page.fill("#co2-gain", "4")
    page.press("#co2-gain", "Enter")
    sync_api.expect(card).to_contain_text("Preview (unsaved)")
    sync_api.expect(card).to_contain_text("Unsaved changes")
    card.get_by_role("button", name="Save calibration").click()
    sync_api.expect(card).to_contain_text("saved ")
    page.wait_for_timeout(1500)
    page.reload()
    page.wait_for_selector(".monitor-screen", timeout=BOOT_MS)
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-calibration").click()
    sync_api.expect(page.locator("#co2-gain")).to_have_value("4")
    assert page.errors == []


def test_arduino_over_web_serial(page, site_url):
    page.add_init_script(FAKE_SERIAL)
    boot(page, site_url)
    sync_api.expect(page.get_by_role("heading", name="No signal source")).to_be_visible()
    page.get_by_role("button", name="Connect Arduino (USB)").click()
    sync_api.expect(page.locator(".header")).to_contain_text("Arduino (USB 2341:0043")
    numbers_ok(page)
    assert page.evaluate("window.__fake.port.commands.slice(0, 2)") == ["INFO", "START"]

    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-diagnostics").click()
    page.get_by_role("button", name="Run hardware check").click()
    sync_api.expect(page.locator(".verdict")).to_be_visible(timeout=25_000)
    checks = page.locator("table.checks")
    sync_api.expect(checks.locator("tr", has_text="Firmware handshake")).to_contain_text("PASS")
    sync_api.expect(checks.locator("tr", has_text="Firmware handshake")).to_contain_text("ADS1115 (I2C 0x48, ±6.144 V range)")
    sync_api.expect(checks.locator("tr", has_text="Dropped samples")).to_contain_text("PASS")
    page.get_by_role("button", name="← Back to monitor").click()

    page.evaluate("window.__fake.port.unplug()")
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("DEVICE DISCONNECTED")
    sync_api.expect(page.get_by_text("Waiting for the Arduino to be plugged back in…")).to_be_visible()
    page.evaluate("window.__fake.port.replug()")
    sync_api.expect(page.locator(".header")).to_contain_text(re.compile(r"Arduino \(USB 2341:0043\)\s*·\s*\d+ Hz"), timeout=15_000)
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("resolved")
    assert page.evaluate("window.__fake.serial.requests") == 1  # no second port picker
    assert page.evaluate("window.__fake.port.opens") == 2
    assert page.errors == []


def test_monitor_on_the_python_acquisition_server(page, site_url):
    """The browser build fed by co2r-daq (python/co2r) running its simulated DAQ: the path
    an NI or LabJack DAQ takes. Skipped when co2r's dependencies are not installed."""
    import socket
    import subprocess
    import sys
    import time

    env = {**os.environ, "PYTHONPATH": str(ROOT / "python"), "PYTHONUNBUFFERED": "1"}
    if subprocess.run([sys.executable, "-c", "import websockets, serial, co2r"], env=env, capture_output=True).returncode != 0:
        pytest.skip("needs the co2r package's dependencies (pip install -e python)")
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    srv = subprocess.Popen(
        [sys.executable, "-m", "co2r", "serve", "--driver", "simulated", "--seed", "1", "--ws-port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        end = time.monotonic() + 20
        while time.monotonic() < end and "serving on" not in (srv.stdout.readline() or ""):
            assert srv.poll() is None, "co2r-daq exited"
        boot(page, site_url)
        page.locator('[data-act="open-settings"]').last.click()
        page.locator("#nav-connection").click()
        page.fill("#bridge-url", f"ws://localhost:{port}")
        page.press("#bridge-url", "Enter")
        page.get_by_role("button", name="Connect to Python").click()
        sync_api.expect(page.locator(".header")).to_contain_text("Simulated DAQ (Python)", timeout=15_000)
        page.wait_for_selector(".monitor-screen")  # connecting returns to the monitor
        numbers_ok(page)
        assert page.errors == []
    finally:
        srv.kill()
        srv.wait()


def test_simulation_panel_and_enlarged_graphs(page, site_url):
    boot(page, site_url)
    page.locator('[data-act="sim-toggle"]').click()
    sync_api.expect(page.locator("#simpanel")).to_be_visible()
    sync_api.expect(page.locator(".sp-start")).to_be_visible()  # not running yet
    page.locator('[data-act="sim-situation"][data-arg="hypoventilation"]').click()
    sync_api.expect(tile(page, "rr")).to_have_text("6", timeout=60_000)
    sync_api.expect(page.locator("#sp-mon-rr")).to_have_text("6")
    sync_api.expect(page.locator("#sp-set-rr")).to_have_text("6")
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("EtCO2 HIGH", timeout=30_000)
    # the speed slider (at the top of the panel) runs simulated time faster
    page.eval_on_selector("#sp-speed", "el => { el.value = 5; el.dispatchEvent(new Event('input', { bubbles: true })); }")
    sync_api.expect(page.locator("#spv-speed")).to_have_text("5 ×")
    sync_api.expect(page.locator("#sp-current")).not_to_contain_text("(adjusted)")
    # dragging a slider changes the running simulator; at 5× the monitor follows in seconds
    page.eval_on_selector("#sp-rr", "el => { el.value = 20; el.dispatchEvent(new Event('input', { bubbles: true })); }")
    sync_api.expect(page.locator("#spv-rr")).to_have_text("20 /min")
    sync_api.expect(page.locator("#sp-current")).to_contain_text("(adjusted)")
    sync_api.expect(tile(page, "rr")).to_have_text("20", timeout=15_000)
    page.locator("#sp-apnea").check()
    sync_api.expect(page.locator("[data-testid=tile-rr]")).to_have_class(re.compile("alarm-high"), timeout=40_000)  # apnea
    # enlarge one graph, then go back with Esc
    page.locator("#expand-co2").click()
    sync_api.expect(page.locator("#monscreen")).to_have_attribute("data-focus", "co2")
    sync_api.expect(page.locator("#row-flow")).to_be_hidden()
    sync_api.expect(page.locator(".trend")).to_be_hidden()
    page.keyboard.press("Escape")
    sync_api.expect(page.locator("#row-flow")).to_be_visible()
    page.locator("#expand-trend").click()
    sync_api.expect(page.locator(".monitor")).to_be_hidden()
    page.locator("#expand-trend").click()
    sync_api.expect(page.locator(".monitor")).to_be_visible()
    page.locator('[data-act="sim-close"]').click()
    sync_api.expect(page.locator("#simpanel")).to_be_hidden()
    assert page.errors == []


def test_site_starts_without_internet_after_one_visit(browser, site_url):
    """The service worker stores the site and the Python runtime on the first visit."""
    ctx = browser.new_context(viewport={"width": 1366, "height": 800})
    page = ctx.new_page()
    try:
        boot(page, site_url + "?sim")
        sync_api.expect(page.locator("html")).to_have_attribute("data-offline", "ready", timeout=60_000)
        ctx.set_offline(True)
        page.reload()
        page.wait_for_selector(".monitor-screen", timeout=BOOT_MS)
        numbers_ok(page)  # the simulator runs: Python, numpy and the package all came from the stored copy
    finally:
        ctx.close()


def test_vendored_build_fetches_nothing_from_the_cdn(tmp_path):
    """--vendor-pyodide copies the runtime into the site for a network with no internet."""
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.build(tmp_path / "site")
    assert (out / "sw.js").exists() and "cdn.jsdelivr.net/pyodide" in (out / "index.html").read_text()
    asked = []

    def fetch(url: str) -> bytes:
        if url.endswith("pyodide.asm.js"):
            raise OSError("404")  # this version ships pyodide.asm.mjs
        asked.append(url.rsplit("/", 1)[1])
        if url.endswith("pyodide-lock.json"):
            return json.dumps({"packages": {"numpy": {"file_name": "numpy-2.whl", "depends": ["dep"]}, "dep": {"file_name": "dep-1.whl", "depends": []}}}).encode()
        return b"x"

    names = mod.vendor(out, fetch)
    assert sorted(names) == sorted(asked) and {"pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "numpy-2.whl", "dep-1.whl"} <= set(names)
    assert all((out / "pyodide" / n).exists() for n in names)
    html = (out / "index.html").read_text()
    assert "cdn.jsdelivr.net" not in html and 'src="pyodide/pyodide.js"' in html
