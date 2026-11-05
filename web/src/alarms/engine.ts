import type { AlarmSettings } from '../storage/settings';

export type Priority = 'high' | 'medium' | 'advisory';

export type AlarmId =
  | 'co2High'
  | 'co2Low'
  | 'apnea'
  | 'noData'
  | 'deviceLost'
  | 'adcError'
  | 'railCo2'
  | 'railFlow'
  | 'railPneumo'
  | 'test'
  | 'audioOff'
  | 'dropped'
  | 'checksum';

export interface AlarmRecord {
  id: AlarmId;
  priority: Priority;
  message: string;
  state: 'active' | 'resolved';
  /** wall-clock ms */
  since: number;
  resolvedAt: number | null;
  acknowledged: boolean;
  value?: number;
  peak?: number;
}

export type AlarmLogKind = 'start' | 'resolved' | 'acknowledged' | 'cleared' | 'silenced' | 'unsilenced' | 'rearmed';

export interface AlarmLogEntry {
  t: number;
  kind: AlarmLogKind;
  id?: AlarmId;
  priority?: Priority;
  message: string;
}

const RANK: Record<Priority, number> = { high: 0, medium: 1, advisory: 2 };

/**
 * Alarm state machine. Pure: time is always passed in, nothing touches the DOM.
 *
 * - Physiological CO2 alarms need `delayBreaths` consecutive breaths (EtCO2 source)
 *   or `delaySec` continuous seconds (live source) beyond the limit.
 * - They clear only past the limit by `hysteresisMmHg`.
 * - High and medium alarms latch: once the condition clears they stay, marked
 *   resolved, until acknowledged. Advisories come and go on their own.
 * - Silence mutes audio for `silenceSec` (≤ 120 s) and then re-arms by itself.
 */
export class AlarmEngine {
  private records = new Map<AlarmId, AlarmRecord>();
  private highCount = 0;
  private lowCount = 0;
  private highSince: number | null = null;
  private lowSince: number | null = null;
  private silencedUntil = 0;

  constructor(
    public settings: AlarmSettings,
    private log: (e: AlarmLogEntry) => void = () => {},
  ) {}

  setSettings(s: AlarmSettings): void {
    this.settings = s;
    if (s.lowMmHg === null) {
      this.lowCount = 0;
      this.lowSince = null;
      const low = this.records.get('co2Low');
      if (low?.state === 'active') this.resolve('co2Low', Date.now());
    }
  }

  // ---------- generic conditions ----------

  /** Raise or clear a condition. High/medium latch on clear; advisories just disappear. */
  setCondition(id: AlarmId, active: boolean, t: number, priority: Priority, message: string, value?: number): void {
    const r = this.records.get(id);
    if (active) {
      if (!r || r.state === 'resolved') {
        this.records.set(id, { id, priority, message, state: 'active', since: t, resolvedAt: null, acknowledged: false, value, peak: value });
        if (priority !== 'advisory') this.log({ t, kind: 'start', id, priority, message });
      } else {
        r.message = message;
        r.value = value;
        if (value !== undefined) r.peak = r.peak === undefined ? value : id === 'co2Low' ? Math.min(r.peak, value) : Math.max(r.peak, value);
      }
    } else if (r && r.state === 'active') {
      this.resolve(id, t);
    }
  }

  private resolve(id: AlarmId, t: number): void {
    const r = this.records.get(id);
    if (!r) return;
    if (r.priority === 'advisory' || id === 'test') {
      this.records.delete(id);
      return;
    }
    r.state = 'resolved';
    r.resolvedAt = t;
    this.log({ t, kind: 'resolved', id, priority: r.priority, message: r.message });
  }

  // ---------- CO2 limits ----------

  private highMsg(v: number) {
    return `EtCO2 HIGH ${v.toFixed(0)} mmHg > ${this.settings.highMmHg}`;
  }
  private lowMsg(v: number) {
    return `EtCO2 LOW ${v.toFixed(0)} mmHg < ${this.settings.lowMmHg}`;
  }

  /** Call once per detected breath with its EtCO2 (mmHg). */
  onBreath(etco2: number, t: number): void {
    if (this.settings.source !== 'etco2' || !Number.isFinite(etco2)) return;
    const { highMmHg, lowMmHg, hysteresisMmHg, delayBreaths } = this.settings;
    const high = this.records.get('co2High');

    if (etco2 > highMmHg) {
      this.highCount++;
      if (this.highCount >= delayBreaths || high?.state === 'active') this.setCondition('co2High', true, t, 'high', this.highMsg(etco2), etco2);
    } else {
      this.highCount = 0;
      if (high?.state === 'active') {
        if (etco2 < highMmHg - hysteresisMmHg) this.resolve('co2High', t);
        else this.setCondition('co2High', true, t, 'high', this.highMsg(etco2), etco2);
      }
    }

    if (lowMmHg !== null) {
      const low = this.records.get('co2Low');
      if (etco2 < lowMmHg) {
        this.lowCount++;
        if (this.lowCount >= delayBreaths || low?.state === 'active') this.setCondition('co2Low', true, t, 'medium', this.lowMsg(etco2), etco2);
      } else {
        this.lowCount = 0;
        if (low?.state === 'active') {
          if (etco2 > lowMmHg + hysteresisMmHg) this.resolve('co2Low', t);
          else this.setCondition('co2Low', true, t, 'medium', this.lowMsg(etco2), etco2);
        }
      }
    }
  }

