import type { Snapshot } from '../../engine/monitor';
import { monitor } from '../../engine/monitor';
import { fmt, hms } from '../format';

export function DiagnosticsPanel({ snap }: { snap: Snapshot }) {
  const stat = (k: string, v: string) => (
    <div key={k}>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
  return (
    <div>
      <h2>Diagnostics</h2>
      <p className="lead">Link quality and the raw stream, for checking wiring and firmware.</p>
      <div className="stats">
        {stat('Samples received', String(snap.received))}
        {stat('Dropped (sequence gaps)', `${snap.dropped} (${fmt(snap.dropRate * 100, 3)} %)`)}
        {stat('Bad lines (checksum/format)', String(snap.badLines))}
        {stat('Measured rate', `${fmt(snap.measuredRateHz, 1)} Hz`)}
        {stat('Device rate', `${snap.rateHz} Hz`)}
        {stat('CO2 / flow / pneumo', (['co2', 'flow', 'pneumo'] as const).map((c) => (snap.stale[c] ? 'stale' : 'ok')).join(' / '))}
      </div>
      <div className="cards">
        <section className="card">
          <h3>Last raw lines</h3>
          <div className="loglist mono" aria-live="off">
            {monitor.recentLines.slice(-25).map((l, i) => (
              <div key={i}>{l}</div>
            ))}
            {!monitor.recentLines.length ? <div>Nothing received yet.</div> : null}
          </div>
        </section>
        <section className="card">
          <h3>Session log</h3>
          <div className="loglist">
            {monitor.log
              .slice(-200)
              .reverse()
              .map((l, i) => (
                <div key={i} className={l.level}>
                  {hms(l.t)} · {l.text}
                </div>
              ))}
          </div>
        </section>
      </div>
    </div>
  );
}
