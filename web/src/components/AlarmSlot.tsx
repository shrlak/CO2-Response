import { useState } from 'react';
import { monitor } from '../engine/monitor';
import type { Snapshot } from '../engine/monitor';
import type { AlarmRecord } from '../alarms/engine';
import { hms, mss } from './format';

const PRI_LABEL = { high: 'HIGH', medium: 'MED', advisory: 'INFO' } as const;

function describe(a: AlarmRecord): string {
  if (a.state === 'resolved') {
    const peak = a.peak !== undefined && (a.id === 'co2High' || a.id === 'co2Low') ? ` (${a.id === 'co2High' ? 'peak' : 'lowest'} ${a.peak.toFixed(0)} mmHg)` : '';
    return `${a.message}${peak}: resolved at ${hms(a.resolvedAt ?? a.since)}`;
  }
  return a.message;
}

export function AlarmSlot({ snap }: { snap: Snapshot }) {
  const [open, setOpen] = useState(false);
  const list = snap.alarms;
  const top = list[0];

  if (!top) {
    return (
      <div className="alarm-slot none" role="status">
        <span className="msg">{snap.status === 'connected' ? 'No active alarms' : 'Not connected. Alarms work only while a source is connected.'}</span>
      </div>
    );
  }

  const cls =
    top.state === 'resolved'
      ? `resolved r-${top.priority}`
      : `${top.priority} ${top.priority === 'high' && !top.acknowledged ? 'flash' : top.priority === 'medium' && !top.acknowledged ? 'flash-slow' : ''}`;

  return (
    <div style={{ position: 'relative' }}>
      <div className={`alarm-slot ${cls}`} role="alert" aria-live="assertive">
        <span className="pri">{top.state === 'resolved' ? 'RESOLVED' : PRI_LABEL[top.priority]}</span>
        <span className="msg">
          {describe(top)}
          {top.state === 'active' && top.priority !== 'advisory' ? <span className="since"> · since {hms(top.since)}</span> : null}
          {snap.silenceMs > 0 && top.priority !== 'advisory' ? <span className="since"> · audio silenced {mss(snap.silenceMs)}</span> : null}
        </span>
        {list.length > 1 ? (
          <button className="more" onClick={() => setOpen(!open)} aria-expanded={open}>
            +{list.length - 1} more
          </button>
        ) : null}
        {top.id === 'audioOff' ? (
          <button className="slot-btn" onClick={() => void monitor.armAudio()}>
            ARM AUDIO
          </button>
        ) : top.priority !== 'advisory' ? (
          <button className="slot-btn" onClick={() => monitor.acknowledge(top.id)}>
            {top.state === 'resolved' ? 'CLEAR' : 'ACKNOWLEDGE'}
          </button>
        ) : null}
      </div>
      {open && list.length > 1 ? (
        <div className="alarm-list">
          <ul>
            {list.map((a) => (
              <li key={a.id}>
                <span className={`prichip ${a.priority}`}>{a.state === 'resolved' ? 'RESOLVED' : PRI_LABEL[a.priority]}</span>
                <span style={{ flex: 1 }}>{describe(a)}</span>
                <span className="note">{hms(a.since)}</span>
                {a.priority !== 'advisory' ? (
                  <button className="btn small" onClick={() => monitor.acknowledge(a.id)}>
                    {a.state === 'resolved' ? 'Clear' : 'Acknowledge'}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
