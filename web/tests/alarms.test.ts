import { describe, expect, it } from 'vitest';
import { AlarmEngine, type AlarmLogEntry } from '../src/alarms/engine';
import { cloneChannels, DEFAULT_CHANNELS } from '../src/config/channels';
import { DEFAULT_PROCESSING, Pipeline } from '../src/processing/pipeline';
import { parseLine } from '../src/protocol/protocol';
import { SimulatorCore } from '../src/sim/simulatorCore';
import { DEFAULT_ALARMS, sanitizeAlarms, type AlarmSettings } from '../src/storage/settings';
import { defaultEncoders } from './helpers';

const make = (s: Partial<AlarmSettings> = {}) => {
  const log: AlarmLogEntry[] = [];
  const e = new AlarmEngine({ ...DEFAULT_ALARMS, ...s }, (x) => log.push(x));
  return { e, log };
};

describe('AlarmEngine: EtCO2 limits', () => {
  it('waits for the trigger delay (2 breaths) before alarming', () => {
    const { e } = make({ highMmHg: 50, delayBreaths: 2 });
    e.onBreath(52, 1000);
    expect(e.get('co2High')).toBeUndefined();
    e.onBreath(48, 5000); // back below resets the count
    e.onBreath(53, 9000);
    expect(e.get('co2High')).toBeUndefined();
    e.onBreath(54, 13000);
    expect(e.get('co2High')).toMatchObject({ state: 'active', priority: 'high', since: 13000, value: 54 });
  });

  it('clears only below limit − hysteresis', () => {
    const { e } = make({ highMmHg: 50, hysteresisMmHg: 2, delayBreaths: 1 });
    e.onBreath(55, 0);
    e.onBreath(49, 4000); // inside the hysteresis band: still active
    expect(e.get('co2High')?.state).toBe('active');
    e.onBreath(48.5, 8000);
    expect(e.get('co2High')?.state).toBe('active');
    e.onBreath(47.9, 12000);
    expect(e.get('co2High')).toMatchObject({ state: 'resolved', resolvedAt: 12000, peak: 55 });
  });

  it('latches after the condition clears until acknowledged', () => {
    const { e, log } = make({ delayBreaths: 1 });
    e.onBreath(60, 0);
    e.onBreath(40, 4000);
    expect(e.get('co2High')?.state).toBe('resolved');
    e.tick(600000); // time passing does not clear it
    expect(e.get('co2High')?.state).toBe('resolved');
    e.acknowledge(700000);
    expect(e.get('co2High')).toBeUndefined();
    expect(log.map((l) => l.kind)).toEqual(['start', 'resolved', 'cleared']);
  });

  it('re-alarms if CO2 rises again while the old alarm is still latched', () => {
    const { e } = make({ delayBreaths: 2 });
    e.onBreath(60, 0);
    e.onBreath(60, 4000);
    e.onBreath(40, 8000);
    expect(e.get('co2High')?.state).toBe('resolved');
    e.onBreath(60, 12000);
    expect(e.get('co2High')?.state).toBe('resolved'); // delay applies again
    e.onBreath(60, 16000);
    expect(e.get('co2High')).toMatchObject({ state: 'active', since: 16000 });
  });

  it('raises a medium alarm below the low limit', () => {
    const { e } = make({ lowMmHg: 25, delayBreaths: 2 });
    e.onBreath(20, 0);
    e.onBreath(19, 4000);
    expect(e.get('co2Low')).toMatchObject({ state: 'active', priority: 'medium', peak: 19 });
    e.onBreath(26, 8000); // within hysteresis of 25 + 2
    expect(e.get('co2Low')?.state).toBe('active');
    e.onBreath(28, 12000);
    expect(e.get('co2Low')?.state).toBe('resolved');
  });

  it('ignores breaths without a CO2 value and the live waveform when keyed on EtCO2', () => {
    const { e } = make({ delayBreaths: 1 });
    e.onBreath(NaN, 0);
    e.onLiveCo2(80, 0);
    e.onLiveCo2(80, 60000);
    expect(e.list()).toHaveLength(0);
  });
});

describe('AlarmEngine: live CO2 source', () => {
  it('needs the value past the limit for delaySec', () => {
    const { e } = make({ source: 'live', highMmHg: 50, delaySec: 5 });
    for (let t = 0; t <= 4900; t += 100) e.onLiveCo2(55, t);
    expect(e.get('co2High')).toBeUndefined();
    e.onLiveCo2(55, 5000);
    expect(e.get('co2High')?.state).toBe('active');
    e.onLiveCo2(49, 5100); // inside hysteresis
    expect(e.get('co2High')?.state).toBe('active');
    e.onLiveCo2(47, 5200);
    expect(e.get('co2High')?.state).toBe('resolved');
  });

  it('restarts the delay when the value dips below the limit', () => {
    const { e } = make({ source: 'live', highMmHg: 50, delaySec: 5 });
    e.onLiveCo2(55, 0);
    e.onLiveCo2(40, 3000);
    e.onLiveCo2(55, 4000);
    e.onLiveCo2(55, 8000);
    expect(e.get('co2High')).toBeUndefined();
    e.onLiveCo2(55, 9000);
    expect(e.get('co2High')?.state).toBe('active');
  });
});

