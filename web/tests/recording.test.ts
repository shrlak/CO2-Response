import { describe, expect, it } from 'vitest';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import type { Breath } from '../src/processing/breath';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import { parseLine } from '../src/protocol/protocol';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { csvEscape, formatEventRow, formatHeader, formatSampleRow, parseSessionCsv, sessionFileName, splitCsvLine } from '../src/storage/csv';
import { defaultEncoders } from './helpers';

function record(seconds: number) {
  const channels = cloneChannels(DEFAULT_CHANNELS);
  channels.channels.co2.calibration = { type: 'linear', gain: 2.02, offset: -0.01 }; // a non-default calibration travels with the file
  const core = new SimulatorCore(defaultEncoders, { scenario: 'co2Ramp', seed: 11 });
  const pipe = new Pipeline(channels, DEFAULT_PROCESSING);
  let csv = formatHeader({ studyCode: 'TEST-01', started: '2026-11-05T05:00:00.000Z', source: 'Simulator', firmware: 'sim-1.0.0', rateHz: 100, channels, processing: DEFAULT_PROCESSING });
  const breaths: Breath[] = [];
  const co2: number[] = [];
  let wall = Date.parse('2026-11-05T05:00:00.000Z');
  for (let i = 0; i < seconds * 100; i++) {
    for (const l of core.nextSample()) {
      const d = parseLine(l);
      if (d.kind !== 'data') continue;
      const p = pipe.process(d.tMs, d.volts);
      csv += formatSampleRow({ wallMs: wall, tMs: d.tMs, seq: d.seq, volts: d.volts, co2MmHg: p.co2MmHg, flowLpm: p.flowLpm, pneumoLpm: p.pneumoLpm, pneumoPa: p.pneumoPa });
      co2.push(p.co2MmHg);
      if (p.breath) {
        breaths.push(p.breath);
        csv += formatEventRow(wall, `BREATH etco2_mmHg=${p.breath.etco2.toFixed(1)}`);
      }
    }
    wall += 10;
  }
  csv += formatEventRow(wall, 'MARK CO2 step, "fast"');
  return { csv, breaths, co2 };
}

describe('session CSV', () => {
  it('escapes and splits quoted fields', () => {
    expect(csvEscape('plain')).toBe('plain');
    expect(csvEscape('a,b "c"')).toBe('"a,b ""c"""');
    expect(splitCsvLine('1,"a,b ""c""",3')).toEqual(['1', 'a,b "c"', '3']);
  });

  it('round-trips metadata, calibration, samples and events', () => {
    const { csv } = record(5);
    const s = parseSessionCsv(csv);
    expect(s.meta.study_code).toBe('TEST-01');
    expect(s.meta.rate_hz).toBe('100');
    expect(s.channels?.channels.co2.calibration).toEqual({ type: 'linear', gain: 2.02, offset: -0.01 });
    expect(s.samples).toHaveLength(500);
    expect(s.samples[0].volts).toHaveLength(3);
    expect(s.events.at(-1)!.text).toBe('MARK CO2 step, "fast"');
  });

  it('replays identically: same volts + same calibration give the same numbers and breaths', () => {
    const { csv, breaths, co2 } = record(60);
    const s = parseSessionCsv(csv);
    const pipe = new Pipeline(s.channels!, DEFAULT_PROCESSING);
    const replayed: Breath[] = [];
    s.samples.forEach((r, i) => {
      const p = pipe.process(r.tMs, r.volts);
      if (p.breath) replayed.push(p.breath);
      if (Number.isFinite(co2[i])) expect(Math.abs(p.co2MmHg - r.co2MmHg)).toBeLessThanOrEqual(0.005);
    });
    expect(replayed).toEqual(breaths);
    expect(breaths.length).toBeGreaterThan(10);
  });

  it('rejects files that are not sessions', () => {
    expect(() => parseSessionCsv('a,b,c\n1,2,3\n')).toThrow(/not a CO2-Response session/);
  });

  it('names files by study code and time only', () => {
    expect(sessionFileName('Subj 07 / run#2', new Date(2026, 10, 5, 3, 4, 5))).toBe('co2r_Subj-07-run-2_20261105-030405.csv');
    expect(sessionFileName('', new Date(2026, 0, 1))).toMatch(/^co2r_session_20260101-000000\.csv$/);
  });
});