  /** Call on every sample when the alarm source is the live CO2 waveform (mmHg). */
  onLiveCo2(co2: number, t: number): void {
    if (this.settings.source !== 'live' || !Number.isFinite(co2)) return;
    const { highMmHg, lowMmHg, hysteresisMmHg, delaySec } = this.settings;
    const ms = delaySec * 1000;
    const high = this.records.get('co2High');
    const live = (v: number, hi: boolean) => `CO2 ${hi ? 'HIGH' : 'LOW'} ${v.toFixed(0)} mmHg ${hi ? '>' : '<'} ${hi ? highMmHg : lowMmHg}`;

    if (co2 > highMmHg) {
      this.highSince ??= t;
      if (t - this.highSince >= ms || high?.state === 'active') this.setCondition('co2High', true, t, 'high', live(co2, true), co2);
    } else {
      this.highSince = null;
      if (high?.state === 'active' && co2 < highMmHg - hysteresisMmHg) this.resolve('co2High', t);
    }

    if (lowMmHg !== null) {
      const low = this.records.get('co2Low');
      if (co2 < lowMmHg) {
        this.lowSince ??= t;
        if (t - this.lowSince >= ms || low?.state === 'active') this.setCondition('co2Low', true, t, 'medium', live(co2, false), co2);
      } else {
        this.lowSince = null;
        if (low?.state === 'active' && co2 > lowMmHg + hysteresisMmHg) this.resolve('co2Low', t);
      }
    }
  }

  // ---------- operator actions ----------

  /**
   * Acknowledge an alarm (the top one if no id). A resolved alarm is cleared;
   * an active one is marked acknowledged and the audio is silenced.
   */
  acknowledge(t: number, id?: AlarmId): void {
    const r = id ? this.records.get(id) : this.list().find((x) => x.priority !== 'advisory');
    if (!r || r.priority === 'advisory') return;
    if (r.state === 'resolved') {
      this.records.delete(r.id);
      this.log({ t, kind: 'cleared', id: r.id, priority: r.priority, message: r.message });
    } else {
      r.acknowledged = true;
      this.log({ t, kind: 'acknowledged', id: r.id, priority: r.priority, message: r.message });
      this.silence(t);
    }
  }

  silence(t: number): void {
    this.silencedUntil = t + Math.min(120, this.settings.silenceSec) * 1000;
    this.log({ t, kind: 'silenced', message: `Audio silenced for ${Math.min(120, this.settings.silenceSec)} s` });
  }

  unsilence(t: number): void {
    if (this.silencedUntil <= t) return;
    this.silencedUntil = 0;
    this.log({ t, kind: 'unsilenced', message: 'Silence cancelled' });
  }

  /** Call regularly (e.g. 4 Hz) so an expired silence re-arms and is logged. */
  tick(t: number): void {
    if (this.silencedUntil && t >= this.silencedUntil) {
      this.silencedUntil = 0;
      this.log({ t, kind: 'rearmed', message: 'Silence expired; audio re-armed' });
    }
  }

  isSilenced(t: number): boolean {
    return t < this.silencedUntil;
  }

  silenceRemainingMs(t: number): number {
    return Math.max(0, this.silencedUntil - t);
  }

  get(id: AlarmId): AlarmRecord | undefined {
    return this.records.get(id);
  }

  remove(id: AlarmId): void {
    this.records.delete(id);
  }

  /** Active before resolved, then by priority, then newest first. */
  list(): AlarmRecord[] {
    return [...this.records.values()].sort(
      (a, b) =>
        (a.state === 'active' ? 0 : 1) - (b.state === 'active' ? 0 : 1) ||
        RANK[a.priority] - RANK[b.priority] ||
        b.since - a.since,
    );
  }

  /** Highest priority among active alarms, used to pick the tone. */
  activePriority(): Priority | null {
    let best: Priority | null = null;
    for (const r of this.records.values()) {
      if (r.state !== 'active' || r.priority === 'advisory') continue;
      if (best === null || RANK[r.priority] < RANK[best]) best = r.priority;
    }
    return best;
  }

  reset(): void {
    this.records.clear();
    this.highCount = this.lowCount = 0;
    this.highSince = this.lowSince = null;
  }
}