describe('AlarmEngine: acknowledge, silence and re-arm', () => {
  it('acknowledging an active alarm silences audio, and silence re-arms after silenceSec', () => {
    const { e, log } = make({ delayBreaths: 1, silenceSec: 120 });
    e.onBreath(60, 0);
    e.acknowledge(1000);
    expect(e.get('co2High')).toMatchObject({ state: 'active', acknowledged: true });
    expect(e.isSilenced(1000)).toBe(true);
    expect(e.silenceRemainingMs(61000)).toBe(60000);
    e.tick(120999);
    expect(e.isSilenced(120999)).toBe(true);
    e.tick(121000);
    expect(e.isSilenced(121000)).toBe(false);
    expect(log.map((l) => l.kind)).toEqual(['start', 'acknowledged', 'silenced', 'rearmed']);
  });

  it('never silences for more than 120 s', () => {
    const { e } = make({ silenceSec: 600 });
    e.silence(0);
    expect(e.silenceRemainingMs(0)).toBe(120000);
  });

  it('cancels a silence on request', () => {
    const { e } = make();
    e.silence(0);
    e.unsilence(1000);
    expect(e.isSilenced(1000)).toBe(false);
  });

  it('sanitizes stored settings: volume never 0, silence never over 120 s', () => {
    const s = sanitizeAlarms({ ...DEFAULT_ALARMS, volume: 0, silenceSec: 999, delayBreaths: 0 });
    expect(s.volume).toBe(0.1);
    expect(s.silenceSec).toBe(120);
    expect(s.delayBreaths).toBe(1);
  });
});

describe('AlarmEngine: technical alarms and ordering', () => {
  it('latches technical alarms but not advisories', () => {
    const { e } = make();
    e.setCondition('noData', true, 0, 'medium', 'NO DATA');
    e.setCondition('audioOff', true, 0, 'advisory', 'audio off');
    e.setCondition('noData', false, 1000, 'medium', '');
    e.setCondition('audioOff', false, 1000, 'advisory', '');
    expect(e.get('noData')?.state).toBe('resolved');
    expect(e.get('audioOff')).toBeUndefined();
  });

  it('orders active alarms, then latched ones, then advisories', () => {
    const { e } = make({ delayBreaths: 1 });
    e.setCondition('dropped', true, 0, 'advisory', 'x');
    e.setCondition('noData', true, 1, 'medium', 'x');
    e.setCondition('railCo2', true, 2, 'medium', 'x');
    e.setCondition('railCo2', false, 3, 'medium', 'x');
    e.onBreath(60, 4);
    // the latched railCo2 still needs acknowledging, so it outranks the advisory
    expect(e.list().map((a) => a.id)).toEqual(['co2High', 'noData', 'railCo2', 'dropped']);
    expect(e.activePriority()).toBe('high');
  });
});

describe('CO2 ramp scenario end to end', () => {
  it('alarms within a breath or two of EtCO2 crossing the limit', () => {
    const core = new SimulatorCore(defaultEncoders, { scenario: 'co2Ramp', rampMmHgPerMin: 15, etco2: 38, seed: 5 });
    const pipe = new Pipeline(cloneChannels(DEFAULT_CHANNELS), DEFAULT_PROCESSING);
    const { e } = make({ highMmHg: 50, delayBreaths: 2 });
    const ets: number[] = [];
    let alarmAt = -1;
    for (let i = 0; i < 150 * 100 && alarmAt < 0; i++) {
      for (const l of core.nextSample()) {
        const d = parseLine(l);
        if (d.kind !== 'data') continue;
        const p = pipe.process(d.tMs, d.volts);
        if (p.breath) {
          ets.push(p.breath.etco2);
          e.onBreath(p.breath.etco2, d.tMs);
          if (e.get('co2High')?.state === 'active') alarmAt = ets.length - 1;
        }
      }
    }
    expect(alarmAt).toBeGreaterThan(0);
    // the two breaths that triggered it were above the limit, the one before was not
    expect(ets[alarmAt]).toBeGreaterThan(50);
    expect(ets[alarmAt - 1]).toBeGreaterThan(50);
    expect(ets.slice(0, alarmAt - 1).every((x) => x <= 50.5)).toBe(true);
    expect(ets[alarmAt]).toBeLessThan(54);
  });
});
