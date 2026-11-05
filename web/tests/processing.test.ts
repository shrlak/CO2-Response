import { describe, expect, it } from 'vitest';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import { parseLine } from '../src/protocol/protocol';
import { BreathDetector, type Breath } from '../src/processing/breath';
import {
  applyCalibration,
  fitPolynomial,
  interpolateTable,
  invertCalibration,
  pneumoFlow,
  syringeK,
  twoPointLinear,
} from '../src/processing/calibration';
import { mmHgToPct, pctToMmHg } from '../src/processing/co2';
import { LowPass } from '../src/processing/filter';
import { etco2Median, linearFit, median, minuteVentilation, respiratoryRate } from '../src/processing/metrics';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import { RailMonitor } from '../src/processing/quality';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { defaultEncoders } from './helpers';

describe('calibration', () => {
  it('applies linear, polynomial and table calibrations', () => {
    expect(applyCalibration({ type: 'linear', gain: 2, offset: -1 }, 3)).toBe(5);
    expect(applyCalibration({ type: 'polynomial', coeffs: [1, 2, 3] }, 2)).toBe(1 + 4 + 12);
    const pts: [number, number][] = [[0, 0], [1, 10], [3, 50]];
    expect(applyCalibration({ type: 'table', points: pts }, 2)).toBe(30);
    expect(interpolateTable(pts, -1)).toBe(-10); // extrapolates the first segment
    expect(interpolateTable(pts, 4)).toBe(70); // and the last
  });

  it('inverts calibrations', () => {
    expect(invertCalibration({ type: 'linear', gain: 40, offset: -100 }, 20)).toBeCloseTo(3);
    const poly = { type: 'polynomial' as const, coeffs: [0, 1, 0.5] };
    expect(applyCalibration(poly, invertCalibration(poly, 4, 0, 5))).toBeCloseTo(4, 6);
  });

  it('builds a line from zero and span readings', () => {
    // CO2: room air 0.04 % reads 0.02 V, 5 % gas reads 2.50 V
    const { gain, offset } = twoPointLinear(0.02, 0.04, 2.5, 5);
    expect(gain * 0.02 + offset).toBeCloseTo(0.04);
    expect(gain * 2.5 + offset).toBeCloseTo(5);
    expect(() => twoPointLinear(1, 0, 1, 5)).toThrow();
  });

  it('fits polynomials by least squares', () => {
    const pts: [number, number][] = [0, 1, 2, 3, 4].map((x) => [x, 2 - x + 0.5 * x * x]);
    const c = fitPolynomial(pts, 2);
    expect(c[0]).toBeCloseTo(2);
    expect(c[1]).toBeCloseTo(-1);
    expect(c[2]).toBeCloseTo(0.5);
    expect(() => fitPolynomial([[1, 1]], 1)).toThrow();
  });

  it('computes pneumotach flow and the syringe constant', () => {
    expect(pneumoFlow({ k1: 0.2, k2: 0 }, 100)).toBeCloseTo(20);
    expect(pneumoFlow({ k1: 0.2, k2: 0.001 }, -100)).toBeCloseTo(-30);
    // 3 L pushed with ∫ΔP dt = 900 Pa·s → k1 = 60·3/900 = 0.2 L/min per Pa
    expect(syringeK(900, 3)).toBeCloseTo(0.2);
    expect(() => syringeK(0, 3)).toThrow();
  });
});

describe('CO2 units', () => {
  it('converts % to mmHg with optional water-vapor correction', () => {
    expect(pctToMmHg(5, 760)).toBeCloseTo(38);
    expect(pctToMmHg(5, 760, true)).toBeCloseTo(35.65);
    expect(mmHgToPct(38, 760)).toBeCloseTo(5);
  });
});

describe('LowPass', () => {
  const run = (f: LowPass, hz: number) => {
    let peak = 0;
    for (let i = 0; i < 2000; i++) {
      const y = f.process(Math.sin((2 * Math.PI * hz * i) / 100));
      if (i > 1000) peak = Math.max(peak, Math.abs(y));
    }
    return peak;
  };
  it('passes DC and slow signals and attenuates fast noise', () => {
    const f = new LowPass(10, 100);
    let y = 0;
    for (let i = 0; i < 200; i++) y = f.process(3);
    expect(y).toBeCloseTo(3);
    expect(run(new LowPass(10, 100), 1)).toBeGreaterThan(0.98);
    expect(run(new LowPass(10, 100), 40)).toBeLessThan(0.1);
  });
});

/** 15 breaths/min, square-ish capnogram peaking at `et`, half-sine inspiration of `vtMl`. */
function synthBreaths(seconds: number, opts: { et?: number; vtMl?: number; rr?: number; flow?: boolean; noise?: number } = {}) {
  const { et = 40, vtMl = 500, rr = 15, flow = true, noise = 0 } = opts;
  const period = 60 / rr;
  const ti = period / 3;
  const qpk = ((vtMl / 1000) * 60 * Math.PI) / (2 * ti);
  const det = new BreathDetector();
  const breaths: Breath[] = [];
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5) * 2;
  for (let i = 0; i < seconds * 100; i++) {
    const t = i / 100;
    const x = t % period;
    let q: number;
    let c: number;
    if (x < ti) {
      q = qpk * Math.sin((Math.PI * x) / ti);
      c = x < 0.1 ? et * (1 - x / 0.1) : 0;
    } else {
      const e = x - ti;
      q = -qpk * 0.8 * Math.sin((Math.PI * e) / (period - ti));
      c = e < 0.2 ? (et * e) / 0.2 : et;
    }
    const b = det.push(t, flow ? q + noise * rnd() : NaN, c);
    if (b) breaths.push(b);
  }
  return breaths;
}

