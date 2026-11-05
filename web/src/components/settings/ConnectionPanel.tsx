import { useEffect, useState } from 'react';
import { monitor, type Snapshot } from '../../engine/monitor';
import { SCENARIOS } from '../../sim/physiology';
import { webSerialSupported } from '../../sources/serial';
import type { SessionRecord } from '../../storage/library';
import { Field, NumberField, NumInput, Seg, SelectField, ToggleField } from '../fields';
import { showToast } from '../toast';

export function ConnectionPanel({ snap }: { snap: Snapshot }) {
  const s = snap.settings;
  const connected = snap.status === 'connected';
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [replayId, setReplayId] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    monitor.library
      .list()
      .then((l) => setSessions(l.filter((r) => r.status !== 'recording')))
      .catch(() => setSessions([]));
  }, [snap.recording.libraryVersion]);
  const chosen = sessions.find((r) => r.id === replayId) ?? sessions[0];
  const [speed, setSpeed] = useState(1);
  const [useFileCal, setUseFileCal] = useState(true);
  const [rate, setRate] = useState(100);
  const [bridgeUrl, setBridgeUrl] = useState(s.bridgeUrl);

  return (
    <div>
      <h2>Connection</h2>
      <p className="lead">
        Status: <b>{snap.status === 'connected' ? `connected to ${snap.sourceLabel}` : snap.status === 'connecting' ? 'connecting…' : snap.status === 'error' ? `lost (${snap.statusDetail})` : 'not connected'}</b>
      </p>

      {connected ? (
        <section className="card" style={{ marginBottom: 12 }}>
          <div className="card-head">
            <h3>Connected device</h3>
            <button className="btn danger" onClick={() => void monitor.disconnect()}>
              Disconnect
            </button>
          </div>
          <div className="stats">
            {Object.entries(snap.info ?? { INFO: 'waiting for reply' }).map(([k, v]) => (
              <div key={k}>
                <div className="k">{k}</div>
                <div className="v">{v}</div>
              </div>
            ))}
          </div>
          {snap.sourceKind === 'serial' || snap.sourceKind === 'bridge' || snap.sourceKind === 'simulator' ? (
            <Field label="Sample rate per channel" hint="Sends RATE to the device (1–200 Hz). 100 Hz is enough for breathing waveforms.">
              <NumInput value={rate} min={1} max={200} onChange={setRate} ariaLabel="Sample rate" />
              <span className="u">Hz</span>
              <button className="btn small" onClick={() => void monitor.send(`RATE ${Math.round(rate)}`).then(() => monitor.send('INFO'))}>
                Send
              </button>
              <button className="btn small" onClick={() => void monitor.send('INFO')}>
                Ask INFO
              </button>
            </Field>
          ) : null}
        </section>
      ) : null}

      <div className="cards">
        <section className="card">
          <div className="card-head">
            <h3>Arduino over USB</h3>
          </div>
          {webSerialSupported() ? (
            <p className="note">Uses Web Serial. Works in desktop Chrome or Edge on https:// pages or localhost. The browser asks which port to use.</p>
          ) : (
            <p className="warn">This browser has no Web Serial. Use desktop Chrome or Edge, or the Python bridge below.</p>
          )}
          <SelectField
            id="baud"
            label="Baud rate"
            value={s.baudRate}
            options={[9600, 57600, 115200, 230400, 250000, 500000].map((b) => ({ value: b, label: String(b) }))}
            onChange={(v) => monitor.updateSettings({ baudRate: v })}
            hint="Must match BAUD_RATE in firmware/include/config.h."
          />
          <div className="row-actions">
            <button className="btn primary" disabled={!webSerialSupported()} onClick={() => void monitor.connect('serial')}>
              Connect Arduino
            </button>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h3>Simulator</h3>
            <span className="meta">no hardware needed</span>
          </div>
          <p className="note">Generates realistic capnogram, flow and pneumotach signals through the same protocol, parser and processing as the Arduino.</p>
          <SelectField id="scenario" label="Scenario" value={s.sim.scenario} options={SCENARIOS.map((x) => ({ value: x.id, label: x.label }))} onChange={(v) => monitor.setSim({ scenario: v })} />
          <NumberField id="sim-rr" label="Respiratory rate" unit="/min" min={4} max={60} value={s.sim.rr} onChange={(v) => monitor.setSim({ rr: v })} />
          <NumberField id="sim-et" label="Baseline EtCO2" unit="mmHg" min={10} max={80} value={s.sim.etco2} onChange={(v) => monitor.setSim({ etco2: v })} />
          <NumberField id="sim-vt" label="Tidal volume" unit="mL" min={100} max={1500} step={10} value={s.sim.vtMl} onChange={(v) => monitor.setSim({ vtMl: v })} />
          <div className="row-actions">
            <button className="btn primary" onClick={() => void monitor.connect('simulator')}>
              {snap.sourceKind === 'simulator' ? 'Restart simulator' : 'Start simulator'}
            </button>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h3>Replay a saved session</h3>
          </div>
          {sessions.length ? (
            <Field label="Session" htmlFor="replay-pick">
              <select id="replay-pick" className="wide" value={chosen?.id ?? ''} onChange={(e) => setReplayId(e.target.value)}>
                {sessions.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.fileName}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <p className="note">No saved sessions yet. Record one, or import a file in Data & recordings.</p>
          )}
          <Field label="Speed">
            <Seg
              label="Replay speed"
              value={speed}
              options={[1, 2, 5, 10].map((x) => ({ value: x, label: `${x}×` }))}
              onChange={setSpeed}
            />
          </Field>
          <ToggleField id="replay-cal" label="Use the calibration saved in the file" checked={useFileCal} onChange={setUseFileCal} hint="On: replays exactly as recorded. Off: re-processes the raw volts with the current calibration." />
          <div className="row-actions">
            <button
              className="btn primary"
              disabled={!chosen || busy}
              onClick={async () => {
                if (!chosen) return;
                setBusy(true);
                try {
                  const session = await monitor.library.parse(chosen.id);
                  if (!session.n) throw new Error('it has no samples');
                  await monitor.connect('replay', { session, fileName: chosen.fileName, speed, useFileCalibration: useFileCal });
                } catch (e) {
                  showToast(`Could not replay ${chosen.fileName}: ${(e as Error).message}`, 6000);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? 'Reading…' : 'Start replay'}
            </button>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h3>Python bridge</h3>
            <span className="meta">Safari, Firefox, second screen</span>
          </div>
          <p className="note">
            Run <span className="mono">python bridge/serial_ws_bridge.py --port COM3</span> on the laptop with the Arduino, then connect here.
          </p>
          <Field label="Bridge address" htmlFor="bridge-url">
            <input id="bridge-url" type="url" className="wide" value={bridgeUrl} onChange={(e) => setBridgeUrl(e.target.value)} onBlur={() => monitor.updateSettings({ bridgeUrl })} />
          </Field>
          <div className="row-actions">
            <button
              className="btn primary"
              onClick={() => {
                monitor.updateSettings({ bridgeUrl });
                void monitor.connect('bridge');
              }}
            >
              Connect to bridge
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
