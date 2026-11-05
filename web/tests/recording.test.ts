import { IDBFactory } from 'fake-indexeddb';
import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { AlarmEngine } from '../src/alarms/engine';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import type { Breath } from '../src/processing/breath';
import { minuteVentilation, respiratoryRate } from '../src/processing/metrics';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import { parseLine } from '../src/protocol/protocol';
import { SimulatorCore } from '../src/sim/simulatorCore';
import {
  breathsCsv,
  cleanText,
  COLUMNS,
  csvEscape,
  eventsCsv,
  exportSessionParts,
  parseSessionCsv,
  SessionParser,
  sessionFileName,
  splitCsvLine,
  summarize,
} from '../src/storage/csv';
import { SessionLibrary } from '../src/storage/library';
import { SessionRecorder } from '../src/storage/recorder';
import { DEFAULT_ALARMS } from '../src/storage/settings';
import { defaultEncoders } from './helpers';

const T0 = Date.parse('2026-11-05T05:00:00.000Z');

beforeEach(() => {
  // a fresh, empty IndexedDB for every test
  (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
});

/** Record `seconds` of a simulated CO2 ramp through the real recorder. */
async function recordSession(lib: SessionLibrary, seconds: number, opts: { stop?: boolean } = {}) {
  const channels = cloneChannels(DEFAULT_CHANNELS);
  channels.channels.co2.calibration = { type: 'linear', gain: 2.02, offset: -0.01 };
  const core = new SimulatorCore(defaultEncoders, { scenario: 'co2Ramp', seed: 11, rampMmHgPerMin: 20 });
  const pipe = new Pipeline(channels, DEFAULT_PROCESSING);
  const rec = new SessionRecorder(lib);
  const alarms = new AlarmEngine({ ...DEFAULT_ALARMS, highMmHg: 45 }, (e) => rec.event(e.t, `ALARM_${e.kind.toUpperCase()} ${e.id ?? ''} ${e.priority ?? ''} ${e.message}`));
  await rec.start({ studyCode: 'TEST #01', appVersion: 'test', source: 'Simulator', firmware: 'sim', rateHz: 100, channels, processing: DEFAULT_PROCESSING, alarmSettings: DEFAULT_ALARMS }, T0);
  rec.event(T0, 'RECORDING_STARTED source=Simulator');
  const breaths: Breath[] = [];
  const co2: number[] = [];
  let wall = T0;
  for (let i = 0; i < seconds * 100; i++) {
    if (i === 1000) rec.event(wall, 'MARK step #2, "fast"');
    for (const l of core.nextSample()) {
      const d = parseLine(l);
      if (d.kind !== 'data') continue;
      const p = pipe.process(d.tMs, d.volts);
      if (p.breath) {
        breaths.push(p.breath);
        rec.breath({ etco2: p.breath.etco2, fico2: p.breath.fico2, rr: respiratoryRate(breaths), vt: p.breath.vtMl, mv: minuteVentilation(breaths, p.breath.tEnd), peak: p.breath.peakInspLpm });
        alarms.onBreath(p.breath.etco2, wall);
      }
      rec.sample({ wallMs: wall, tMs: d.tMs, seq: d.seq, volts: d.volts, co2MmHg: p.co2MmHg, co2Pct: p.co2Pct, flowLpm: p.flowLpm, pneumoPa: p.pneumoPa, pneumoLpm: p.pneumoLpm });
      co2.push(p.co2MmHg);
    }
    wall += 10;
  }
  const out = opts.stop === false ? null : await rec.stop();
  return { rec, out, breaths, co2, channels };
}

describe('CSV helpers', () => {
  it('escapes, splits and cleans free text', () => {
    expect(csvEscape('plain')).toBe('plain');
    expect(csvEscape('a,b "c"')).toBe('"a,b ""c"""');
    expect(splitCsvLine('1,"a,b ""c""",3')).toEqual(['1', 'a,b "c"', '3']);
    expect(cleanText('run #2\nnext')).toBe('run no.2 next');
  });

  it('names files by study code and time only', () => {
    expect(sessionFileName('Subj 07 / run#2', new Date(2026, 10, 5, 3, 4, 5))).toBe('co2r_Subj-07-run-2_20261105-030405.csv');
    expect(sessionFileName('', new Date(2026, 0, 1))).toMatch(/^co2r_session_20260101-000000\.csv$/);
  });
});

describe('recording to one session file', () => {
  it('writes a single rectangular CSV with samples, breaths and events', async () => {
    const lib = new SessionLibrary();
    const { out, breaths } = await recordSession(lib, 60);
    const text = await (await lib.blob(out!.rec!.id)).text();
    const lines = text.trimEnd().split('\n');
    expect(lines[0]).toBe('# CO2-Response session,v2');
    const header = lines.find((l) => !l.startsWith('#'))!;
    expect(header).toBe(COLUMNS.join(','));
    const rows = lines.slice(lines.indexOf(header) + 1);
    expect(rows).toHaveLength(6000);
    for (const r of rows) {
      expect(r.startsWith('#')).toBe(false);
      expect(splitCsvLine(r)).toHaveLength(COLUMNS.length);
    }
    // '#' never appears in a data row, so comment='#' readers are safe
    expect(rows.some((r) => r.includes('#'))).toBe(false);

    const s = parseSessionCsv(text);
    expect(s.version).toBe(2);
    expect(s.studyCode).toBe('TEST no.01');
    expect(s.n).toBe(6000);
    expect(s.breaths).toHaveLength(breaths.length);
    expect(s.events.map((e) => e.text)).toEqual(expect.arrayContaining(['RECORDING_STARTED source=Simulator', 'MARK step no.2, "fast"']));
    expect(s.events.some((e) => e.text.startsWith('ALARM_START co2High high'))).toBe(true);
    // time_s comes from the device clock: exactly 10 ms apart
    for (let i = 1; i < s.n; i++) expect(s.timeS[i] - s.timeS[i - 1]).toBeCloseTo(0.01, 6);
    expect(out!.rec!.status).toBe('complete');
    expect(out!.rec!.stats).toMatchObject({ samples: 6000, breaths: breaths.length, markers: 1 });
  });

  it('replays identically from the file: same volts + same calibration give the same breaths', async () => {
    const lib = new SessionLibrary();
    const { out, breaths, co2 } = await recordSession(lib, 60);
    const s = await lib.parse(out!.rec!.id);
    expect(s.channels?.channels.co2.calibration).toEqual({ type: 'linear', gain: 2.02, offset: -0.01 });
    const pipe = new Pipeline(s.channels!, DEFAULT_PROCESSING);
    const replayed: Breath[] = [];
    for (let i = 0; i < s.n; i++) {
      const p = pipe.process(s.tMs[i], [s.v0[i], s.v1[i], s.v2[i]]);
      if (p.breath) replayed.push(p.breath);
      if (Number.isFinite(co2[i])) expect(Math.abs(p.co2MmHg - s.co2MmHg[i])).toBeLessThanOrEqual(0.006);
    }
    expect(replayed).toEqual(breaths);
    // the breath columns in the file match what was computed live (rounded to 0.1)
    s.breaths.forEach((b, i) => expect(Math.abs(b.etco2 - breaths[i].etco2)).toBeLessThanOrEqual(0.05));
  });

  it('survives a crash: data written before the tab died is recovered', async () => {
    const lib = new SessionLibrary();
    const { rec } = await recordSession(lib, 5, { stop: false });
    await new Promise((r) => setTimeout(r, 1300)); // one flush interval
    const id = rec.rec!.id;
    // a new page load finds the unfinished recording
    const recovered = await new SessionLibrary().recoverInterrupted(Date.now() + 20000);
    expect(recovered.map((r) => r.id)).toEqual([id]);
    expect(recovered[0].status).toBe('interrupted');
    const s = await lib.parse(id);
    expect(s.n).toBe(500);
    await rec.stop(); // clean up the timer
  });

  it('does not mark a recording that is still being written as interrupted', async () => {
    const lib = new SessionLibrary();
    const { rec } = await recordSession(lib, 1, { stop: false });
    expect(await lib.recoverInterrupted(Date.now())).toEqual([]);
    await rec.stop();
  });
});

describe('parsing', () => {
  it('gives the same result however the text is split into pieces', async () => {
    const lib = new SessionLibrary();
    const { out } = await recordSession(lib, 10);
    const text = await (await lib.blob(out!.rec!.id)).text();
    const whole = parseSessionCsv(text);
    const p = new SessionParser();
    for (let i = 0; i < text.length; i += 997) p.push(text.slice(i, i + 997));
    const pieces = p.finish();
    expect(pieces.n).toBe(whole.n);
    expect(Array.from(pieces.v1)).toEqual(Array.from(whole.v1));
    expect(pieces.breaths).toEqual(whole.breaths);
    expect(pieces.events).toEqual(whole.events);
  });

  it('reads v1 files (breaths stored as BREATH events)', () => {
    const v1 = [
      '# CO2-Response session,v1',
      '# study_code,OLD',
      '# started,2026-11-05T05:00:00.000Z',
      'wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,flow_lpm,pneumo_lpm,pneumo_pa,event',
      '2026-11-05T05:00:00.000Z,,,,,,,,,,CONNECTED Simulator',
      '2026-11-05T05:00:00.010Z,10,0,2.0000,2.5000,2.5000,38.00,0.00,0.00,0.0,',
      '2026-11-05T05:00:00.020Z,20,1,2.0100,2.5100,2.5100,38.19,0.40,0.40,2.0,',
      '2026-11-05T05:00:00.020Z,,,,,,,,,,BREATH etco2_mmHg=38.2 rr=15.0 vt_ml=500 mv_lpm=7.50 by=flow',
      '2026-11-05T05:00:01.000Z,,,,,,,,,,"MARK a, b"',
    ].join('\n');
    const s = parseSessionCsv(v1);
    expect(s.version).toBe(1);
    expect(s.n).toBe(2);
    expect(s.timeS[1]).toBeCloseTo(0.02);
    expect(s.breaths).toEqual([{ timeS: 0.02, n: 1, etco2: 38.2, fico2: NaN, rr: 15, vt: 500, mv: 7.5, peak: NaN }]);
    expect(s.events.map((e) => e.text)).toEqual(['CONNECTED Simulator', 'MARK a, b']);
    // exporting converts it to v2
    const v2 = parseSessionCsv(exportSessionParts(s).join(''));
    expect(v2.version).toBe(2);
    expect(v2.meta.converted_from).toBe('v1');
    expect(v2.n).toBe(2);
    expect(v2.breaths[0].etco2).toBe(38.2);
    expect(v2.events.map((e) => e.text)).toEqual(['CONNECTED Simulator', 'MARK a, b']);
  });

  it('rejects files that are not sessions', () => {
    expect(() => parseSessionCsv('a,b,c\n1,2,3\n')).toThrow(/not a CO2-Response session/);
    expect(() => parseSessionCsv('# only comments\n')).toThrow(/no data header/);
  });
});

describe('export', () => {
  it('re-exports a whole session losslessly and a range as a subset', async () => {
    const lib = new SessionLibrary();
    const { out } = await recordSession(lib, 30);
    const s = await lib.parse(out!.rec!.id);
    const again = parseSessionCsv(exportSessionParts(s).join(''));
    expect(again.n).toBe(s.n);
    expect(Array.from(again.v0)).toEqual(Array.from(s.v0));
    expect(again.breaths).toEqual(s.breaths);
    expect(again.events).toEqual(s.events);

    const part = parseSessionCsv(exportSessionParts(s, { t0: 10, t1: 20 }).join(''));
    expect(part.meta.exported_range_s).toBe('10.000-20.000');
    expect(part.n).toBe(1001);
    expect(part.timeS[0]).toBeCloseTo(10);
    expect(part.breaths.every((b) => b.timeS >= 10 && b.timeS <= 20)).toBe(true);
    expect(part.events.every((e) => e.timeS >= 10 && e.timeS <= 20)).toBe(true);
  });

  it('writes breath and event tables', async () => {
    const lib = new SessionLibrary();
    const { out, breaths } = await recordSession(lib, 30);
    const s = await lib.parse(out!.rec!.id);
    const b = breathsCsv(s).trimEnd().split('\n');
    expect(b[0]).toBe('breath,time_s,wall_time_iso,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm');
    expect(b).toHaveLength(breaths.length + 1);
    const e = eventsCsv(s).trimEnd().split('\n');
    expect(e[0]).toBe('time_s,wall_time_iso,type,event');
    expect(e.some((l) => l.includes(',MARK,'))).toBe(true);
    const sum = summarize(s);
    expect(sum).toMatchObject({ samples: 3000, breaths: breaths.length, markers: 1, dropped: 0, rateHz: 100 });
  });
});

describe('session library', () => {
  it('imports a file, lists it, and deletes it with its data', async () => {
    const src = new SessionLibrary();
    const { out } = await recordSession(src, 10);
    const blob = await src.blob(out!.rec!.id);

    (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
    const lib = new SessionLibrary();
    const { rec } = await lib.importFile(blob, 'from-another-pc.csv');
    expect(rec).toMatchObject({ status: 'imported', fileName: 'from-another-pc.csv', studyCode: 'TEST no.01' });
    expect(rec.stats.samples).toBe(1000);
    expect((await lib.list()).map((r) => r.id)).toEqual([rec.id]);
    expect(await (await lib.blob(rec.id)).text()).toBe(await blob.text());
    await lib.delete(rec.id);
    expect(await lib.list()).toEqual([]);
    expect((await lib.blob(rec.id)).size).toBe(0);
  });

  it('keeps nothing when an imported file is not a session', async () => {
    const lib = new SessionLibrary();
    await expect(lib.importFile(new Blob(['name,age\nx,1\n']), 'people.csv')).rejects.toThrow(/not a CO2-Response session/);
    expect(await lib.list()).toEqual([]);
  });
});
