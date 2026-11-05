import { useRef, useState } from 'react';
import { monitor, type Snapshot } from '../../engine/monitor';
import { overall, reportText, type CheckResult } from '../../engine/linkTest';
import { downloadText } from '../../storage/download';
import { Seg } from '../fields';
import { showToast } from '../toast';

const LABEL = { pass: 'PASS', warn: 'CHECK', fail: 'FAIL', info: 'NOTE' } as const;

export function HardwareCheck({ snap }: { snap: Snapshot }) {
  const [seconds, setSeconds] = useState(10);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState<CheckResult[] | null>(null);
  const [ranFor, setRanFor] = useState(0);
  const cancel = useRef<(() => void) | null>(null);
  const connected = snap.status === 'connected';

  const run = async () => {
    setResults(null);
    setProgress(0);
    setRunning(true);
    const job = monitor.runHardwareCheck(seconds, setProgress);
    cancel.current = job.cancel;
    try {
      const r = await job.done;
      setResults(r);
      setRanFor(seconds);
    } catch (e) {
      showToast((e as Error).message, 5000);
    } finally {
      setRunning(false);
      cancel.current = null;
    }
  };

  const verdict = results ? overall(results) : null;

  return (
    <section className="card" style={{ marginBottom: 12 }}>
      <div className="card-head">
        <h3>Hardware check</h3>
        <span className="meta">USB link, ADC and each sensor's wiring</span>
      </div>
      <p className="note">
        Run this after wiring or moving the setup, and before each study. It watches the live data and checks the handshake, sample rate, dropped and corrupt lines, and whether each channel is stuck, clipping or noisy. Leave the sensors connected and breathing through them (or the simulator running). 10 minutes is the acceptance test for a new setup (target: under 0.1 % dropped).
      </p>
      <div className="row-actions">
        <Seg
          label="Check length"
          value={seconds}
          options={[
            { value: 10, label: '10 s' },
            { value: 60, label: '1 min' },
            { value: 600, label: '10 min' },
          ]}
          onChange={setSeconds}
        />
        {running ? (
          <button className="btn danger" onClick={() => cancel.current?.()}>
            Stop early ({Math.round(progress * 100)} %)
          </button>
        ) : (
          <button className="btn primary" disabled={!connected} onClick={() => void run()}>
            Run hardware check
          </button>
        )}
        {!connected ? <span className="note">Connect the Arduino or start the simulator first.</span> : null}
        {snap.i2cScan ? <span className="warn">Device I2C scan: {snap.i2cScan}</span> : null}
      </div>
      {running ? (
        <div className="progress" aria-label="Hardware check progress">
          <div style={{ width: `${progress * 100}%` }} />
        </div>
      ) : null}
      {results ? (
        <>
          <p className={`verdict ${verdict}`}>
            {verdict === 'pass' ? 'All checks passed.' : verdict === 'warn' ? 'Usable, but check the items marked CHECK.' : 'Fix the items marked FAIL before using this setup.'}
          </p>
          <table className="pts checks">
            <thead>
              <tr>
                <th />
                <th>Check</th>
                <th>Result</th>
                <th>What to do</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className={`chip st-${r.status}`}>{LABEL[r.status]}</span>
                  </td>
                  <td>{r.label}</td>
                  <td>{r.result}</td>
                  <td className="note">{r.fix ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row-actions" style={{ marginTop: 8 }}>
            <button className="btn small" onClick={() => void downloadText(reportText(results, ranFor), `co2r_hardware-check_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.txt`)}>
              Save report (.txt)
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}
