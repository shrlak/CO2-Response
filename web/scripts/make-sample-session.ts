/**
 * Writes sample-data/synthetic_session.csv: 150 s of simulated data (60 s normal
 * breathing, then a CO2 ramp that crosses the 50 mmHg limit), recorded through the
 * same parser, processing, alarm engine and session recorder as the live app.
 *   npm run sample-data
 */
import 'fake-indexeddb/auto';
import { writeFileSync } from 'node:fs';
import { AlarmEngine } from '../src/alarms/engine';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import type { Breath } from '../src/processing/breath';
import { invertCalibration } from '../src/processing/calibration';
import { minuteVentilation, respiratoryRate } from '../src/processing/metrics';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import { parseLine } from '../src/protocol/protocol';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { SessionLibrary } from '../src/storage/library';
import { SessionRecorder } from '../src/storage/recorder';
import { DEFAULT_ALARMS } from '../src/storage/settings';
import pkg from '../package.json';

const ch = cloneChannels(DEFAULT_CHANNELS);
const c = DEFAULT_CHANNELS.channels;
const enc = {
  co2: (y: number) => invertCalibration(c.co2.calibration, y),
  flow: (y: number) => invertCalibration(c.flow.calibration, y),
  pneumo: (y: number) => invertCalibration(c.pneumo.calibration, y),
};
const core = new SimulatorCore(enc, { scenario: 'normal', seed: 2026, rampMmHgPerMin: 12 });
const pipe = new Pipeline(ch, DEFAULT_PROCESSING);
const start = Date.parse('2026-11-05T05:00:00.000Z');
const lib = new SessionLibrary();
const rec = new SessionRecorder(lib);
await rec.start({ studyCode: 'SAMPLE', appVersion: pkg.version, source: 'Simulator', firmware: 'sim-1.0.0', rateHz: 100, channels: ch, processing: DEFAULT_PROCESSING, alarmSettings: DEFAULT_ALARMS }, start);
const alarms = new AlarmEngine(DEFAULT_ALARMS, (e) => rec.event(e.t, `ALARM_${e.kind.toUpperCase()}${e.id ? ` ${e.id}` : ''}${e.priority ? ` ${e.priority}` : ''} ${e.message}`));
const breaths: Breath[] = [];
let wall = start;
rec.event(wall, 'RECORDING_STARTED source=Simulator');
for (let i = 0; i < 150 * 100; i++) {
  if (i === 60 * 100) {
    core.model.setParams({ scenario: 'co2Ramp' });
    rec.event(wall, 'MARK CO2 rebreathing started');
  }
  for (const line of core.nextSample()) {
    const d = parseLine(line);
    if (d.kind !== 'data') continue;
    const p = pipe.process(d.tMs, d.volts);
    if (p.breath) {
      const b = p.breath;
      breaths.push(b);
      rec.breath({ etco2: b.etco2, fico2: b.fico2, rr: respiratoryRate(breaths), vt: b.vtMl, mv: minuteVentilation(breaths, b.tEnd), peak: b.peakInspLpm });
      alarms.onBreath(b.etco2, wall);
    }
    rec.sample({ wallMs: wall, tMs: d.tMs, seq: d.seq, volts: d.volts, co2MmHg: p.co2MmHg, co2Pct: p.co2Pct, flowLpm: p.flowLpm, pneumoPa: p.pneumoPa, pneumoLpm: p.pneumoLpm });
  }
  wall += 10;
}
rec.event(wall, 'RECORDING_STOPPED');
const { rec: stored } = await rec.stop();
const text = await (await lib.blob(stored!.id)).text();
const path = new URL('../../sample-data/synthetic_session.csv', import.meta.url);
writeFileSync(path, text);
console.log(`wrote ${path.pathname}: ${(text.length / 1e6).toFixed(2)} MB, ${breaths.length} breaths`);
