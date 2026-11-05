import { useEffect, useRef, useState } from 'react';
import { monitor, type Snapshot } from '../../engine/monitor';
import { breathsCsv, eventsCsv, sessionFileName } from '../../storage/csv';
import { downloadText, saveBlob } from '../../storage/download';
import type { SessionRecord } from '../../storage/library';
import { Field } from '../fields';
import { duration, fmt, hms } from '../format';
import { toggleRecording } from '../recording';
import { showToast } from '../toast';

const STATUS: Record<SessionRecord['status'], string> = {
  recording: 'Recording',
  complete: 'Recorded',
  interrupted: 'Recovered',
  imported: 'Imported',
};

const mb = (b: number) => (b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} kB`);
const stamp = (ms: number) => new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export async function exportSession(rec: SessionRecord, what: 'session' | 'breaths' | 'events'): Promise<void> {
  try {
    if (what === 'session') {
      const r = await saveBlob(await monitor.library.blob(rec.id), rec.fileName);
      if (r !== 'declined') showToast(`Saved ${rec.fileName}.`);
      return;
    }
    const parsed = await monitor.library.parse(rec.id);
    const name = rec.fileName.replace(/\.csv$/i, '') + (what === 'breaths' ? '_breaths.csv' : '_events.csv');
    const r = await downloadText(what === 'breaths' ? breathsCsv(parsed) : eventsCsv(parsed), name, 'text/csv');
    if (r !== 'declined') showToast(`Saved ${name}.`);
  } catch (e) {
    showToast(`Export failed: ${(e as Error).message}`, 6000);
  }
}

export async function importFiles(files: FileList | File[]): Promise<void> {
  for (const f of Array.from(files)) {
    try {
      const { rec } = await monitor.library.importFile(f, f.name);
      showToast(`Imported ${f.name}: ${rec.stats.breaths ?? 0} breaths, ${duration((rec.stats.durationS ?? 0) * 1000)}.`, 5000);
    } catch (e) {
      showToast(`Could not import ${f.name}: ${(e as Error).message}`, 7000);
    }
  }
  monitor.libraryChanged();
}

function ExportMenu({ rec }: { rec: SessionRecord }) {
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: 'relative' }}>
      <button className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>
        Export ▾
      </button>
      {open ? (
        <span className="menu" role="menu" onMouseLeave={() => setOpen(false)}>
          <button role="menuitem" onClick={() => (setOpen(false), void exportSession(rec, 'session'))}>
            Whole session (one .csv)
          </button>
          <button role="menuitem" onClick={() => (setOpen(false), void exportSession(rec, 'breaths'))}>
            Breaths only (.csv)
          </button>
          <button role="menuitem" onClick={() => (setOpen(false), void exportSession(rec, 'events'))}>
            Alarms & events only (.csv)
          </button>
        </span>
      ) : null}
    </span>
  );
}

export function DataPanel({ snap, openReview }: { snap: Snapshot; openReview: (id: string) => void }) {
  const s = snap.settings;
  const rec = snap.recording;
  const [code, setCode] = useState(s.studyCode);
  const [sessions, setSessions] = useState<SessionRecord[] | null>(null);
  const [err, setErr] = useState('');
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ usage: number; quota: number; persisted: boolean } | null>(null);
  const [drag, setDrag] = useState(false);
  const [, force] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const tick = rec.active ? Math.floor(snap.now / 2000) : 0;

  useEffect(() => {
    let live = true;
    monitor.library
      .list()
      .then((l) => live && (setSessions(l), setErr('')))
      .catch((e: Error) => live && (setSessions([]), setErr(e.message)));
    monitor.library.usage().then((u) => live && setUsage(u));
    return () => {
      live = false;
    };
  }, [rec.libraryVersion, tick]);

  return (
    <div style={{ maxWidth: 1180 }}>
      <h2>Data & recordings</h2>
      <p className="lead">
        Each recording is one CSV file with every sample, every breath, every alarm and every marker, ready for Excel, MATLAB, Python or R. While it records it is saved to this browser every second, so a crash loses at most a second. When you stop, the file is saved to your computer and also kept here. Never put a name, MRN or date of birth in the study code.
      </p>

      <div className="cards" style={{ marginBottom: 12 }}>
        <section className="card">
          <div className="card-head">
            <h3>Recording</h3>
            <span className="meta">{rec.active ? rec.fileName : `Next file: ${sessionFileName(s.studyCode)}`}</span>
          </div>
          <Field label="Study code" htmlFor="study" hint="Goes in the file name and header. Letters, numbers and dashes work best.">
            <input id="study" type="text" className="wide" value={code} maxLength={40} disabled={rec.active} onChange={(e) => setCode(e.target.value)} onBlur={() => monitor.updateSettings({ studyCode: code.trim() })} />
          </Field>
          {rec.active ? (
            <p>
              Recording for <b>{duration(snap.now - rec.startedAt)}</b>, {mb(rec.bytes)} so far{rec.stored ? ', saved to this browser every second.' : '.'}
              {rec.writeError ? <span className="warn"> Browser storage failed ({rec.writeError}); the rest is kept in memory and saved when you stop.</span> : null}
            </p>
          ) : (
            <p className="note">Press Record on the monitor or Start here. Stopping saves the file to your Downloads folder.</p>
          )}
          <div className="row-actions">
            <button className={`btn ${rec.active ? 'danger' : 'primary'}`} onClick={() => void toggleRecording()}>
              {rec.active ? 'Stop and save file' : 'Start recording'}
            </button>
          </div>
        </section>

        <section
          className={`card dropzone ${drag ? 'drag' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (e.dataTransfer.files.length) void importFiles(e.dataTransfer.files);
          }}
        >
          <div className="card-head">
            <h3>Import</h3>
            <span className="meta">.csv session files, v1 or v2</span>
          </div>
          <p className="note">Bring a session back to review it, replay it through the monitor, or export parts of it. Drop files here or choose them. Files made by older versions of this app are converted when you export them.</p>
          <div className="row-actions">
            <button className="btn primary" onClick={() => fileRef.current?.click()}>
              Import session files…
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) void importFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </div>
        </section>
      </div>

      <section className="card" style={{ marginBottom: 12 }}>
        <div className="card-head">
          <h3>Saved sessions</h3>
          <span className="meta">
            {usage ? `${mb(usage.usage)} used of ${mb(usage.quota)} available in this browser` : ''}
            {usage && !usage.persisted ? (
              <button className="btn small" style={{ marginLeft: 8 }} onClick={() => void monitor.library.persist().then((ok) => (showToast(ok ? 'The browser will keep these sessions.' : 'The browser did not agree to keep them permanently; export important sessions.'), monitor.libraryChanged()))}>
                Keep when disk is low
              </button>
            ) : null}
          </span>
        </div>
        {err ? <p className="warn">{err}</p> : null}
        {sessions === null ? (
          <p className="note">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="note">No sessions yet. Record one, or import a file.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pts sessions">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Study</th>
                  <th>Started</th>
                  <th>Length</th>
                  <th>Size</th>
                  <th>Breaths</th>
                  <th>Alarms</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sessions.map((r) => {
                  const recording = r.status === 'recording';
                  const len = recording && rec.active ? snap.now - r.startedAt : (r.stats.durationS ?? ((r.endedAt ?? r.lastWriteAt) - r.startedAt) / 1000) * 1000;
                  return (
                    <tr key={r.id}>
                      <td className="mono">{r.fileName}</td>
                      <td>{r.studyCode || '—'}</td>
                      <td>{stamp(r.startedAt)}</td>
                      <td>{duration(len)}</td>
                      <td>{mb(r.bytes)}</td>
                      <td>{r.stats.breaths ?? '—'}</td>
                      <td>{r.stats.alarms ?? '—'}</td>
                      <td>
                        <span className={`chip ${r.status}`}>{STATUS[r.status]}</span>
                      </td>
                      <td className="row-actions" style={{ flexWrap: 'nowrap' }}>
                        <button className="btn small" onClick={() => openReview(r.id)}>
                          Open
                        </button>
                        <ExportMenu rec={r} />
                        {confirmDel === r.id ? (
                          <>
                            <button
                              className="btn small danger"
                              onClick={async () => {
                                await monitor.library.delete(r.id);
                                setConfirmDel(null);
                                monitor.libraryChanged();
                                showToast(`Deleted ${r.fileName} from this browser.`);
                              }}
                            >
                              Confirm delete
                            </button>
                            <button className="btn small" onClick={() => setConfirmDel(null)}>
                              Keep
                            </button>
                          </>
                        ) : (
                          <button className="btn small" disabled={recording && rec.active} onClick={() => setConfirmDel(r.id)}>
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="note">Sessions live in this browser on this computer. Clearing site data deletes them, so export anything you need to keep.</p>
      </section>

      <div className="cards">
        <section className="card">
          <div className="card-head">
            <h3>This session, without recording</h3>
          </div>
          <p className="note">The monitor keeps the breath trend since this page was opened, even when nothing is being recorded.</p>
          <div className="row-actions">
            <button
              className="btn"
              disabled={!monitor.trend.length}
              onClick={() => {
                const rows = ['wall_time_iso,etco2_mmHg,rr_bpm,vt_ml,mv_lpm', ...monitor.trend.map((p) => [new Date(p.t * 1000).toISOString(), fmt(p.etco2, 1), fmt(p.rr, 1), fmt(p.vt, 0), fmt(p.mv, 2)].join(',').replace(/--/g, ''))];
                void downloadText(rows.join('\n') + '\n', sessionFileName(s.studyCode).replace('.csv', '_live-breaths.csv'), 'text/csv');
              }}
            >
              Download breath trend (.csv)
            </button>
            <button className="btn" onClick={() => void downloadText(monitor.log.map((l) => `${new Date(l.t).toISOString()}\t${l.level}\t${l.text}`).join('\n') + '\n', sessionFileName(s.studyCode).replace('.csv', '_log.txt'))}>
              Download session log (.txt)
            </button>
          </div>
        </section>

        <section className="card">
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
      </div>
    </div>
  );
}
