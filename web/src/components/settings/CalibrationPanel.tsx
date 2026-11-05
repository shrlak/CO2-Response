import { useEffect, useRef, useState } from 'react';
import { cloneChannels, DEFAULT_CHANNELS, validateChannels, type Calibration, type ChannelConfig, type ChannelId } from '../../config/channels';
import { monitor, type Snapshot } from '../../engine/monitor';
import { applyCalibration, fitPolynomial, pneumoFlow, syringeK1, twoPointLinear } from '../../processing/calibration';
import { pctToMmHg } from '../../processing/co2';
import { downloadText } from '../../storage/download';
import { Field, NumberField, NumInput, Seg } from '../fields';
import { fmt } from '../format';
import { showToast } from '../toast';

const NAMES: Record<ChannelId, string> = { co2: 'CO2 (capnograph)', flow: 'Flow sensor', pneumo: 'Pneumotachograph' };
const ZERO_REF: Record<ChannelId, number> = { co2: 0.04, flow: 0, pneumo: 0 };
const SPAN_DEFAULT: Record<ChannelId, [string, number, string, number]> = {
  co2: ['Room air', 0.04, 'Calibration gas', 5],
  flow: ['No flow', 0, 'Reference flow', 30],
  pneumo: ['No flow (ΔP = 0)', 0, 'Reference pressure', 200],
};

type Method = 'manual' | 'zerospan' | 'fit' | 'syringe';

function digitsFor(unit: string) {
  return unit === '%' ? 3 : unit === 'Pa' ? 1 : 2;
}

/** Capture the average raw voltage of a channel for 2 s. */
async function captureVolts(id: ChannelId): Promise<number | null> {
  try {
    const r = await monitor.captureAverage(id, 2);
    return r.volts;
  } catch (e) {
    showToast((e as Error).message, 5000);
    return null;
  }
}

function CaptureButton({ id, onVolts, label = 'Capture' }: { id: ChannelId; onVolts: (v: number) => void; label?: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="btn small"
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        const v = await captureVolts(id);
        setBusy(false);
        if (v !== null) onVolts(v);
      }}
      title="Average the live voltage for 2 seconds"
    >
      {busy ? 'Capturing 2 s…' : label}
    </button>
  );
}

// ---------------------------------------------------------------- manual entry

