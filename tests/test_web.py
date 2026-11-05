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
    # lower the high limit below the simulated 38 mmHg: EtCO2 HIGH after two breaths
    page.fill("#drawer-high", "30")
    page.press("#drawer-high", "Enter")
    page.locator(".drawer").get_by_role("button", name="Done").click()
    sync_api.expect(page.locator(".alarm-slot")).to_contain_text("EtCO2 HIGH", timeout=30_000)
    sync_api.expect(page.locator("[data-testid=tile-etco2]")).to_have_class(re.compile("alarm-high"))
    page.locator(".alarm-slot .slot-btn").click()
    sync_api.expect(page.locator(".pill")).to_contain_text("SILENCED")
    page.locator('[data-act="open-settings"]').last.click()
    page.locator("#nav-display").click()
    page.get_by_role("button", name="Light").click()
    sync_api.expect(page.locator("html")).to_have_attribute("data-theme", "light")
    for tab in ("connection", "calibration", "alarms", "data", "diagnostics"):
        page.locator(f"#nav-{tab}").click()
        page.wait_for_timeout(300)
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
    assert text.startswith("# CO2-Response session,v3") and "MARK Event 1" in text and "RECORDING_STOPPED" in text

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
