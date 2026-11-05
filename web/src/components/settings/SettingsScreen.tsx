import type { Snapshot } from '../../engine/monitor';
import { monitor } from '../../engine/monitor';
import { AlarmForm } from '../AlarmForm';
import { showToast } from '../toast';
import { CalibrationPanel } from './CalibrationPanel';
import { ConnectionPanel } from './ConnectionPanel';
import { DiagnosticsPanel } from './DiagnosticsPanel';
import { DisplayPanel } from './DisplayPanel';
import { SessionPanel } from './SessionPanel';

export type SettingsTab = 'connection' | 'calibration' | 'alarms' | 'display' | 'session' | 'diagnostics';

const TABS: { id: SettingsTab; label: string }[] = [
  { id: 'connection', label: 'Connection' },
  { id: 'calibration', label: 'Calibration' },
  { id: 'alarms', label: 'Alarms' },
  { id: 'display', label: 'Display & units' },
  { id: 'session', label: 'Session & recording' },
  { id: 'diagnostics', label: 'Diagnostics' },
];

export function SettingsScreen({ snap, tab, setTab, onBack }: { snap: Snapshot; tab: SettingsTab; setTab: (t: SettingsTab) => void; onBack: () => void }) {
  return (
    <div className="settings-screen">
      <nav className="settings-nav" aria-label="Settings sections">
        <button className="btn primary back" onClick={onBack}>
          ← Back to monitor
        </button>
        {TABS.map((t) => (
          <button key={t.id} className="navbtn" aria-current={tab === t.id ? 'page' : undefined} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
        <p className="note" style={{ marginTop: 'auto' }}>
          Alarms keep running while you are on this screen.
        </p>
      </nav>
      <div className="settings-body">
        {tab === 'connection' ? <ConnectionPanel snap={snap} /> : null}
        {tab === 'calibration' ? <CalibrationPanel snap={snap} /> : null}
        {tab === 'alarms' ? (
          <div style={{ maxWidth: 720 }}>
            <h2>Alarms</h2>
            <p className="lead">CO2 limits, delays and audio. Changes apply immediately. Technical alarms (no data, sensor at rail, device disconnected) are always on.</p>
            <AlarmForm idPrefix="settings" value={snap.settings.alarms} onChange={(a) => monitor.updateSettings({ alarms: a })} />
            <div className="row-actions" style={{ marginTop: 14 }}>
              <button className={`btn ${snap.audioArmed ? '' : 'primary'}`} onClick={() => void monitor.armAudio()}>
                {snap.audioArmed ? 'Audio armed' : 'Arm audio'}
              </button>
              <button
                className="btn"
                onClick={() => {
                  if (!snap.audioArmed) showToast('Arm audio first to hear the test tones.');
                  monitor.testAlarm();
                }}
              >
                Test alarm (high, then medium tone)
              </button>
            </div>
          </div>
        ) : null}
        {tab === 'display' ? <DisplayPanel snap={snap} /> : null}
        {tab === 'session' ? <SessionPanel snap={snap} /> : null}
        {tab === 'diagnostics' ? <DiagnosticsPanel snap={snap} /> : null}
      </div>
    </div>
  );
}
