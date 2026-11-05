import type { ChannelId, ChannelsConfig } from '../config/channels';
import { applyCalibration } from '../processing/calibration';
import { RailMonitor } from '../processing/quality';

/**
 * Hardware check: watch the live stream for a while and judge the USB link and the
 * wiring of each sensor. Collection is incremental; analysis is a pure function so it
 * can be tested without hardware.
 */

export type CheckStatus = 'pass' | 'warn' | 'fail' | 'info';

export interface CheckResult {
  id: string;
  label: string;
  status: CheckStatus;
  result: string;
  fix?: string;
}

export interface ChannelStats {
  n: number;
  min: number;
  max: number;
  sum: number;
  atRail: number;
  /** |second differences| of the volts, for a noise estimate that ignores slow breathing */
  d2: number[];
}

export interface LinkStats {
  seconds: number;
  /** device-clock span of the samples received, s */
  spanSec: number;
  received: number;
  dropped: number;
  badLines: number;
  info: Record<string, string> | null;
  adcErrors: number;
  i2cScan: string | null;
  /** reason sent by a Python DAQ driver with ADC_NOT_FOUND */
  adcDetail?: string | null;
  channels: Record<ChannelId, ChannelStats>;
}

const IDS: ChannelId[] = ['co2', 'flow', 'pneumo'];
const NAMES: Record<ChannelId, string> = { co2: 'CO2 sensor (A0)', flow: 'Flow sensor (A1)', pneumo: 'Pneumotach transducer (A2)' };
const MAX_D2 = 20000;

export class LinkTestCollector {
  private st: Record<ChannelId, ChannelStats>;
  private prev: Record<ChannelId, [number, number] | null> = { co2: null, flow: null, pneumo: null };
  private t0: number | null = null;
  private t1 = 0;
  private lastT = -Infinity;
  private span = 0;
  private stride = 1;

  constructor(private channels: ChannelsConfig) {
    const blank = (): ChannelStats => ({ n: 0, min: Infinity, max: -Infinity, sum: 0, atRail: 0, d2: [] });
    this.st = { co2: blank(), flow: blank(), pneumo: blank() };
  }

  sample(tSec: number, volts: readonly number[]): void {
    // device clock restarts on a reset: add up the spans
    if (this.t0 === null || tSec < this.lastT) {
      if (this.t0 !== null) this.span += this.t1 - this.t0;
      this.t0 = tSec;
    }
    this.t1 = tSec;
    this.lastT = tSec;
    const a = this.channels.adc;
    for (const id of IDS) {
      const ch = this.channels.channels[id];
      const v = volts[ch.index];
      if (!Number.isFinite(v)) continue;
      const s = this.st[id];
      s.n++;
      s.sum += v;
      if (v < s.min) s.min = v;
      if (v > s.max) s.max = v;
      if (RailMonitor.atRail(v, a.railLowV, a.railHighV, a.railToleranceV, ch.railCheck ?? 'both')) s.atRail++;
      const p = this.prev[id];
      if (p && s.n % this.stride === 0) {
        s.d2.push(Math.abs(v - 2 * p[0] + p[1]));
        if (s.d2.length >= MAX_D2) {
          // keep memory bounded on long runs: thin out and sample less often
          s.d2 = s.d2.filter((_, i) => i % 2 === 0);
          this.stride *= 2;
        }
      }
      this.prev[id] = [v, p ? p[0] : v];
    }
  }

  get spanSec(): number {
    return this.span + (this.t0 === null ? 0 : this.t1 - this.t0);
  }

  stats(): Record<ChannelId, ChannelStats> {
    return this.st;
  }
}