describe('breath detection, EtCO2, RR, VT, MV', () => {
  it('returns RR = 15 ± 0.5 and EtCO2 = 40 ± 0.5 for a 15/min capnogram peaking at 40 mmHg', () => {
    const b = synthBreaths(60);
    expect(respiratoryRate(b)).toBeGreaterThan(14.5);
    expect(respiratoryRate(b)).toBeLessThan(15.5);
    expect(etco2Median(b)).toBeGreaterThan(39.5);
    expect(etco2Median(b)).toBeLessThan(40.5);
    expect(b.at(-1)!.detectedBy).toBe('flow');
  });

  it('integrates tidal volume and minute ventilation', () => {
    const b = synthBreaths(90, { vtMl: 500 });
    expect(b.at(-1)!.vtMl).toBeGreaterThan(490);
    expect(b.at(-1)!.vtMl).toBeLessThan(510);
    expect(minuteVentilation(b, b.at(-1)!.tEnd)).toBeCloseTo(7.5, 1);
  });

  it('falls back to the capnogram when there is no flow signal', () => {
    const b = synthBreaths(60, { flow: false });
    expect(b.at(-1)!.detectedBy).toBe('co2');
    expect(respiratoryRate(b)).toBeCloseTo(15, 0);
    expect(etco2Median(b)).toBeCloseTo(40, 0);
    expect(b.at(-1)!.vtMl).toBeNaN();
  });

  it('ignores noise inside the deadband', () => {
    const b = synthBreaths(60, { noise: 2.5 });
    expect(b.length).toBeGreaterThanOrEqual(13);
    expect(b.length).toBeLessThanOrEqual(15);
  });

  it('rejects breaths shorter than the minimum length', () => {
    const det = new BreathDetector({ deadbandLpm: 3, minBreathSec: 1 });
    const out: Breath[] = [];
    // inspiration, brief expiration, then a second "inspiration" only 0.4 s after the first
    const flows = [10, 10, -10, 10, 10, -10];
    flows.forEach((f, i) => {
      const b = det.push(i * 0.08, f, 30);
      if (b) out.push(b);
    });
    expect(out).toHaveLength(0);
  });
});

describe('metrics', () => {
  it('computes median and linear fit', () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 2, 3])).toBe(2.5);
    expect(median([NaN])).toBeNaN();
    const fit = linearFit([40, 45, 50], [8, 13, 18])!;
    expect(fit.slope).toBeCloseTo(1);
    expect(fit.r2).toBeCloseTo(1);
  });
});

describe('RailMonitor', () => {
  it('flags a channel stuck at a rail for 2 s', () => {
    const r = new RailMonitor(0, 5, 0.005);
    expect(r.push(0, 0)).toBe(false);
    expect(r.push(1.9, 0)).toBe(false);
    expect(r.push(2.0, 0)).toBe(true);
    expect(r.push(2.1, 2.5)).toBe(false);
    expect(r.push(3, 4.999)).toBe(false);
    expect(r.push(5.1, 4.999)).toBe(true);
  });

  it('can ignore the low rail (a capnograph reads 0 V at 0 % CO2)', () => {
    const r = new RailMonitor(0, 5, 0.005, 2, 'high');
    expect(r.push(0, 0)).toBe(false);
    expect(r.push(10, 0)).toBe(false);
    expect(r.push(11, 5)).toBe(false);
    expect(r.push(13, 5)).toBe(true);
  });
});

describe('Pipeline (simulator → parser → processing)', () => {
  const runSim = (scenario: 'normal' | 'co2Disconnect', seconds: number) => {
    const core = new SimulatorCore(defaultEncoders, { scenario, etco2: 38, rr: 15, vtMl: 500 });
    const p = new Pipeline(cloneChannels(DEFAULT_CHANNELS), DEFAULT_PROCESSING);
    const breaths: Breath[] = [];
    let last = null as ReturnType<Pipeline['process']> | null;
    for (let i = 0; i < seconds * 100; i++) {
      for (const l of core.nextSample()) {
        const d = parseLine(l);
        if (d.kind !== 'data') continue;
        last = p.process(d.tMs, d.volts);
        if (last.breath) breaths.push(last.breath);
      }
    }
    return { breaths, last: last! };
  };

  it('recovers the simulated EtCO2, RR and VT', () => {
    const { breaths } = runSim('normal', 60);
    expect(etco2Median(breaths)).toBeGreaterThan(37);
    expect(etco2Median(breaths)).toBeLessThan(39.5);
    expect(respiratoryRate(breaths)).toBeCloseTo(15, 0);
    expect(breaths.at(-1)!.vtMl).toBeGreaterThan(460);
    expect(breaths.at(-1)!.vtMl).toBeLessThan(540);
  });

  it('marks a disconnected CO2 sensor invalid and stuck', () => {
    const { last } = runSim('co2Disconnect', 5);
    expect(last.valid.co2).toBe(false);
    expect(last.stuck.co2).toBe(true);
    expect(last.co2MmHg).toBeNaN();
    expect(last.valid.flow).toBe(true);
  });

  it('keeps CO2 valid at 0 % during inspiration (no gaps at the low rail)', () => {
    const core = new SimulatorCore(defaultEncoders, { scenario: 'normal' });
    const p = new Pipeline(cloneChannels(DEFAULT_CHANNELS), DEFAULT_PROCESSING);
    let invalid = 0;
    for (let i = 0; i < 2000; i++)
      for (const l of core.nextSample()) {
        const d = parseLine(l);
        if (d.kind === 'data' && !p.process(d.tMs, d.volts).valid.co2) invalid++;
      }
    expect(invalid).toBe(0);
  });
});
