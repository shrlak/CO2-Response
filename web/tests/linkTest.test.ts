import { describe, expect, it } from 'vitest';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import { analyzeLinkTest, LinkTestCollector, noiseVolts, overall, reportText, type LinkStats } from '../src/engine/linkTest';
import { parseLine, SeqTracker } from '../src/protocol/protocol';
import type { Scenario } from '../src/sim/physiology';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { defaultEncoders } from './helpers';

const INFO = { FW: '1.0.0', ADC: 'ADS1115', CH: '3', RATE: '100', ADDR: '0x48', FSR: '6.144' };

/** Run the simulator through the collector the way the engine does. */
function check(scenario: Scenario, seconds = 20, patch: Partial<LinkStats> = {}) {
  const channels = cloneChannels(DEFAULT_CHANNELS);
  const core = new SimulatorCore(defaultEncoders, { scenario, seed: 4 });
  const col = new LinkTestCollector(channels);
  const seq = new SeqTracker();
  let bad = 0;
  for (let i = 0; i < seconds * 100; i++) {
    for (const l of core.nextSample()) {
      const d = parseLine(l);
      if (d.kind === 'bad') bad++;
      if (d.kind !== 'data') continue;
      seq.push(d.seq, d.tMs);
      col.sample(d.tMs / 1000, d.volts);
    }
  }
  const stats: LinkStats = { seconds, spanSec: col.spanSec, received: seq.received, dropped: seq.dropped, badLines: bad, info: INFO, adcErrors: 0, i2cScan: null, channels: col.stats(), ...patch };
  const r = analyzeLinkTest(stats, channels);
  return { r, by: (id: string) => r.find((x) => x.id === id)! };
}

describe('hardware check', () => {
  it('passes a healthy stream', () => {
    const { r, by } = check('normal');
    expect(by('handshake')).toMatchObject({ status: 'pass' });
    expect(by('handshake').result).toContain('ADS1115 (I2C 0x48, ±6.144 V range)');
    expect(by('rate')).toMatchObject({ status: 'pass' });
    expect(by('rate').result).toMatch(/^100\.0 Hz/);
    expect(by('drops')).toMatchObject({ status: 'pass' });
    expect(by('corrupt')).toMatchObject({ status: 'pass', result: 'None' });
    for (const id of ['ch-co2', 'ch-flow', 'ch-pneumo']) expect(by(id).status).toBe('pass');
    expect(overall(r)).toBe('pass');
  });

  it('flags dropped samples, corrupt lines and noise on a bad link', () => {
    const { by } = check('noisy', 60);
    expect(by('drops').status).not.toBe('pass');
    expect(by('corrupt').status).toBe('fail');
    expect(by('ch-flow').status).toBe('warn');
    expect(by('ch-flow').result).toMatch(/^Noisy: noise 1\.\d+ L\/min/);
    expect(by('ch-flow').fix).toMatch(/ground/);
  });

  it('fails a CO2 channel stuck at the rail (unplugged capnograph)', () => {
    const { r, by } = check('co2Disconnect');
    expect(by('ch-co2')).toMatchObject({ status: 'fail' });
    expect(by('ch-co2').result).toMatch(/^Stuck at the top/);
    expect(by('ch-co2').fix).toMatch(/pull-up/);
    expect(by('ch-flow').status).toBe('pass');
    expect(overall(r)).toBe('fail');
  });

  it('says what to do when the ADC is at another I2C address', () => {
    const { by } = check('noData', 5, { adcErrors: 5, i2cScan: '0x49', info: { FW: '1.0.0', ADC: 'ADS1115', CH: '3', RATE: '100', ADDR: '0x48' } });
    expect(by('adc').status).toBe('fail');
    expect(by('adc').fix).toMatch(/answers at 0x49 but the firmware looks for 0x48/);
    expect(by('data').status).toBe('fail');
  });

  it('says to check power and SDA/SCL when nothing answers on I2C', () => {
    const { by } = check('noData', 5, { adcErrors: 3, i2cScan: 'none' });
    expect(by('adc').fix).toMatch(/^Nothing answers on the I2C bus/);
    expect(by('adc').fix).toMatch(/SDA to A4 and SCL to A5/);
  });

  it('flags a missing handshake and an input range smaller than the configured rails', () => {
    expect(check('normal', 5, { info: null }).by('handshake')).toMatchObject({ status: 'warn' });
    expect(check('normal', 5, { info: { ...INFO, FSR: '4.096' } }).by('fsr')).toMatchObject({ status: 'warn' });
  });

  it('estimates white-noise sigma from second differences', () => {
    let seed = 1;
    const gauss = () => {
      // Box-Muller with a small LCG
      seed = (seed * 16807) % 2147483647;
      const u = seed / 2147483647;
      seed = (seed * 16807) % 2147483647;
      const v = seed / 2147483647;
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    };
    const x = Array.from({ length: 20000 }, (_, i) => Math.sin(i / 200) + 0.002 * gauss());
    const d2 = x.slice(2).map((v, i) => Math.abs(v - 2 * x[i + 1] + x[i]));
    expect(noiseVolts(d2)).toBeGreaterThan(0.0018);
    expect(noiseVolts(d2)).toBeLessThan(0.0022);
  });

  it('writes a plain-text report', () => {
    const { r } = check('co2Disconnect', 5);
    const t = reportText(r, 5, new Date('2026-11-05T05:00:00Z'));
    expect(t).toMatch(/^CO2-Response hardware check, 2026-11-05T05:00:00.000Z, 5 s\nOverall: FAIL/);
    expect(t).toContain('[FAIL] CO2 sensor (A0)');
  });
});