function ManualEditor({ id, draft, setDraft }: { id: ChannelId; draft: ChannelConfig; setDraft: (c: ChannelConfig) => void }) {
  const cal = draft.calibration;
  const u = draft.unit;
  const setCal = (c: Calibration) => setDraft({ ...draft, calibration: c });
  const [coeffText, setCoeffText] = useState(cal.type === 'polynomial' ? cal.coeffs.join(', ') : '');
  useEffect(() => {
    if (cal.type === 'polynomial') setCoeffText(cal.coeffs.join(', '));
  }, [cal]);

  const switchType = (t: Calibration['type']) => {
    if (t === cal.type) return;
    // convert so the curve stays the same where possible
    const y = (v: number) => applyCalibration(cal, v);
    if (t === 'linear') setCal({ type: 'linear', gain: +(y(1) - y(0)).toPrecision(6), offset: +y(0).toPrecision(6) });
    if (t === 'polynomial') setCal({ type: 'polynomial', coeffs: cal.type === 'linear' ? [cal.offset, cal.gain] : [+y(0).toPrecision(6), +(y(1) - y(0)).toPrecision(6)] });
    if (t === 'table') setCal({ type: 'table', points: [0, 1.25, 2.5, 3.75, 5].map((v) => [v, +y(v).toPrecision(6)] as [number, number]) });
  };

  return (
    <div>
      <Field label="Calibration type">
        <Seg
          label="Calibration type"
          value={cal.type}
          options={[
            { value: 'linear', label: 'Linear' },
            { value: 'polynomial', label: 'Polynomial' },
            { value: 'table', label: 'Lookup table' },
          ]}
          onChange={switchType}
        />
      </Field>

      {cal.type === 'linear' ? (
        <>
          <NumberField id={`${id}-gain`} label="Gain" unit={`${u}/V`} step={0.001} value={cal.gain} onChange={(v) => setCal({ ...cal, gain: v })} />
          <NumberField id={`${id}-offset`} label="Offset" unit={u} step={0.001} value={cal.offset} onChange={(v) => setCal({ ...cal, offset: v })} hint={`value = gain × volts + offset`} />
        </>
      ) : null}

      {cal.type === 'polynomial' ? (
        <Field label="Coefficients c0, c1, c2, …" htmlFor={`${id}-coeffs`} hint={`value = c0 + c1·V + c2·V² + …  (in ${u})`}>
          <input
            id={`${id}-coeffs`}
            type="text"
            className="wide"
            value={coeffText}
            onChange={(e) => setCoeffText(e.target.value)}
            onBlur={() => {
              const c = coeffText.split(/[,\s]+/).filter(Boolean).map(Number);
              if (c.length && c.every(Number.isFinite)) setCal({ type: 'polynomial', coeffs: c });
              else {
                showToast('Enter numbers separated by commas, lowest order first.');
                setCoeffText(cal.coeffs.join(', '));
              }
            }}
          />
        </Field>
      ) : null}

      {cal.type === 'table' ? (
        <div style={{ padding: '8px 0' }}>
          <table className="pts">
            <thead>
              <tr>
                <th>Volts</th>
                <th>Value ({u})</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cal.points.map((p, i) => (
                <tr key={i}>
                  <td>
                    <NumInput ariaLabel={`Point ${i + 1} volts`} step={0.0001} value={p[0]} onChange={(v) => setCal({ type: 'table', points: cal.points.map((q, j) => (j === i ? [v, q[1]] : q)) })} />
                  </td>
                  <td>
                    <NumInput ariaLabel={`Point ${i + 1} value`} step={0.001} value={p[1]} onChange={(v) => setCal({ type: 'table', points: cal.points.map((q, j) => (j === i ? [q[0], v] : q)) })} />
                  </td>
                  <td className="row-actions">
                    <CaptureButton id={id} label="Capture V" onVolts={(v) => setCal({ type: 'table', points: cal.points.map((q, j) => (j === i ? [+v.toFixed(4), q[1]] : q)) })} />
                    <button className="btn small" type="button" disabled={cal.points.length <= 2} onClick={() => setCal({ type: 'table', points: cal.points.filter((_, j) => j !== i) })}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row-actions" style={{ marginTop: 8 }}>
            <button className="btn small" type="button" onClick={() => setCal({ type: 'table', points: [...cal.points, [cal.points.at(-1)![0] + 0.5, cal.points.at(-1)![1]]] })}>
              Add point
            </button>
            <span className="note">Values between points are interpolated; outside the table the end segments are extended.</span>
          </div>
        </div>
      ) : null}

      <NumberField
        id={`${id}-zero`}
        label="Zero offset"
        unit={u}
        step={0.001}
        value={draft.zeroOffset}
        onChange={(v) => setDraft({ ...draft, zeroOffset: v })}
        hint="Subtracted after the calibration. Set it by hand or with the button below."
      />
      <div className="row-actions" style={{ padding: '8px 0' }}>
        <CaptureButton
          id={id}
          label={id === 'co2' ? 'Zero now on room air (0.04 %)' : 'Zero now (no flow)'}
          onVolts={(v) => {
            const z = applyCalibration(draft.calibration, v) - ZERO_REF[id];
            setDraft({ ...draft, zeroOffset: +z.toPrecision(6) });
            showToast(`Zero offset set to ${z.toFixed(digitsFor(u))} ${u}. Save to apply.`);
          }}
        />
      </div>

      {id === 'pneumo' && draft.flowModel ? (
        <>
          <NumberField id="pneumo-k1" label="Flow constant k1" unit="L/min per Pa" step={0.0001} value={draft.flowModel.k1} onChange={(v) => setDraft({ ...draft, flowModel: { ...draft.flowModel!, k1: v } })} />
          <NumberField
            id="pneumo-k2"
            label="Quadratic term k2"
            unit="L/min per Pa²"
            step={0.000001}
            value={draft.flowModel.k2}
            onChange={(v) => setDraft({ ...draft, flowModel: { ...draft.flowModel!, k2: v } })}
            hint="flow = k1·ΔP + k2·ΔP·|ΔP|. Leave k2 at 0 for a linear (Fleisch-type) pneumotach."
          />
        </>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- reference points (zero/span and multi-point fit)

interface Pt {
  label: string;
  ref: number;
  volts: number;
}

function PointsCalibrator({ id, draft, setDraft, mode }: { id: ChannelId; draft: ChannelConfig; setDraft: (c: ChannelConfig) => void; mode: 'zerospan' | 'fit' }) {
  const [a, ra, b, rb] = SPAN_DEFAULT[id];
  const [pts, setPts] = useState<Pt[]>(() =>
    mode === 'zerospan'
      ? [
          { label: a, ref: ra, volts: NaN },
          { label: b, ref: rb, volts: NaN },
        ]
      : [
          { label: 'Point 1', ref: ra, volts: NaN },
          { label: 'Point 2', ref: rb / 2, volts: NaN },
          { label: 'Point 3', ref: rb, volts: NaN },
        ],
  );
  const [result, setResult] = useState('');
  const u = draft.unit;
  const ready = pts.filter((p) => Number.isFinite(p.volts) && Number.isFinite(p.ref));
  const setPt = (i: number, p: Partial<Pt>) => setPts(pts.map((q, j) => (j === i ? { ...q, ...p } : q)));

  const apply = (cal: Calibration, how: string) => {
    setDraft({ ...draft, calibration: cal, zeroOffset: 0 });
    const err = Math.max(...ready.map((p) => Math.abs(applyCalibration(cal, p.volts) - p.ref)));
    setResult(`${how}. Largest error at the reference points: ${err.toFixed(digitsFor(u))} ${u}. Review below, then Save.`);
  };

  const fit = (deg: 1 | 2 | 'table') => {
    try {
      if (deg === 'table') {
        apply({ type: 'table', points: ready.map((p) => [p.volts, p.ref] as [number, number]).sort((x, y) => x[0] - y[0]) }, `Lookup table from ${ready.length} points`);
      } else if (deg === 1 && ready.length === 2) {
        const { gain, offset } = twoPointLinear(ready[0].volts, ready[0].ref, ready[1].volts, ready[1].ref);
        apply({ type: 'linear', gain: +gain.toPrecision(7), offset: +offset.toPrecision(7) }, `Line through 2 points: gain ${gain.toPrecision(5)} ${u}/V, offset ${offset.toPrecision(5)} ${u}`);
      } else {
        const c = fitPolynomial(
          ready.map((p) => [p.volts, p.ref]),
          deg,
        ).map((x) => +x.toPrecision(7));
        apply(deg === 1 ? { type: 'linear', gain: c[1], offset: c[0] } : { type: 'polynomial', coeffs: c }, deg === 1 ? `Least-squares line through ${ready.length} points` : `Quadratic fit through ${ready.length} points`);
      }
    } catch (e) {
      setResult('');
      showToast((e as Error).message, 5000);
    }
  };

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {mode === 'zerospan' ? (
        <ol className="steps">
          {id === 'co2' ? (
            <>
              <li>Flow room air through the sample line, wait for the reading to settle, then capture point A.</li>
              <li>Flow the calibration gas (e.g. 5 % CO2), wait for it to settle, then capture point B. Enter the gas’s certified value.</li>
            </>
          ) : (
            <>
              <li>With no flow through the sensor, capture point A.</li>
              <li>Apply a known {id === 'flow' ? 'flow (reference flowmeter)' : 'pressure (manometer)'} and capture point B. Enter the reference value.</li>
            </>
          )}
          <li>Press Compute line, check the preview at the top of this card, then Save.</li>
        </ol>
      ) : (
        <p className="note">Capture three or more readings across the range you will measure, then fit a line, a quadratic, or use the points as a lookup table. You can also type volts in by hand.</p>
      )}
      <table className="pts">
        <thead>
          <tr>
            <th>Point</th>
            <th>Reference ({u})</th>
            <th>Volts</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {pts.map((p, i) => (
            <tr key={i}>
              <td>{mode === 'zerospan' ? `${'AB'[i]} · ${p.label}` : p.label}</td>
              <td>
                <NumInput ariaLabel={`${p.label} reference`} step={0.001} value={p.ref} onChange={(v) => setPt(i, { ref: v })} />
              </td>
              <td>
                <NumInput ariaLabel={`${p.label} volts`} step={0.0001} value={p.volts} onChange={(v) => setPt(i, { volts: v })} />
              </td>
              <td className="row-actions">
                <CaptureButton id={id} onVolts={(v) => setPt(i, { volts: +v.toFixed(4) })} />
                {mode === 'fit' && pts.length > 2 ? (
                  <button className="btn small" type="button" onClick={() => setPts(pts.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row-actions">
        {mode === 'zerospan' ? (
          <button className="btn primary" type="button" disabled={ready.length < 2} onClick={() => fit(1)}>
            Compute line
          </button>
        ) : (
          <>
            <button className="btn small" type="button" onClick={() => setPts([...pts, { label: `Point ${pts.length + 1}`, ref: NaN, volts: NaN }])}>
              Add point
            </button>
            <button className="btn primary" type="button" disabled={ready.length < 2} onClick={() => fit(1)}>
              Fit line
            </button>
            <button className="btn" type="button" disabled={ready.length < 3} onClick={() => fit(2)}>
              Fit quadratic
            </button>
            <button className="btn" type="button" disabled={ready.length < 2} onClick={() => fit('table')}>
              Use as lookup table
            </button>
          </>
        )}
      </div>
      {result ? <p className="ok-text">{result}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------- 3 L syringe

function SyringeCalibrator({ draft, setDraft }: { draft: ChannelConfig; setDraft: (c: ChannelConfig) => void }) {
  const [volume, setVolume] = useState(3);
  const [running, setRunning] = useState(false);
  const [res, setRes] = useState<{ a1: number; a2: number; seconds: number } | null>(null);
  const [live, setLive] = useState(0);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const stopRef = useRef<(() => void) | null>(null);
  const acc = useRef({ a1: 0, a2: 0, lastT: null as number | null, lastP: 0, t0: 0, tEnd: 0 });

  useEffect(() => () => stopRef.current?.(), []);

  const start = () => {
    acc.current = { a1: 0, a2: 0, lastT: null, lastP: 0, t0: 0, tEnd: 0 };
    setRes(null);
    const idx = draftRef.current.index;
    let n = 0;
    const off = monitor.onSample((p) => {
      const d = draftRef.current;
      const v = p.volts[idx];
      if (!Number.isFinite(v)) return;
      const pa = applyCalibration(d.calibration, v) - d.zeroOffset;
      const A = acc.current;
      if (A.lastT !== null) {
        const dt = p.tSec - A.lastT;
        A.a1 += ((pa + A.lastP) / 2) * dt;
        A.a2 += ((pa * Math.abs(pa) + A.lastP * Math.abs(A.lastP)) / 2) * dt;
      } else A.t0 = p.tSec;
      A.lastT = p.tSec;
      A.tEnd = p.tSec;
      A.lastP = pa;
      if (++n % 25 === 0) setLive(A.a1);
    });
    stopRef.current = off;
    setRunning(true);
  };

  const stop = () => {
    stopRef.current?.();
    stopRef.current = null;
    setRunning(false);
    const A = acc.current;
    if (A.lastT === null) {
      showToast('No pneumotach data arrived. Connect the device first.', 5000);
      return;
    }
    setRes({ a1: A.a1, a2: A.a2, seconds: A.tEnd - A.t0 });
  };

  const fm = draft.flowModel ?? { k1: 0.2, k2: 0 };
  const measuredL = res ? Math.abs(fm.k1 * res.a1 + fm.k2 * res.a2) / 60 : NaN;
  let newK1 = NaN;
  let err = '';
  if (res) {
    try {
      newK1 = syringeK1(res.a1, res.a2, volume, fm.k2);
    } catch (e) {
      err = (e as Error).message;
    }
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <ol className="steps">
        <li>Zero the pneumotach with no flow (Enter values → Zero now), and save.</li>
        <li>Connect the calibration syringe with the plunger fully out.</li>
        <li>Press Start stroke, push the plunger all the way in with one smooth stroke (1–2 s), then press Stop.</li>
        <li>Check the measured volume. Apply the new k1, then push again to confirm it reads the syringe volume.</li>
      </ol>
      <NumberField id="syringe-vol" label="Syringe volume" unit="L" step={0.1} min={0.1} max={10} value={volume} onChange={setVolume} />
      <div className="row-actions">
        {!running ? (
          <button className="btn primary" type="button" onClick={start}>
            Start stroke
          </button>
        ) : (
          <button className="btn danger" type="button" onClick={stop}>
            Stop
          </button>
        )}
        {running ? <span className="note">Integrating… ∫ΔP dt = {live.toFixed(1)} Pa·s</span> : null}
      </div>
      {res ? (
        <div className="stats">
          <div>
            <div className="k">∫ΔP dt</div>
            <div className="v">{res.a1.toFixed(1)} Pa·s</div>
          </div>
          <div>
            <div className="k">Stroke time</div>
            <div className="v">{res.seconds.toFixed(2)} s</div>
          </div>
          <div>
            <div className="k">Volume with current k1</div>
            <div className="v">
              {fmt(measuredL, 3)} L <span className="note">({fmt(((measuredL - volume) / volume) * 100, 1)} %)</span>
            </div>
          </div>
          <div>
            <div className="k">New k1</div>
            <div className="v">{Number.isFinite(newK1) ? newK1.toPrecision(5) : '--'}</div>
          </div>
        </div>
      ) : null}
      {err ? <p className="err">{err}</p> : null}
      {res && Number.isFinite(newK1) && newK1 > 0 ? (
        <div className="row-actions">
          <button className="btn primary" type="button" onClick={() => setDraft({ ...draft, flowModel: { ...fm, k1: +newK1.toPrecision(6) } })}>
            Apply k1 = {newK1.toPrecision(5)}
          </button>
          <span className="note">Then Save calibration.</span>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- per-channel card

function ChannelCard({ id, snap }: { id: ChannelId; snap: Snapshot }) {
  const saved = snap.channels.channels[id];
  const savedJson = JSON.stringify(saved);
  const [draft, setDraft] = useState<ChannelConfig>(() => JSON.parse(savedJson));
  const [method, setMethod] = useState<Method>('manual');
  useEffect(() => {
    setDraft(JSON.parse(savedJson));
  }, [savedJson]);

  const dirty = JSON.stringify(draft) !== savedJson;
  const v = snap.numbers.volts[draft.index];
  const value = Number.isFinite(v) ? applyCalibration(draft.calibration, v) - draft.zeroOffset : NaN;
  const savedValue = Number.isFinite(v) ? applyCalibration(saved.calibration, v) - saved.zeroOffset : NaN;
  const u = draft.unit;
  const d = digitsFor(u);
  let third: [string, string] = ['', ''];
  if (id === 'co2') third = ['As mmHg', `${fmt(pctToMmHg(value, snap.settings.processing.barometricMmHg, snap.settings.processing.waterVapor), 1)} mmHg`];
  if (id === 'pneumo') third = ['Flow', `${fmt(pneumoFlow(draft.flowModel ?? { k1: 0.2, k2: 0 }, value), 1)} L/min`];
  if (id === 'flow') third = ['Saved calibration', `${fmt(savedValue, d)} ${u}`];

  const methods: { value: Method; label: string }[] = [
    { value: 'manual', label: 'Enter values' },
    { value: 'zerospan', label: id === 'co2' ? 'Zero & span' : 'Two-point' },
    { value: 'fit', label: 'Multi-point fit' },
  ];
  if (id === 'pneumo') methods.push({ value: 'syringe', label: '3 L syringe' });

  const save = () => {
    try {
      const next = cloneChannels(snap.channels);
      next.channels[id] = { ...draft, calibratedAt: new Date().toISOString() };
      monitor.setChannels(next);
      showToast(`${NAMES[id]} calibration saved.`);
    } catch (e) {
      showToast((e as Error).message, 5000);
    }
  };

  return (
    <section className={`card ${id}`} aria-labelledby={`cal-${id}`}>
      <div className="card-head">
        <h3 id={`cal-${id}`}>{NAMES[id]}</h3>
        <span className="meta">
          CH{draft.index} · {saved.calibratedAt ? `saved ${new Date(saved.calibratedAt).toLocaleString()}` : 'default (not yet calibrated)'}
        </span>
      </div>
      {saved.note && !saved.calibratedAt ? <p className="note">{saved.note}</p> : null}
      <div className="live" aria-live="off">
        <div>
          <div className="k">Raw</div>
          <div className="v">{Number.isFinite(v) ? `${v.toFixed(4)} V` : '-- V'}</div>
        </div>
        <div>
          <div className="k">{dirty ? 'Preview (unsaved)' : 'Calibrated'}</div>
          <div className="v">
            {fmt(value, d)} {u}
          </div>
        </div>
        <div>
          <div className="k">{third[0]}</div>
          <div className="v">{third[1]}</div>
        </div>
      </div>
      <div className="subtabs" role="tablist">
        {methods.map((m) => (
          <button key={m.value} role="tab" className="tab" aria-selected={method === m.value} onClick={() => setMethod(m.value)}>
            {m.label}
          </button>
        ))}
      </div>
      {method === 'manual' ? <ManualEditor id={id} draft={draft} setDraft={setDraft} /> : null}
      {method === 'zerospan' ? <PointsCalibrator key="zs" id={id} draft={draft} setDraft={setDraft} mode="zerospan" /> : null}
      {method === 'fit' ? <PointsCalibrator key="fit" id={id} draft={draft} setDraft={setDraft} mode="fit" /> : null}
      {method === 'syringe' ? <SyringeCalibrator draft={draft} setDraft={setDraft} /> : null}
      <div className="row-actions" style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        <button className="btn primary" disabled={!dirty} onClick={save}>
          Save calibration
        </button>
        <button className="btn" disabled={!dirty} onClick={() => setDraft(JSON.parse(savedJson))}>
          Discard changes
        </button>
        <button
          className="btn"
          onClick={() => {
            const def = DEFAULT_CHANNELS.channels[id];
            setDraft({ ...JSON.parse(JSON.stringify(def)), index: draft.index });
            showToast('Default values loaded. Save to apply.');
          }}
        >
          Load defaults
        </button>
        {dirty ? <span className="dirty">Unsaved changes</span> : null}
      </div>
    </section>
  );
}

export function CalibrationPanel({ snap }: { snap: Snapshot }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const live = snap.status === 'connected';
  return (
    <div>
      <h2>Calibration</h2>
      <p className="lead">
        Each channel turns the ADC voltage into a reading. Type the numbers in by hand, or capture live readings against references (room air and calibration gas, a reference flowmeter, a 3 L syringe). The preview updates as you edit; nothing changes on the monitor until you press Save.
      </p>
      {!live ? <p className="warn">No live data. You can still enter calibrations by hand; capturing readings needs a connected device or the simulator.</p> : null}
      {snap.usingFileCalibration ? <p className="warn">A replay is running with the calibration stored in its file. Changes saved here apply after the replay ends.</p> : null}
      <div className="row-actions" style={{ marginBottom: 12 }}>
        <button
          className="btn"
          onClick={() => downloadText(JSON.stringify({ ...snap.channels, exportedAt: new Date().toISOString() }, null, 2), `co2r-calibration-${new Date().toISOString().slice(0, 10)}.json`, 'application/json')}
        >
          Export calibration (JSON)
        </button>
        <button className="btn" onClick={() => fileRef.current?.click()}>
          Import calibration…
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = '';
            if (!f) return;
            try {
              monitor.setChannels(validateChannels(JSON.parse(await f.text())));
              showToast(`Imported ${f.name}.`);
            } catch (err) {
              showToast(`Could not import ${f.name}: ${(err as Error).message}`, 6000);
            }
          }}
        />
        <button
          className="btn danger"
          onClick={() => {
            void monitor.resetChannels();
            showToast('All channels reset to config/channels.json.');
          }}
        >
          Reset all to defaults
        </button>
        <span className="note">Saved in this browser. Export a copy for your lab notebook.</span>
      </div>
      <div className="cards">
        {(['co2', 'flow', 'pneumo'] as ChannelId[]).map((id) => (
          <ChannelCard key={id} id={id} snap={snap} />
        ))}
      </div>
    </div>
  );
}

