import { monitor, type Snapshot } from '../../engine/monitor';
import { NumberField, SegField, SelectField, ToggleField } from '../fields';

export function DisplayPanel({ snap }: { snap: Snapshot }) {
  const s = snap.settings;
  const p = s.processing;
  const setP = (patch: Partial<typeof p>) => monitor.updateSettings({ processing: { ...p, ...patch } });
  return (
    <div style={{ maxWidth: 760 }}>
      <h2>Display & units</h2>
      <p className="lead">How the monitor looks and how raw signals are turned into numbers.</p>
      <h3 style={{ marginTop: 10 }}>Screen</h3>
      <SegField
        label="Theme"
        value={s.theme}
        options={[
          { value: 'dark', label: 'Dark' },
          { value: 'light', label: 'Light' },
          { value: 'system', label: 'Match system' },
        ]}
        onChange={(v) => monitor.updateSettings({ theme: v })}
        hint="Dark is easier on the eyes in a dim room or overnight. Light suits a bright bench."
      />
      <SegField
        label="CO2 units"
        value={s.co2Unit}
        options={[
          { value: 'mmHg', label: 'mmHg' },
          { value: '%', label: '%' },
        ]}
        onChange={(v) => monitor.updateSettings({ co2Unit: v })}
        hint="Alarm limits are always set in mmHg."
      />
      <SelectField id="sweep" label="Sweep speed" value={s.sweepSec} options={[6, 12, 30, 60].map((x) => ({ value: x, label: `${x} s per screen` }))} onChange={(v) => monitor.updateSettings({ sweepSec: v })} />
      <SelectField id="co2scale" label="Capnogram scale" value={s.co2ScaleMaxMmHg} options={[40, 60, 80, 100].map((x) => ({ value: x, label: `0–${x} mmHg` }))} onChange={(v) => monitor.updateSettings({ co2ScaleMaxMmHg: v })} />
      <SelectField id="flowscale" label="Flow scale" value={s.flowScaleLpm} options={[30, 60, 100, 200].map((x) => ({ value: x, label: `±${x} L/min` }))} onChange={(v) => monitor.updateSettings({ flowScaleLpm: v })} />
      <ToggleField id="rawv" label="Show raw volts on the monitor" checked={s.showRawVolts} onChange={(v) => monitor.updateSettings({ showRawVolts: v })} hint="Useful while calibrating; hide it for clinical use." />

      <h3 style={{ marginTop: 18 }}>Signal processing</h3>
      <NumberField id="pb" label="Barometric pressure" unit="mmHg" min={500} max={800} value={p.barometricMmHg} onChange={(v) => setP({ barometricMmHg: v })} hint="Used to convert % CO2 to mmHg. Pittsburgh is typically about 735 mmHg; enter the lab’s reading for accuracy." />
      <ToggleField id="wv" label="Water-vapor correction (BTPS)" checked={p.waterVapor} onChange={(v) => setP({ waterVapor: v })} hint="Subtracts 47 mmHg before converting % to mmHg." />
      <ToggleField id="filt" label="Low-pass filter" checked={p.filterOn} onChange={(v) => setP({ filterOn: v })} hint="2nd-order Butterworth on every channel." />
      {p.filterOn ? <NumberField id="filthz" label="Filter cutoff" unit="Hz" min={1} max={40} step={0.5} value={p.filterHz} onChange={(v) => setP({ filterHz: v })} /> : null}
      <NumberField
        id="deadband"
        label="Breath detection deadband"
        unit="L/s"
        min={0.005}
        max={0.5}
        step={0.005}
        value={+(p.deadbandLpm / 60).toFixed(3)}
        onChange={(v) => setP({ deadbandLpm: v * 60 })}
        hint="Flow must pass ± this to count as a new inspiration. Raise it if noise is counted as breaths."
      />
      <NumberField id="minbreath" label="Minimum breath length" unit="s" min={0.3} max={5} step={0.1} value={p.minBreathSec} onChange={(v) => setP({ minBreathSec: v })} />
    </div>
  );
}
