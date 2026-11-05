import { useEffect, useRef, useState } from 'react';
import { monitor, type Snapshot } from '../engine/monitor';
import { mmHgToPct } from '../processing/co2';
import { webSerialSupported } from '../sources/serial';
import { AlarmForm } from './AlarmForm';
import { fmt, mss } from './format';
import { NumericTile } from './NumericTile';
import { toggleRecording } from './recording';
import { showToast } from './toast';
import { TrendPanel } from './TrendPanel';
import { WavePanel } from './WavePanel';
import type { SettingsTab } from './settings/SettingsScreen';

interface Props {
  snap: Snapshot;
  theme: string;
  openSettings: (tab?: SettingsTab) => void;
}

const isTyping = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);

export function MonitorScreen({ snap, theme, openSettings }: Props) {
  const s = snap.settings;
  const n = snap.numbers;
  const [drawer, setDrawer] = useState(false);
  const [mark, setMark] = useState<{ id: number; label: string } | null>(null);
  const markInput = useRef<HTMLInputElement>(null);

  const pct = s.co2Unit === '%';
  const toDisp = (mm: number) => (pct ? mmHgToPct(mm, s.processing.barometricMmHg, s.processing.waterVapor) : mm);
  const co2Digits = pct ? 1 : 0;
  const co2Unit = pct ? '%' : 'mmHg';
  const co2Max = toDisp(s.co2ScaleMaxMmHg);
  const co2Ticks = pct ? [0, 2, 4, 6, 8, 10, 12].filter((t) => t <= co2Max + 0.01) : [0, 20, 40, 60, 80, 100].filter((t) => t <= co2Max);
  const fs = s.flowScaleLpm;
  const flowTicks = [-fs, 0, fs];

  const co2Alarm = snap.alarms.find((a) => (a.id === 'co2High' || a.id === 'co2Low') && a.state === 'active');
  const apnea = snap.alarms.find((a) => a.id === 'apnea' && a.state === 'active');
  const lim = (v: number | null) => (v === null ? 'off' : fmt(toDisp(v), co2Digits));
  const raw = (i: number) => (s.showRawVolts && Number.isFinite(n.volts[i]) ? `${n.volts[i].toFixed(4)} V` : null);
  const ch = snap.channels.channels;
  const staleVal = (stale: boolean, v: number, d = 0) => (stale ? '---' : fmt(v, d));

  const doMark = () => {
    const m = monitor.addMarker();
    setMark({ id: m.id, label: m.label });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'm' || e.key === 'M') {
        e.preventDefault();
        const m = monitor.addMarker();
        setMark({ id: m.id, label: m.label });
      }
      if (e.key === 'Escape') setDrawer(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (mark) markInput.current?.select();
  }, [mark]);

  const disconnected = snap.status === 'idle' || snap.status === 'error';
  const tag = snap.frozen ? 'FROZEN' : snap.sourceKind === 'simulator' ? 'SIMULATED' : snap.sourceKind === 'replay' ? 'REPLAY' : null;


  return (
    <div className="monitor-screen">
      <main className="monitor">
        <section className={`row co2 ${co2Alarm?.priority === 'high' || apnea ? 'in-alarm' : ''}`}>
          <WavePanel
            id="co2"
            label="CO2"
            unit={co2Unit}
            colorVar="--co2"
            min={0}
            max={co2Max}
            ticks={co2Ticks}
            limit={toDisp(s.alarms.highMmHg)}
            sweepSec={s.sweepSec}
            rateHz={snap.rateHz}
            theme={theme}
            tag={tag}
          />
          <div className="nums">
            <NumericTile
              id="tile-etco2"
              big
              label="EtCO2"
              unit={co2Unit}
              value={staleVal(snap.stale.co2, toDisp(n.etco2), co2Digits)}
              stale={snap.stale.co2}
              alarm={co2Alarm ? (co2Alarm.priority === 'high' ? 'high' : 'medium') : null}
              flash={!!co2Alarm && !co2Alarm.acknowledged}
              limits={{ hi: lim(s.alarms.highMmHg), lo: lim(s.alarms.lowMmHg) }}
              qualifier={snap.stale.co2 ? 'no CO2 signal' : `median 3 br: ${fmt(toDisp(n.etco2Median), co2Digits)}`}
              raw={raw(ch.co2.index)}
            />
            <NumericTile id="tile-rr" label="RR" unit="/min" value={fmt(n.rr)} alarm={apnea ? 'high' : null} flash={!!apnea && !apnea.acknowledged} qualifier={Number.isNaN(n.rr) && snap.status === 'connected' ? 'no breath detected' : null} />
            <NumericTile id="tile-fico2" label="FiCO2" unit={co2Unit} value={staleVal(snap.stale.co2, toDisp(n.fico2), co2Digits)} stale={snap.stale.co2} />
          </div>
        </section>

        <section className="row flow">
          <WavePanel id="flow" label="Flow" unit="L/min" colorVar="--flow" min={-fs} max={fs} ticks={flowTicks} signed sweepSec={s.sweepSec} rateHz={snap.rateHz} theme={theme} />
          <div className="nums">
            <NumericTile id="tile-flow" label="Flow" unit="L/min" value={staleVal(snap.stale.flow, n.flow)} stale={snap.stale.flow} raw={raw(ch.flow.index)} />
            <NumericTile id="tile-peak" label="Peak insp" unit="L/min" value={staleVal(snap.stale.flow, n.peakFlow)} stale={snap.stale.flow} />
          </div>
        </section>

        <section className="row pneumo">
          <WavePanel id="pneumo" label="Pneumotach" unit="L/min" colorVar="--pneumo" min={-fs} max={fs} ticks={flowTicks} signed sweepSec={s.sweepSec} rateHz={snap.rateHz} theme={theme} />
          <div className="nums">
            <NumericTile id="tile-vt" label="VT" unit="mL" value={staleVal(snap.stale.pneumo, n.vt)} stale={snap.stale.pneumo} raw={raw(ch.pneumo.index)} qualifier={snap.stale.pneumo ? null : `flow ${fmt(n.pneumoFlow)} L/min`} />
            <NumericTile id="tile-mv" label="MV" unit="L/min" value={staleVal(snap.stale.pneumo, n.mv, 1)} stale={snap.stale.pneumo} />
          </div>
        </section>

        {disconnected ? (
          <div className="connect-overlay">
            <div className="connect-card">
              <h2>{snap.status === 'error' ? 'Connection lost' : 'No signal source'}</h2>
              {snap.statusDetail ? <p className="err">{snap.statusDetail}</p> : null}
              {snap.awaitingReconnect ? <p className="warn">Waiting for the Arduino to be plugged back in…</p> : null}
              <p>Connect the Arduino over USB, or start the simulator to try the monitor without hardware. Waveforms and alarms start as soon as data arrives.</p>
              <div className="actions">
                {snap.knownPort ? (
                  <button className="btn primary" onClick={() => void monitor.reconnectKnownPort()}>
                    Reconnect {snap.knownPort}
                  </button>
                ) : null}
                <button className={`btn ${snap.knownPort ? '' : 'primary'}`} disabled={!webSerialSupported()} onClick={() => void monitor.connect('serial')}>
                  {snap.knownPort ? 'Choose another port…' : 'Connect Arduino (USB)'}
                </button>
                <button className="btn" onClick={() => void monitor.connect('simulator')}>
                  Start simulator
                </button>
                <button className="btn" onClick={() => openSettings('connection')}>
                  More sources…
                </button>
              </div>
              {!webSerialSupported() ? <p className="note">This browser has no Web Serial. Use desktop Chrome or Edge, or run the Python bridge.</p> : null}
            </div>
          </div>
        ) : null}
      </main>

      <TrendPanel snap={snap} theme={theme} />

      <nav className="softkeys" aria-label="Monitor controls">
        <button className={`key ${snap.silenceMs > 0 ? 'active' : ''}`} onClick={() => monitor.toggleSilence()}>
          {snap.silenceMs > 0 ? 'Cancel silence' : 'Silence'}
          <span className="sub">{snap.silenceMs > 0 ? `${mss(snap.silenceMs)} left` : `${s.alarms.silenceSec} s`}</span>
        </button>
        <button className={`key ${snap.audioArmed ? 'armed' : 'need'}`} onClick={() => void monitor.armAudio()}>
          {snap.audioArmed ? 'Audio armed' : 'Arm audio'}
          <span className="sub">{snap.audioArmed ? 'tones will sound' : 'click once per session'}</span>
        </button>
        <div style={{ position: 'relative', display: 'grid' }}>
          <button className="key" onClick={doMark}>
            Mark event<span className="sub">hotkey M</span>
          </button>
          {mark ? (
            <form
              className="popover"
              style={{ left: 0 }}
              onSubmit={(e) => {
                e.preventDefault();
                monitor.renameMarker(mark.id, mark.label);
                setMark(null);
              }}
            >
              <label htmlFor="mark-label" className="note">
                Label for the marker just placed
              </label>
              <input
                id="mark-label"
                ref={markInput}
                type="text"
                value={mark.label}
                onChange={(e) => setMark({ ...mark, label: e.target.value })}
                onKeyDown={(e) => e.key === 'Escape' && setMark(null)}
              />
              <div className="row-actions">
                <button className="btn primary small" type="submit">
                  Save label
                </button>
                <button className="btn small" type="button" onClick={() => setMark(null)}>
                  Keep “{mark.label}”
                </button>
              </div>
            </form>
          ) : null}
        </div>
        <button className={`key ${snap.recording.active ? 'rec-on' : ''}`} onClick={() => void toggleRecording()}>
          {snap.recording.active ? '■ Stop rec' : '● Record'}
          <span className="sub">{snap.recording.active ? 'saves one CSV on stop' : 'one CSV per session'}</span>
        </button>
        <button className={`key ${snap.frozen ? 'active' : ''}`} onClick={() => monitor.setFrozen(!snap.frozen)}>
          {snap.frozen ? 'Unfreeze' : 'Freeze'}
          <span className="sub">waveforms only</span>
        </button>
        <button className="key" onClick={() => setDrawer(true)}>
          Alarm limits
          <span className="sub">
            high {fmt(toDisp(s.alarms.highMmHg), co2Digits)}
            {s.alarms.lowMmHg !== null ? ` · low ${fmt(toDisp(s.alarms.lowMmHg), co2Digits)}` : ''}
          </span>
        </button>
        <button className="key" onClick={() => openSettings()}>
          Settings
          <span className="sub">calibration, connection</span>
        </button>
      </nav>

      {drawer ? (
        <>
          <div className="drawer-backdrop" onClick={() => setDrawer(false)} />
          <aside className="drawer" aria-label="Alarm limits">
            <h2>Alarm limits</h2>
            <p className="note">Changes apply immediately and are saved in this browser.</p>
            <AlarmForm idPrefix="drawer" value={s.alarms} onChange={(a) => monitor.updateSettings({ alarms: a })} />
            <div className="row-actions" style={{ marginTop: 'auto' }}>
              <button
                className="btn"
                onClick={() => {
                  if (!snap.audioArmed) showToast('Arm audio first to hear the test tones.');
                  monitor.testAlarm();
                }}
              >
                Test alarm
              </button>
              <button className="btn primary" autoFocus onClick={() => setDrawer(false)}>
                Done
              </button>
            </div>
          </aside>
        </>
      ) : null}
    </div>
  );
}
