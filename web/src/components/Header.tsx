import type { Snapshot } from '../engine/monitor';
import { duration, fmt, hms, mss } from './format';

export function Header({ snap }: { snap: Snapshot }) {
  const connected = snap.status === 'connected';
  const anyActive = snap.alarms.some((a) => a.state === 'active' && a.priority !== 'advisory');
  let pill: { cls: string; text: string };
  if (snap.silenceMs > 0) pill = { cls: 'silenced', text: `SILENCED ${mss(snap.silenceMs)}` };
  else if (anyActive) pill = { cls: 'alarm', text: 'ALARM' };
  else if (!snap.audioArmed) pill = { cls: 'audio-off', text: 'AUDIO OFF' };
  else if (!connected) pill = { cls: '', text: 'DISARMED' };
  else pill = { cls: 'armed', text: 'ARMED' };

  const statusText =
    snap.status === 'connected'
      ? snap.sourceLabel
      : snap.status === 'connecting'
        ? `Connecting… ${snap.sourceLabel}`
        : snap.status === 'error'
          ? 'Connection lost'
          : 'Not connected';

  return (
    <header className="header">
      <span className="ruo-tag">RESEARCH USE ONLY</span>
      <span className="hgroup">
        Study <b>{snap.settings.studyCode || '—'}</b>
      </span>
      <span className="hgroup" title={snap.statusDetail}>
        <span className={`dot ${snap.status}`} aria-hidden="true" />
        <b>{statusText}</b>
        {connected ? (
          <>
            · {fmt(snap.measuredRateHz, 0)} Hz · <b>{snap.dropped}</b> dropped
            {snap.badLines ? (
              <>
                {' '}
                · <b>{snap.badLines}</b> bad lines
              </>
            ) : null}
          </>
        ) : null}
        {snap.usingFileCalibration ? ' · file calibration' : null}
        {snap.replayProgress !== null ? ` · ${Math.round(snap.replayProgress * 100)} %` : null}
      </span>
      <span className="hspacer" />
      <span className={`rec ${snap.recording.active ? 'on' : ''}`}>{snap.recording.active ? `● REC ${duration(snap.now - snap.recording.startedAt)}` : 'Not recording'}</span>
      <span className={`pill ${pill.cls}`}>{pill.text}</span>
      <span className="clock">{hms(snap.now)}</span>
    </header>
  );
}