function median(xs: number[]): number {
  if (!xs.length) return NaN;
  const v = [...xs].sort((a, b) => a - b);
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

/** σ of white noise from the median |second difference|: median|N(0, 6σ²)| = 0.6745·√6·σ */
export const noiseVolts = (d2: number[]): number => median(d2) / 1.6522;

const pct = (x: number) => `${(x * 100).toFixed(x < 0.01 ? 3 : 1)} %`;

export function analyzeLinkTest(s: LinkStats, channels: ChannelsConfig, barometricMmHg = 760): CheckResult[] {
  const out: CheckResult[] = [];
  const info = s.info;

  // --- device and ADC
  if (s.adcErrors > 0) {
    const scan = s.i2cScan?.trim();
    let fix = 'Check the ADC board: VDD to 5V, GND to GND, SDA to A4 and SCL to A5 on an Uno or Nano (20/21 on a Mega).';
    if (scan && scan !== 'none') {
      const want = info?.ADDR ?? '0x48';
      fix = `Something answers at ${scan} but the firmware looks for ${want}. An ADS1115 is at 0x48 with ADDR to GND, 0x49 to VDD, 0x4A to SDA, 0x4B to SCL. Wire ADDR to GND, or set ADS_I2C_ADDRESS in config.h and reflash.`;
    } else if (scan === 'none') {
      fix = `Nothing answers on the I2C bus. ${fix}`;
    }
    if (s.adcDetail) fix = `${s.adcDetail}. Check that the DAQ is plugged in, its vendor driver is installed, and the device name in the co2r-daq settings matches (run co2r-daq list).`;
    out.push({ id: 'adc', label: s.adcDetail ? 'DAQ device' : 'ADC board', status: 'fail', result: s.adcDetail ? 'The Python DAQ driver cannot open the device' : `Firmware reports ADC_NOT_FOUND${scan ? ` (I2C scan: ${scan})` : ''}`, fix });
  }

  if (!info) {
    out.push({
      id: 'handshake',
      label: 'Firmware handshake',
      status: s.received ? 'warn' : 'fail',
      result: 'No INFO reply from the device',
      fix: 'Check that the CO2-Response firmware is flashed and that the baud rate here matches BAUD_RATE in config.h (115200 by default). Close the Arduino IDE Serial Monitor: only one program can use the port.',
    });
  } else {
    const extra = [info.ADDR ? `I2C ${info.ADDR}` : '', info.FSR ? `±${info.FSR} V range` : ''].filter(Boolean).join(', ');
    out.push({ id: 'handshake', label: 'Firmware handshake', status: 'pass', result: `Firmware ${info.FW ?? '?'}, ${info.ADC ?? '?'}${extra ? ` (${extra})` : ''}, ${info.CH ?? '?'} channels at ${info.RATE ?? '?'} Hz` });
    const fsr = Number(info.FSR);
    if (Number.isFinite(fsr) && fsr + 1e-6 < channels.adc.railHighV) {
      out.push({
        id: 'fsr',
        label: 'Input range',
        status: 'warn',
        result: `The ADC full scale is ${fsr} V but channels.json expects signals up to ${channels.adc.railHighV} V`,
        fix: `Signals above ${fsr} V will clip. Use GAIN_TWOTHIRDS in config.h for 0–5 V sensors, or set railHighV to ${fsr} in channels.json for 0–3.3 V sensors.`,
      });
    }
  }

  // --- link
  if (!s.received) {
    out.push({ id: 'data', label: 'Data stream', status: 'fail', result: `No data lines in ${s.seconds.toFixed(0)} s`, fix: 'Press Connect again. If the device is connected but silent, it may be waiting for the ADC (see above) or stopped: use Settings > Connection > Ask INFO, then reconnect.' });
    return out;
  }
  const expected = Number(info?.RATE) || 100;
  const measured = s.spanSec > 0 ? (s.received + s.dropped - 1) / s.spanSec : 0;
  const rateErr = Math.abs(measured - expected) / expected;
  out.push({
    id: 'rate',
    label: 'Sample rate',
    status: rateErr <= 0.02 ? 'pass' : rateErr <= 0.1 ? 'warn' : 'fail',
    result: `${measured.toFixed(1)} Hz per channel (device set to ${expected} Hz)`,
    fix: rateErr > 0.02 ? 'The Arduino cannot keep up. Lower the rate (Settings > Connection), or check for E,OVERRUN in Diagnostics.' : undefined,
  });
  const dr = s.dropped / (s.received + s.dropped);
  out.push({
    id: 'drops',
    label: 'Dropped samples',
    status: dr < 0.001 ? 'pass' : dr < 0.01 ? 'warn' : 'fail',
    result: `${s.dropped} of ${s.received + s.dropped} (${pct(dr)}); target under 0.1 %`,
    fix: dr >= 0.001 ? 'Plug the Arduino straight into the computer (not a hub), use a short data-grade USB cable, keep this tab in front, and close other programs that use the port.' : undefined,
  });
  const br = s.badLines / (s.received + s.badLines);
  out.push({
    id: 'corrupt',
    label: 'Corrupt lines',
    status: s.badLines === 0 ? 'pass' : br < 0.001 ? 'warn' : 'fail',
    result: s.badLines === 0 ? 'None' : `${s.badLines} (${pct(br)}) failed the checksum`,
    fix: s.badLines ? 'Usually a baud-rate mismatch or electrical noise on the USB cable. Match the baud rate, use a shorter cable, keep it away from pumps and heaters.' : undefined,
  });

  // --- each sensor channel
  for (const id of IDS) {
    const c = s.channels[id];
    const ch = channels.channels[id];
    const name = NAMES[id];
    if (!c.n) {
      out.push({ id: `ch-${id}`, label: name, status: 'fail', result: 'No values', fix: 'The device sent fewer channels than expected. Check CH in the firmware INFO.' });
      continue;
    }
    const mean = c.sum / c.n;
    const railFrac = c.atRail / c.n;
    const span = c.max - c.min;
    const noiseV = noiseVolts(c.d2);
    // engineering units per volt at the operating point
    const dv = 0.01;
    let perVolt = Math.abs(applyCalibration(ch.calibration, mean + dv) - applyCalibration(ch.calibration, mean - dv)) / (2 * dv);
    let unit = ch.unit;
    let warnAt = 1;
    let failAt = 3;
    if (id === 'co2') {
      perVolt *= barometricMmHg / 100; // % → mmHg
      unit = 'mmHg';
      warnAt = 0.5;
      failAt = 2;
    } else if (id === 'pneumo') {
      const fm = ch.flowModel ?? { k1: 0.2, k2: 0 };
      const pa = applyCalibration(ch.calibration, mean) - ch.zeroOffset;
      perVolt *= Math.abs(fm.k1 + 2 * fm.k2 * Math.abs(pa)); // Pa → L/min
      unit = 'L/min';
    }
    const noise = noiseV * perVolt;
    const where = `${mean.toFixed(3)} V average, ${c.min.toFixed(3)}–${c.max.toFixed(3)} V`;

    if (railFrac > 0.5) {
      out.push({
        id: `ch-${id}`,
        label: name,
        status: 'fail',
        result: `Stuck at the ${mean > channels.adc.railHighV / 2 ? 'top' : 'bottom'} of the ADC range (${where})`,
        fix:
          id === 'co2'
            ? 'The capnograph output is missing or not powered (the pull-up holds A0 high), or its output is above 5 V. Check its analog-out cable, its ground to the Arduino ground, and its output setting.'
            : 'The sensor is unplugged, not powered, or its output is outside 0–5 V. Check its 5 V and ground, and the signal wire to the ADC input.',
      });
      continue;
    }
    if (railFrac > 0.01) {
      out.push({ id: `ch-${id}-clip`, label: name, status: 'warn', result: `Touches the ADC limit in ${pct(railFrac)} of samples (${where})`, fix: 'The signal is clipping. Check the sensor range, or add a divider and put its ratio into the calibration.' });
    }
    const noiseText = `noise ${noise < 0.01 ? noise.toExponential(1) : noise.toFixed(2)} ${unit} (${(noiseV * 1000).toFixed(2)} mV)`;
    if (noise >= failAt || noise >= warnAt) {
      out.push({
        id: `ch-${id}`,
        label: name,
        status: noise >= failAt ? 'fail' : 'warn',
        result: `Noisy: ${noiseText}; ${where}`,
        fix: 'Join all grounds at one point, twist the signal wire with its ground (or use shielded cable), keep it away from pumps, heaters and mains wiring, and keep the filter on.',
      });
    } else if (span < 0.005) {
      out.push({ id: `ch-${id}`, label: name, status: 'info', result: `Steady, no change seen (${where}); ${noiseText}`, fix: 'Fine if nothing is breathing through the sensor yet. Breathe through it to check it responds.' });
    } else {
      out.push({ id: `ch-${id}`, label: name, status: 'pass', result: `${where}; ${noiseText}` });
    }
  }
  return out;
}

export function overall(results: CheckResult[]): CheckStatus {
  if (results.some((r) => r.status === 'fail')) return 'fail';
  if (results.some((r) => r.status === 'warn')) return 'warn';
  return 'pass';
}

export function reportText(results: CheckResult[], seconds: number, when = new Date()): string {
  const lines = [`CO2-Response hardware check, ${when.toISOString()}, ${seconds} s`, `Overall: ${overall(results).toUpperCase()}`, ''];
  for (const r of results) {
    lines.push(`[${r.status.toUpperCase()}] ${r.label}: ${r.result}`);
    if (r.fix) lines.push(`        ${r.fix}`);
  }
  return lines.join('\n') + '\n';
}
