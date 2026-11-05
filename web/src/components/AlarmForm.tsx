import type { AlarmSettings } from '../storage/settings';
import { NumberField, SegField, ToggleField, Field } from './fields';

export function AlarmForm({ value, onChange, idPrefix }: { value: AlarmSettings; onChange: (a: AlarmSettings) => void; idPrefix: string }) {
  const set = (p: Partial<AlarmSettings>) => onChange({ ...value, ...p });
  const id = (s: string) => `${idPrefix}-${s}`;
  return (
    <div>
      <SegField
        label="Alarm on"
        value={value.source}
        options={[
          { value: 'etco2', label: 'EtCO2 per breath' },
          { value: 'live', label: 'Live CO2' },
        ]}
        onChange={(v) => set({ source: v })}
        hint={value.source === 'live' ? 'Live CO2 alarms when the waveform itself stays past a limit, e.g. raised inspired CO2 during rebreathing.' : 'Compared once per breath, after each expiration.'}
      />
      <NumberField id={id('high')} label="High CO2 limit" unit="mmHg" value={value.highMmHg} min={5} max={150} step={1} onChange={(v) => set({ highMmHg: v })} hint="Drawn as the dashed line on the capnogram and trend." />
      <ToggleField id={id('lowon')} label="Low CO2 limit" checked={value.lowMmHg !== null} onChange={(on) => set({ lowMmHg: on ? 25 : null })} hint="Optional. Helps catch apnea, a leak or a disconnected sample line." />
      {value.lowMmHg !== null ? <NumberField id={id('low')} label="Low limit" unit="mmHg" value={value.lowMmHg} min={0} max={100} onChange={(v) => set({ lowMmHg: v })} /> : null}
      {value.source === 'etco2' ? (
        <NumberField id={id('delayb')} label="Trigger delay" unit="breaths" value={value.delayBreaths} min={1} max={10} onChange={(v) => set({ delayBreaths: Math.round(v) })} hint="Consecutive breaths past the limit before alarming. Stops a single breath from alarming." />
      ) : (
        <NumberField id={id('delays')} label="Trigger delay" unit="s" value={value.delaySec} min={0} max={60} step={0.5} onChange={(v) => set({ delaySec: v })} />
      )}
      <NumberField id={id('hyst')} label="Hysteresis" unit="mmHg" value={value.hysteresisMmHg} min={0} max={10} step={0.5} onChange={(v) => set({ hysteresisMmHg: v })} hint="A high alarm clears only below limit − hysteresis." />
      <NumberField id={id('apnea')} label="Apnea alarm after" unit="s" value={value.apneaSec} min={0} max={120} onChange={(v) => set({ apneaSec: v })} hint="High-priority alarm when no breath is detected for this long. 0 turns it off." />
      <NumberField id={id('silence')} label="Silence duration" unit="s" value={value.silenceSec} min={10} max={120} onChange={(v) => set({ silenceSec: v })} hint="Audio comes back on by itself after this. Maximum 120 s." />
      <Field label="Alarm volume" htmlFor={id('vol')} hint="Cannot be set to zero.">
        <input id={id('vol')} type="range" min={10} max={100} step={5} value={Math.round(value.volume * 100)} onChange={(e) => set({ volume: Number(e.target.value) / 100 })} />
        <span className="u">{Math.round(value.volume * 100)} %</span>
      </Field>
    </div>
  );
}
