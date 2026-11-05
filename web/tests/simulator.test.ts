import { describe, expect, it } from 'vitest';
import { parseLine, SeqTracker } from '../src/protocol/protocol';
import { PhysioModel } from '../src/sim/physiology';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { defaultEncoders } from './helpers';

function run(core: SimulatorCore, seconds: number) {
  const out: ReturnType<typeof parseLine>[] = [];
  for (let i = 0; i < seconds * core.rateHz; i++) for (const l of core.nextSample()) out.push(parseLine(l));
  return out;
}

describe('PhysioModel', () => {
  it('breathes at the set rate', () => {
    const m = new PhysioModel({ rr: 15 });
    let starts = 0;
    let prev = 0;
    for (let i = 0; i < 60 * 100; i++) {
      const f = m.step(0.01).flowLpm;
      if (prev <= 3 && f > 3) starts++;
      prev = f;
    }
    expect(starts).toBeGreaterThanOrEqual(14);
    expect(starts).toBeLessThanOrEqual(16);
  });

  it('peaks at the set end-tidal CO2', () => {
    const m = new PhysioModel({ etco2: 40, barometricMmHg: 760 });
    let peak = 0;
    for (let i = 0; i < 20 * 100; i++) peak = Math.max(peak, ((m.step(0.01).co2Pct ?? 0) / 100) * 760);
    expect(peak).toBeGreaterThan(39.3);
    expect(peak).toBeLessThan(41);
  });

  it('raises CO2 in the ramp scenario', () => {
    const m = new PhysioModel({ scenario: 'co2Ramp', rampMmHgPerMin: 15 });
    for (let i = 0; i < 60 * 100; i++) m.step(0.01);
    expect(m.targetEtco2).toBeCloseTo(38 + 15, 0);
  });

  it('stops flow during apnea', () => {
    const m = new PhysioModel({ scenario: 'apnea' });
    let maxFlow = 0;
    for (let i = 0; i < 10 * 100; i++) maxFlow = Math.max(maxFlow, Math.abs(m.step(0.01).flowLpm));
    expect(maxFlow).toBeLessThan(2);
  });
});

describe('SimulatorCore', () => {
  it('emits valid, gap-free data lines at 100 Hz', () => {
    const core = new SimulatorCore(defaultEncoders);
    const lines = run(core, 5);
    expect(lines).toHaveLength(500);
    const seq = new SeqTracker();
    for (const l of lines) {
      expect(l.kind).toBe('data');
      if (l.kind === 'data') {
        seq.push(l.seq);
        expect(l.volts).toHaveLength(3);
        for (const v of l.volts) expect(v).toBeGreaterThanOrEqual(0);
      }
    }
    expect(seq.dropped).toBe(0);
  });

  it('puts a disconnected CO2 sensor on the high rail (pull-up)', () => {
    const core = new SimulatorCore(defaultEncoders, { scenario: 'co2Disconnect' });
    for (const l of run(core, 1)) if (l.kind === 'data') expect(l.volts[0]).toBe(5);
  });

  it('sends nothing in the no-data scenario', () => {
    const core = new SimulatorCore(defaultEncoders, { scenario: 'noData' });
    expect(run(core, 1)).toHaveLength(0);
  });

  it('injects bad checksums and dropped samples in the noisy scenario', () => {
    const core = new SimulatorCore(defaultEncoders, { scenario: 'noisy', seed: 3 });
    const lines = run(core, 60);
    const seq = new SeqTracker();
    let bad = 0;
    for (const l of lines) {
      if (l.kind === 'bad') bad++;
      if (l.kind === 'data') seq.push(l.seq);
    }
    expect(bad).toBeGreaterThan(10);
    expect(seq.dropped).toBeGreaterThan(5);
  });

  it('answers firmware commands', () => {
    const core = new SimulatorCore(defaultEncoders);
    expect(core.command('INFO')[0]).toMatch(/^I,FW,.*RATE,100$/);
    expect(core.command('rate 50')).toEqual(['A,RATE,50']);
    expect(core.command('RATE 500')).toEqual(['E,BAD_RATE']);
    expect(core.command('STOP')).toEqual(['A,STOP']);
    expect(core.nextSample()).toEqual([]);
    expect(core.command('BOGUS')).toEqual(['E,UNKNOWN_CMD']);
  });
});
