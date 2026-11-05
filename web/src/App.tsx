import '@fontsource/barlow-semi-condensed/400.css';
import '@fontsource/barlow-semi-condensed/500.css';
import '@fontsource/barlow-semi-condensed/600.css';
import '@fontsource/barlow-semi-condensed/700.css';
import './styles.css';
import { useEffect, useState } from 'react';
import { AlarmSlot } from './components/AlarmSlot';
import { Header } from './components/Header';
import { MonitorScreen } from './components/MonitorScreen';
import { SettingsScreen, type SettingsTab } from './components/settings/SettingsScreen';
import { Toast } from './components/Toast';
import { monitor, useMonitor } from './engine/useMonitor';
import type { ThemePref } from './storage/settings';

function useResolvedTheme(pref: ThemePref): 'dark' | 'light' {
  const query = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const [systemDark, setSystemDark] = useState(query?.matches ?? true);
  useEffect(() => {
    if (!query) return;
    const on = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    query.addEventListener('change', on);
    return () => query.removeEventListener('change', on);
  }, [query]);
  const theme = pref === 'system' ? (systemDark ? 'dark' : 'light') : pref;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  return theme;
}

function ResearchBanner() {
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem('co2r.ruo') === '1';
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  return (
    <div className="ruo-banner" role="note">
      <span>Research use only. Not a medical device and not for clinical decision-making. Keep a certified monitor as the primary alarm; this alarm works only while this tab is open, the computer is awake and audio is armed.</span>
      <button
        onClick={() => {
          setHidden(true);
          try {
            sessionStorage.setItem('co2r.ruo', '1');
          } catch {
            /* ignore */
          }
        }}
      >
        I understand
      </button>
    </div>
  );
}

export function App() {
  const snap = useMonitor();
  const theme = useResolvedTheme(snap.settings.theme);
  const [screen, setScreen] = useState<'monitor' | 'settings'>('monitor');
  const [tab, setTab] = useState<SettingsTab>('connection');

  useEffect(() => {
    void monitor.init().then(() => {
      const q = new URLSearchParams(location.search);
      if (q.has('sim') || location.hash === '#sim') void monitor.connect('simulator');
    });
  }, []);

  return (
    <div className="app">
      <ResearchBanner />
      <Header snap={snap} />
      <AlarmSlot snap={snap} />
      {screen === 'monitor' ? (
        <MonitorScreen
          snap={snap}
          theme={theme}
          openSettings={(t) => {
            if (t) setTab(t);
            setScreen('settings');
          }}
        />
      ) : (
        <SettingsScreen snap={snap} tab={tab} setTab={setTab} onBack={() => setScreen('monitor')} />
      )}
      <Toast />
    </div>
  );
}
