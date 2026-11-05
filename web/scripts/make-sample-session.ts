/**
 * Writes sample-data/synthetic_session.csv: 150 s of simulated data (60 s normal
 * breathing, then a CO2 ramp that crosses the 50 mmHg limit), recorded through the
 * same parser, processing and alarm engine as the live app.
 *   npm run sample-data
 */
import { writeFileSync } from 'node:fs';
import { AlarmEngine } from '../src/alarms/engine';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import { invertCalibration } from '../src/processing/calibration';
import { minuteVentilation, respiratoryRate } from '../src/processing/metrics';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import type { Breath } from '../src/processing/breath';
import { parseLine } from '../src/protocol/protocol';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { formatEventRow, formatHeader, formatSampleRow } from '../src/storage/csv';
import { DEFAULT_ALARMS } from '../src/storage/settings';

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
let wall = start;
let out = formatHeader({ studyCode: 'SAMPLE', started: new Date(start).toISOString(), source: 'Simulator', firmware: 'sim-1.0.0', rateHz: 100, channels: ch, processing: DEFAULT_PROCESSING });
const alarms = new AlarmEngine(DEFAULT_ALARMS, (e) => {
  out += formatEventRow(e.t, `ALARM_${e.kind.toUpperCase()}${e.id ? ` ${e.id}` : ''}${e.priority ? ` ${e.priority}` : ''} ${e.message}`);
});
const breaths: Breath[] = [];

out += formatEventRow(wall, 'CONNECTED Simulator');
for (let i = 0; i < 150 * 100; i++) {
  if (i === 60 * 100) {
    core.model.setParams({ scenario: 'co2Ramp' });
    out += formatEventRow(wall, 'MARK CO2 rebreathing started');
  }
  for (const line of core.nextSample()) {
    const d = parseLine(line);
    if (d.kind !== 'data') continue;
    const p = pipe.process(d.tMs, d.volts);
    out += formatSampleRow({ wallMs: wall, tMs: d.tMs, seq: d.seq, volts: d.volts, co2MmHg: p.co2MmHg, flowLpm: p.flowLpm, pneumoLpm: p.pneumoLpm, pneumoPa: p.pneumoPa });
    if (p.breath) {
      breaths.push(p.breath);
      const b = p.breath;
      out += formatEventRow(wall, `BREATH etco2_mmHg=${b.etco2.toFixed(1)} rr=${respiratoryRate(breaths).toFixed(1)} vt_ml=${b.vtMl.toFixed(0)} mv_lpm=${minuteVentilation(breaths, b.tEnd).toFixed(2)} by=${b.detectedBy}`);
      alarms.onBreath(b.etco2, wall);
    }
  }
  wall += 10;
}
const path = new URL('../../sample-data/synthetic_session.csv', import.meta.url);
writeFileSync(path, out);
console.log(`wrote ${path.pathname}: ${(out.length / 1e6).toFixed(2)} MB, ${breaths.length} breaths`);
