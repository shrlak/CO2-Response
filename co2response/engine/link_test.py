"""Hardware check: watch the live stream for a while and judge the USB link and the
wiring of each sensor. Collection is incremental; the analysis is a pure function so it
can be tested without hardware."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..channels import CHANNEL_IDS, DEFAULT_FLOW_MODEL
from ..jsfmt import iso_ms, js_num, to_exponential, to_fixed
from ..processing.calibration import apply_calibration
from ..processing.quality import at_rail

NAMES = {"co2": "CO2 sensor (A0)", "flow": "Flow sensor (A1)", "pneumo": "Pneumotach transducer (A2)"}
_MAX_D2 = 20000


@dataclass
class CheckResult:
    id: str
    label: str
    #: 'pass' | 'warn' | 'fail' | 'info'
    status: str
    result: str
    fix: str | None = None


@dataclass
class ChannelStats:
    n: int = 0
    min: float = math.inf
    max: float = -math.inf
    sum: float = 0.0
    at_rail: int = 0
    #: |second differences| of the volts, for a noise estimate that ignores slow breathing
    d2: list[float] = field(default_factory=list)


@dataclass
class LinkStats:
    seconds: float
    #: device-clock span of the samples received, s
    span_sec: float
    received: int
    dropped: int
    bad_lines: int
    info: dict[str, str] | None
    adc_errors: int
    i2c_scan: str | None
    channels: dict[str, ChannelStats]


class LinkTestCollector:
    def __init__(self, channels: dict) -> None:
        self.channels = channels
        self._st = {cid: ChannelStats() for cid in CHANNEL_IDS}
        self._prev: dict[str, tuple[float, float] | None] = {cid: None for cid in CHANNEL_IDS}
        self._t0: float | None = None
        self._t1 = 0.0
        self._last_t = -math.inf
        self._span = 0.0
        self._stride = 1

    def sample(self, t_sec: float, volts: list[float]) -> None:
        # the device clock restarts on a reset: add up the spans
        if self._t0 is None or t_sec < self._last_t:
            if self._t0 is not None:
                self._span += self._t1 - self._t0
            self._t0 = t_sec
        self._t1 = t_sec
        self._last_t = t_sec
        a = self.channels["adc"]
        for cid in CHANNEL_IDS:
            ch = self.channels["channels"][cid]
            i = int(ch["index"])
            v = volts[i] if i < len(volts) else math.nan
            if not math.isfinite(v):
                continue
            s = self._st[cid]
            s.n += 1
            s.sum += v
            s.min = min(s.min, v)
            s.max = max(s.max, v)
            if at_rail(v, a["railLowV"], a["railHighV"], a["railToleranceV"], ch.get("railCheck", "both")):
                s.at_rail += 1
            p = self._prev[cid]
            if p is not None and s.n % self._stride == 0:
                s.d2.append(abs(v - 2 * p[0] + p[1]))
                if len(s.d2) >= _MAX_D2:
                    # keep memory bounded on long runs: thin out and sample less often
                    s.d2 = s.d2[::2]
                    self._stride *= 2
            self._prev[cid] = (v, p[0] if p else v)

    @property
    def span_sec(self) -> float:
        return self._span + (0 if self._t0 is None else self._t1 - self._t0)

    def stats(self) -> dict[str, ChannelStats]:
        return self._st


def _median(xs: list[float]) -> float:
    if not xs:
        return math.nan
    v = sorted(xs)
    m = len(v) >> 1
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def noise_volts(d2: list[float]) -> float:
    """σ of white noise from the median |second difference|: median|N(0, 6σ²)| = 0.6745·√6·σ."""
    return _median(d2) / 1.6522


def _pct(x: float) -> str:
    return f"{to_fixed(x * 100, 3 if x < 0.01 else 1)} %"


def analyze_link_test(s: LinkStats, channels: dict, barometric_mmhg: float = 760) -> list[CheckResult]:
    out: list[CheckResult] = []
    info = s.info

    # --- device and ADC
    if s.adc_errors > 0:
        scan = (s.i2c_scan or "").strip() or None
        fix = "Check the ADC board: VDD to 5V, GND to GND, SDA to A4 and SCL to A5 on an Uno or Nano (20/21 on a Mega)."
        if scan and scan != "none":
            want = (info or {}).get("ADDR", "0x48")
            fix = (
                f"Something answers at {scan} but the firmware looks for {want}. An ADS1115 is at 0x48 with ADDR to GND, "
                "0x49 to VDD, 0x4A to SDA, 0x4B to SCL. Wire ADDR to GND, or set ADS_I2C_ADDRESS in config.h and reflash."
            )
        elif scan == "none":
            fix = f"Nothing answers on the I2C bus. {fix}"
        out.append(CheckResult("adc", "ADC board", "fail", f"Firmware reports ADC_NOT_FOUND{f' (I2C scan: {scan})' if scan else ''}", fix))

    if not info:
        out.append(
            CheckResult(
                "handshake",
                "Firmware handshake",
                "warn" if s.received else "fail",
                "No INFO reply from the device",
                "Check that the CO2-Response firmware is flashed and that the baud rate here matches BAUD_RATE in config.h "
                "(115200 by default). Close the Arduino IDE Serial Monitor: only one program can use the port.",
            )
        )
    else:
        extra = ", ".join(x for x in (f"I2C {info['ADDR']}" if info.get("ADDR") else "", f"±{info['FSR']} V range" if info.get("FSR") else "") if x)
        out.append(
            CheckResult(
                "handshake",
                "Firmware handshake",
                "pass",
                f"Firmware {info.get('FW', '?')}, {info.get('ADC', '?')}{f' ({extra})' if extra else ''}, {info.get('CH', '?')} channels at {info.get('RATE', '?')} Hz",
            )
        )
        try:
            fsr = float(info.get("FSR", ""))
        except ValueError:
            fsr = math.nan
        rail_high = channels["adc"]["railHighV"]
        if math.isfinite(fsr) and fsr + 1e-6 < rail_high:
            out.append(
                CheckResult(
                    "fsr",
                    "Input range",
                    "warn",
                    f"The ADC full scale is {js_num(fsr)} V but channels.json expects signals up to {js_num(rail_high)} V",
                    f"Signals above {js_num(fsr)} V will clip. Use GAIN_TWOTHIRDS in config.h for 0–5 V sensors, or set railHighV to {js_num(fsr)} in channels.json for 0–3.3 V sensors.",
                )
            )

    # --- link
    if not s.received:
        out.append(
            CheckResult(
                "data",
                "Data stream",
                "fail",
                f"No data lines in {to_fixed(s.seconds, 0)} s",
                "Press Connect again. If the device is connected but silent, it may be waiting for the ADC (see above) or stopped: "
                "use Settings > Connection > Ask INFO, then reconnect.",
            )
        )
        return out
    try:
        expected = float((info or {}).get("RATE", "")) or 100
    except ValueError:
        expected = 100
    if not math.isfinite(expected):
        expected = 100
    measured = (s.received + s.dropped - 1) / s.span_sec if s.span_sec > 0 else 0
    rate_err = abs(measured - expected) / expected
    out.append(
        CheckResult(
            "rate",
            "Sample rate",
            "pass" if rate_err <= 0.02 else "warn" if rate_err <= 0.1 else "fail",
            f"{to_fixed(measured, 1)} Hz per channel (device set to {js_num(expected)} Hz)",
            "The Arduino cannot keep up. Lower the rate (Settings > Connection), or check for E,OVERRUN in Diagnostics." if rate_err > 0.02 else None,
        )
    )
    dr = s.dropped / (s.received + s.dropped)
    out.append(
        CheckResult(
            "drops",
            "Dropped samples",
            "pass" if dr < 0.001 else "warn" if dr < 0.01 else "fail",
            f"{s.dropped} of {s.received + s.dropped} ({_pct(dr)}); target under 0.1 %",
            "Plug the Arduino straight into the computer (not a hub), use a short data-grade USB cable, and close other programs that use the port."
            if dr >= 0.001
            else None,
        )
    )
    br = s.bad_lines / (s.received + s.bad_lines)
    out.append(
        CheckResult(
            "corrupt",
            "Corrupt lines",
            "pass" if s.bad_lines == 0 else "warn" if br < 0.001 else "fail",
            "None" if s.bad_lines == 0 else f"{s.bad_lines} ({_pct(br)}) failed the checksum",
            "Usually a baud-rate mismatch or electrical noise on the USB cable. Match the baud rate, use a shorter cable, keep it away from pumps and heaters."
            if s.bad_lines
            else None,
        )
    )

    # --- each sensor channel
    for cid in CHANNEL_IDS:
        c = s.channels[cid]
        ch = channels["channels"][cid]
        name = NAMES[cid]
        if not c.n:
            out.append(CheckResult(f"ch-{cid}", name, "fail", "No values", "The device sent fewer channels than expected. Check CH in the firmware INFO."))
            continue
        mean = c.sum / c.n
        rail_frac = c.at_rail / c.n
        span = c.max - c.min
        noise_v = noise_volts(c.d2)
        # engineering units per volt at the operating point
        dv = 0.01
        per_volt = abs(apply_calibration(ch["calibration"], mean + dv) - apply_calibration(ch["calibration"], mean - dv)) / (2 * dv)
        unit = ch.get("unit", "")
        warn_at, fail_at = 1.0, 3.0
        if cid == "co2":
            per_volt *= barometric_mmhg / 100  # % → mmHg
            unit = "mmHg"
            warn_at, fail_at = 0.5, 2.0
        elif cid == "pneumo":
            fm = ch.get("flowModel") or DEFAULT_FLOW_MODEL
            pa = apply_calibration(ch["calibration"], mean) - ch["zeroOffset"]
            per_volt *= abs(fm["k1"] + 2 * fm["k2"] * abs(pa))  # Pa → L/min
            unit = "L/min"
        noise = noise_v * per_volt
        where = f"{to_fixed(mean, 3)} V average, {to_fixed(c.min, 3)}–{to_fixed(c.max, 3)} V"

        if rail_frac > 0.5:
            top = mean > channels["adc"]["railHighV"] / 2
            out.append(
                CheckResult(
                    f"ch-{cid}",
                    name,
                    "fail",
                    f"Stuck at the {'top' if top else 'bottom'} of the ADC range ({where})",
                    "The capnograph output is missing or not powered (the pull-up holds A0 high), or its output is above 5 V. "
                    "Check its analog-out cable, its ground to the Arduino ground, and its output setting."
                    if cid == "co2"
                    else "The sensor is unplugged, not powered, or its output is outside 0–5 V. Check its 5 V and ground, and the signal wire to the ADC input.",
                )
            )
            continue
        if rail_frac > 0.01:
            out.append(
                CheckResult(
                    f"ch-{cid}-clip",
                    name,
                    "warn",
                    f"Touches the ADC limit in {_pct(rail_frac)} of samples ({where})",
                    "The signal is clipping. Check the sensor range, or add a divider and put its ratio into the calibration.",
                )
            )
        noise_s = to_exponential(noise, 1) if noise < 0.01 else to_fixed(noise, 2)
        noise_text = f"noise {noise_s} {unit} ({to_fixed(noise_v * 1000, 2)} mV)"
        if noise >= fail_at or noise >= warn_at:
            out.append(
                CheckResult(
                    f"ch-{cid}",
                    name,
                    "fail" if noise >= fail_at else "warn",
                    f"Noisy: {noise_text}; {where}",
                    "Join all grounds at one point, twist the signal wire with its ground (or use shielded cable), keep it away from pumps, heaters and mains wiring, and keep the filter on.",
                )
            )
        elif span < 0.005:
            out.append(
                CheckResult(
                    f"ch-{cid}",
                    name,
                    "info",
                    f"Steady, no change seen ({where}); {noise_text}",
                    "Fine if nothing is breathing through the sensor yet. Breathe through it to check it responds.",
                )
            )
        else:
            out.append(CheckResult(f"ch-{cid}", name, "pass", f"{where}; {noise_text}"))
    return out


def overall(results: list[CheckResult]) -> str:
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "pass"


def report_text(results: list[CheckResult], seconds: float, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    lines = [f"CO2-Response hardware check, {iso_ms(when.timestamp() * 1000)}, {js_num(seconds)} s", f"Overall: {overall(results).upper()}", ""]
    for r in results:
        lines.append(f"[{r.status.upper()}] {r.label}: {r.result}")
        if r.fix:
            lines.append(f"        {r.fix}")
    return "\n".join(lines) + "\n"
