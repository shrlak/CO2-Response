import { DEFAULT_PROCESSING, type ProcessingOptions } from '../processing/pipeline';
import type { ChannelsConfig } from '../config/channels';
import { validateChannels } from '../config/channels';
import type { Scenario } from '../sim/physiology';

export type ThemePref = 'dark' | 'light' | 'system';
export type Co2Unit = 'mmHg' | '%';

export interface AlarmSettings {
  /** what the CO2 limits are compared against */
  source: 'etco2' | 'live';
  highMmHg: number;
  /** null = off */
  lowMmHg: number | null;
  /** consecutive breaths beyond a limit before alarming (EtCO2 source) */
  delayBreaths: number;
  /** seconds beyond a limit before alarming (live source) */
  delaySec: number;
  hysteresisMmHg: number;
  /** audio silence length, s (max 120) */
  silenceSec: number;
  /** 0.1–1; cannot be 0 */
  volume: number;
  /** no breath for this long raises a high-priority alarm; 0 = off */
  apneaSec: number;
}

export interface SimSettings {
  scenario: Scenario;
  rr: number;
  etco2: number;
  vtMl: number;
}

export interface Settings {
  theme: ThemePref;
  co2Unit: Co2Unit;
  /** seconds per screen for the sweep */
  sweepSec: number;
  /** capnogram full scale, mmHg */
  co2ScaleMaxMmHg: number;
  /** flow panels ± full scale, L/min */
  flowScaleLpm: number;
  showRawVolts: boolean;
  trendMinutes: number;
  processing: ProcessingOptions;
  alarms: AlarmSettings;
  studyCode: string;
  baudRate: number;
  bridgeUrl: string;
  /** reconnect by itself when the same Arduino is plugged back in */
  autoReconnect: boolean;
  /** start recording as soon as an Arduino or bridge connects */
  autoRecord: boolean;
  sim: SimSettings;
}

export const DEFAULT_ALARMS: AlarmSettings = {
  source: 'etco2',
  highMmHg: 50, // TODO: confirm default limits with the PI / clinicians
  lowMmHg: null,
  delayBreaths: 2,
  delaySec: 5,
  hysteresisMmHg: 2,
  silenceSec: 120,
  volume: 0.8,
  apneaSec: 20,
};

export const DEFAULT_SETTINGS: Settings = {
  theme: 'dark',
  co2Unit: 'mmHg',
  sweepSec: 12,
  co2ScaleMaxMmHg: 60,
  flowScaleLpm: 60,
  showRawVolts: false,
  trendMinutes: 30,
  processing: DEFAULT_PROCESSING,
  alarms: DEFAULT_ALARMS,
  studyCode: 'STUDY-01',
  baudRate: 115200,
  bridgeUrl: 'ws://localhost:8765',
  autoReconnect: true,
  autoRecord: false,
  sim: { scenario: 'normal', rr: 15, etco2: 38, vtMl: 500 },
};

const SETTINGS_KEY = 'co2r.settings.v1';
const CHANNELS_KEY = 'co2r.channels.v1';

function read(key: string): unknown {
  try {
    const s = localStorage.getItem(key);
    return s ? JSON.parse(s) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked or full: settings stay for this session only */
  }
}

/** Enforce the safety limits on alarm settings regardless of what was stored. */
export function sanitizeAlarms(a: AlarmSettings): AlarmSettings {
  const clamp = (v: number, lo: number, hi: number, d: number) => (Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);
  return {
    ...a,
    source: a.source === 'live' ? 'live' : 'etco2',
    highMmHg: clamp(a.highMmHg, 5, 150, DEFAULT_ALARMS.highMmHg),
    lowMmHg: a.lowMmHg === null || a.lowMmHg === undefined ? null : clamp(a.lowMmHg, 0, 100, 0),
    delayBreaths: Math.round(clamp(a.delayBreaths, 1, 10, 2)),
    delaySec: clamp(a.delaySec, 0, 60, 5),
    hysteresisMmHg: clamp(a.hysteresisMmHg, 0, 10, 2),
    silenceSec: clamp(a.silenceSec, 10, 120, 120),
    volume: clamp(a.volume, 0.1, 1, 0.8),
    apneaSec: clamp(a.apneaSec, 0, 120, 20),
  };
}

export function loadSettings(): Settings {
  const s = (read(SETTINGS_KEY) ?? {}) as Partial<Settings>;
  return {
    ...DEFAULT_SETTINGS,
    ...s,
    processing: { ...DEFAULT_SETTINGS.processing, ...(s.processing ?? {}) },
    alarms: sanitizeAlarms({ ...DEFAULT_SETTINGS.alarms, ...(s.alarms ?? {}) }),
    sim: { ...DEFAULT_SETTINGS.sim, ...(s.sim ?? {}) },
  };
}

export function saveSettings(s: Settings): void {
  write(SETTINGS_KEY, s);
}

export function loadSavedChannels(): ChannelsConfig | null {
  const c = read(CHANNELS_KEY);
  if (!c) return null;
  try {
    return validateChannels(c);
  } catch {
    return null;
  }
}

export function saveChannels(c: ChannelsConfig): void {
  write(CHANNELS_KEY, c);
}

export function clearSavedChannels(): void {
  try {
    localStorage.removeItem(CHANNELS_KEY);
  } catch {
    /* ignore */
  }
}
