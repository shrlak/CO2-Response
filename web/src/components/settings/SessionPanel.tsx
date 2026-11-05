import { useState } from 'react';
import { monitor, type Snapshot } from '../../engine/monitor';
import { downloadText } from '../../storage/download';
import { sessionFileName } from '../../storage/csv';
import { Field } from '../fields';
import { duration, fmt, hms } from '../format';
import { showToast } from '../toast';

export function SessionPanel({ snap }: { snap: Snapshot }) {
  const s = snap.settings;
  const [code, setCode] = useState(s.studyCode);
  const [, force] = useState(0);
  const rec = snap.recording;

  return (
    <div style={{ maxWidth: 900 }}>
      <h2>Session & recording</h2>
      <p className="lead">Recordings are CSV files with raw volts, processed values, every breath, every alarm and every marker. Never put a name, MRN or date of birth in the study code.</p>
      <Field label="Study code" htmlFor="study" hint="Free text, e.g. STUDY-01 or SUBJ-07-RUN2. It goes in the file name and header.">
        <input id="study" type="text" className="wide" value={code} maxLength={40} onChange={(e) => setCode(e.target.value)} onBlur={() => monitor.updateSettings({ studyCode: code.trim() })} />
      </Field>

      <section className="card" style={{ margin: '14px 0' }}>
        <div className="card-head">
          <h3>Recording</h3>
          <span className="meta">{rec.active ? `${rec.mode === 'file' ? 'Writing to' : 'Buffering in memory for'} ${rec.fileName}` : `Next file: ${sessionFileName(s.studyCode)}`}</span>
        </div>
        {rec.active ? (
          <p>
            Recording for <b>{duration(snap.now - rec.startedAt)}</b>, {fmt(rec.bytes / 1e6, 1)} MB.
            {rec.mode === 'memory' ? ' The file downloads when you stop. For overnight runs use Chrome or Edge so it streams straight to disk.' : null}
          </p>
        ) : (
          <p className="note">In Chrome and Edge you choose where to save and the file is written as it records, so long sessions do not fill memory.</p>
        )}
        <div className="row-actions">
          {rec.active ? (
            <button className="btn danger" onClick={() => void monitor.stopRecording()}>
              Stop recording
            </button>
          ) : (
            <button className="btn primary" onClick={() => void monitor.startRecording()}>
              Start recording
            </button>
          )}
        </div>
      </section>

      <section className="card" style={{ margin: '14px 0' }}>
        <div className="card-head">
          <h3>Event markers</h3>
          <span className="meta">press M on the monitor to add one</span>
        </div>
        {monitor.markers.length ? (
          <table className="pts">
            <thead>
              <tr>
                <th>Time</th>
                <th>Label</th>
              </tr>
            </thead>
            <tbody>
              {monitor.markers.map((m) => (
                <tr key={m.id}>
                  <td>{hms(m.t)}</td>
                  <td>
                    <input
                      type="text"
                      aria-label={`Marker ${m.id} label`}
                      className="wide"
                      defaultValue={m.label}
                      onBlur={(e) => {
                        monitor.renameMarker(m.id, e.target.value);
                        force((x) => x + 1);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="note">No markers yet.</p>
        )}
      </section>

      <div className="row-actions">
        <button
          className="btn"
          onClick={() => {
            const rows = ['wall_time_iso,etco2_mmHg,rr_per_min,vt_ml,mv_lpm', ...monitor.trend.map((p) => [new Date(p.t * 1000).toISOString(), fmt(p.etco2, 1), fmt(p.rr, 1), fmt(p.vt, 0), fmt(p.mv, 2)].join(',').replace(/--/g, ''))];
            downloadText(rows.join('\n') + '\n', sessionFileName(s.studyCode).replace('.csv', '_breaths.csv'), 'text/csv');
          }}
        >
          Download breath trend (CSV)
        </button>
        <button
          className="btn"
          onClick={() => {
            downloadText(monitor.log.map((l) => `${new Date(l.t).toISOString()}\t${l.level}\t${l.text}`).join('\n') + '\n', sessionFileName(s.studyCode).replace('.csv', '_log.txt'));
            showToast('Session log downloaded.');
          }}
        >
          Download session log
        </button>
      </div>
    </div>
  );
}
