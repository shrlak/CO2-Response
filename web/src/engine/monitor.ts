import { AlarmAudio } from '../alarms/audio';
import { AlarmEngine, type AlarmLogEntry, type AlarmRecord } from '../alarms/engine';
import { cloneChannels, DEFAULT_CHANNELS, validateChannels, type ChannelId, type ChannelsConfig } from '../config/channels';
import type { Breath } from '../processing/breath';
import { invertCalibration } from '../processing/calibration';
import { etco2Median, minuteVentilation, respiratoryRate } from '../processing/metrics';
import { Pipeline, type Processed } from '../processing/pipeline';
import { parseLine, SeqTracker } from '../protocol/protocol';
import type { SweepRenderer } from '../render/sweep';
import type { Encoders } from '../sim/simulatorCore';
import { BridgeSource } from '../sources/bridge';
import { ReplaySource } from '../sources/replay';
import { SerialSource } from '../sources/serial';
import { SimulatorSource } from '../sources/simulator';
import type { Source, SourceKind, SourceStatus } from '../sources/types';
import type { ParsedSession } from '../storage/csv';
import { SessionRecorder } from '../storage/recorder';
import {
  clearSavedChannels,
  loadSavedChannels,
  loadSettings,
  sanitizeAlarms,
  saveChannels,
  saveSettings,
  type SimSettings,
  type Settings,
} from '../storage/settings';

export interface Numbers {
  co2Live: number;
  etco2: number;
  etco2Median: number;
  rr: number;
  fico2: number;
  flow: number;
  peakFlow: number;
  pneumoFlow: number;
  pneumoPa: number;
  vt: number;
  mv: number;
  volts: readonly number[];
}

export interface TrendPoint {
  /** wall clock, s */
  t: number;
  etco2: number;
  mv: number;
  vt: number;
  rr: number;
}

export interface Marker {
  id: number;
  /** wall clock, ms */
  t: number;
  label: string;
}

export interface LogLine {
  t: number;
  text: string;
  level: 'info' | 'alarm' | 'error';
}

export type EngineStatus = SourceStatus | 'idle';

export interface Snapshot {
  now: number;
  status: EngineStatus;
  statusDetail: string;
  sourceKind: SourceKind | null;
  sourceLabel: string;
  info: Record<string, string> | null;
  rateHz: number;
  measuredRateHz: number;
  received: number;
  dropped: number;
  dropRate: number;
  badLines: number;
  numbers: Numbers;
  stale: Record<ChannelId, boolean>;
  breathStale: boolean;
  alarms: AlarmRecord[];
  silenceMs: number;
  audioArmed: boolean;
  recording: { active: boolean; startedAt: number; fileName: string; mode: 'file' | 'memory'; bytes: number };
  frozen: boolean;
  trendVersion: number;
  settings: Settings;
  channels: ChannelsConfig;
  usingFileCalibration: boolean;
  replayProgress: number | null;
}

const IDS: ChannelId[] = ['co2', 'flow', 'pneumo'];
const MAX_TREND = 20000;
const MAX_TRACE = 4 * 3600 * 2; // 4 h at 2 Hz

/** "True" sensor model for the simulator: the bundled default calibration, inverted. */
export function simulatorEncoders(): Encoders {
  const c = DEFAULT_CHANNELS.channels;
  const { railLowV: lo, railHighV: hi } = DEFAULT_CHANNELS.adc;
  return {
    co2: (pct) => invertCalibration(c.co2.calibration, pct, lo, hi),
    flow: (lpm) => invertCalibration(c.flow.calibration, lpm, lo, hi),
    pneumo: (pa) => invertCalibration(c.pneumo.calibration, pa, lo, hi),
  };
}

/**
 * Owns the data path: source → parser → processing → alarms → recorder, plus the
 * waveform renderers. Runs independently of React; the UI subscribes to snapshots
 * published at 4 Hz. The alarm checks run here, on every sample and breath, not in
 * the drawing loop.
 */
export class MonitorEngine {
  settings: Settings;
  channels: ChannelsConfig;
  readonly alarms: AlarmEngine;
  readonly audio = new AlarmAudio();
  readonly recorder = new SessionRecorder();

  private fileChannels: ChannelsConfig | null = null;
  private source: Source | null = null;
  private status: EngineStatus = 'idle';
  private statusDetail = '';
  private info: Record<string, string> | null = null;
  private pipeline: Pipeline;
  private seq = new SeqTracker();
  private badLines = 0;
  private badTimes: number[] = [];
  private lastDataWall = 0;
  private lastValidWall: Record<ChannelId, number> = { co2: 0, flow: 0, pneumo: 0 };
  private lastBreathWall = 0;
  private last: Processed | null = null;
  private flowPeakCur = -Infinity;
  private flowPeakLast = NaN;
  private rateCount = 0;
  private rateWindowStart = 0;
  private measuredRate = 0;
  private lastHighTone = 0;
  private lastMedTone = 0;
  private titleFlip = false;
  private wakeLock: { release(): Promise<void> } | null = null;
  private traceMax = -Infinity;
  private traceStart = 0;

  /** breaths on the device clock (cleared on every new connection) */
  breaths: Breath[] = [];
  /** per-breath trend on the wall clock (kept across reconnects) */
  trend: TrendPoint[] = [];
  /** continuous CO2 behind the trend: [wall s, mmHg] at 2 Hz */
  traceT: number[] = [];
  traceV: number[] = [];
  markers: Marker[] = [];
  /** where alarms started, for the trend chart (wall ms) */
  alarmStarts: { t: number; priority: 'high' | 'medium' }[] = [];
  log: LogLine[] = [];
  recentLines: string[] = [];
  private trendVersion = 0;
  private frozen = false;
  private markerId = 1;

  private renderers = new Map<ChannelId, Set<SweepRenderer>>();
  private sampleListeners = new Set<(p: Processed) => void>();
  private listeners = new Set<() => void>();
  private snap: Snapshot;
  private lastFrame = 0;

  constructor() {
    this.settings = loadSettings();
    this.channels = loadSavedChannels() ?? cloneChannels(DEFAULT_CHANNELS);
    this.alarms = new AlarmEngine(this.settings.alarms, (e) => this.onAlarmLog(e));
    this.audio.volume = this.settings.alarms.volume;
    this.pipeline = new Pipeline(this.effectiveChannels, this.processingOptions());
    this.snap = this.buildSnapshot();
    if (typeof window !== 'undefined') {
      setInterval(() => this.tick(), 250);
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && this.status === 'connected') void this.requestWakeLock();
      });
      this.startRenderLoop();
    }
  }

  // ---------- subscription ----------

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): Snapshot => this.snap;

  private emit(): void {
    this.snap = this.buildSnapshot();
    for (const l of this.listeners) l();
  }

  // ---------- configuration ----------

  get effectiveChannels(): ChannelsConfig {
    return this.fileChannels ?? this.channels;
  }

  private processingOptions() {
    const rate = Number(this.info?.RATE) || 100;
    return { ...this.settings.processing, sampleRateHz: rate };
  }

  updateSettings(patch: Partial<Settings>): void {
    const next = { ...this.settings, ...patch };
    next.alarms = sanitizeAlarms(next.alarms);
    this.settings = next;
    saveSettings(next);
    this.alarms.setSettings(next.alarms);
    this.audio.volume = next.alarms.volume;
    this.pipeline.setOptions(this.processingOptions());
    this.emit();
  }

  setSim(patch: Partial<SimSettings>): void {
    this.updateSettings({ sim: { ...this.settings.sim, ...patch } });
    if (this.source instanceof SimulatorSource) this.source.core.model.setParams(patch);
  }

  /** Save a new calibration (applies immediately). */
  setChannels(c: ChannelsConfig): void {
    this.channels = validateChannels(cloneChannels(c));
    saveChannels(this.channels);
    if (!this.fileChannels) this.pipeline.setChannels(this.channels);
    this.addLog('Calibration saved');
    this.recorder.event(Date.now(), 'CALIBRATION_CHANGED');
    this.emit();
  }

  /** Back to config/channels.json (or the bundled defaults). */
  async resetChannels(): Promise<void> {
    clearSavedChannels();
    this.channels = await loadDefaultChannels();
    if (!this.fileChannels) this.pipeline.setChannels(this.channels);
    this.addLog('Calibration reset to defaults');
    this.emit();
  }

  /** Load config/channels.json if nothing was saved in this browser. */
  async init(): Promise<void> {
    if (!loadSavedChannels()) {
      this.channels = await loadDefaultChannels();
      this.pipeline.setChannels(this.effectiveChannels);
      this.emit();
    }
  }

  // ---------- connection ----------

  async connect(kind: SourceKind, opts: { session?: ParsedSession; fileName?: string; speed?: number; useFileCalibration?: boolean } = {}): Promise<void> {
    // Build the source before any await: the serial port picker needs the click's user activation.
    let src: Source;
    switch (kind) {
      case 'serial':
        src = new SerialSource(this.settings.baudRate);
        break;
      case 'simulator':
        src = new SimulatorSource(simulatorEncoders(), { ...this.settings.sim, seed: Date.now() & 0xffff }, DEFAULT_CHANNELS.adc.railLowV, DEFAULT_CHANNELS.adc.railHighV);
        break;
      case 'bridge':
        src = new BridgeSource(this.settings.bridgeUrl);
        break;
      case 'replay': {
        if (!opts.session) throw new Error('No session to replay');
        const r = new ReplaySource(opts.session, opts.fileName ?? 'session.csv');
        r.speed = opts.speed ?? 1;
        src = r;
        break;
      }
    }
    const startPromise = this.source ? this.stopSource('Switched source').then(() => this.startSource(src, kind, opts)) : this.startSource(src, kind, opts);
    await startPromise;
  }

  private async startSource(src: Source, kind: SourceKind, opts: { session?: ParsedSession; useFileCalibration?: boolean }): Promise<void> {
    this.fileChannels = kind === 'replay' && opts.useFileCalibration !== false && opts.session?.channels ? opts.session.channels : null;
    this.info = null;
    this.pipeline = new Pipeline(this.effectiveChannels, this.processingOptions());
    this.seq.reset();
    this.badLines = 0;
    this.badTimes = [];
    this.breaths = [];
    this.last = null;
    this.flowPeakCur = -Infinity;
    this.flowPeakLast = NaN;
    const now = Date.now();
    this.lastDataWall = this.lastBreathWall = now;
    this.lastValidWall = { co2: 0, flow: 0, pneumo: 0 };
    for (const set of this.renderers.values()) for (const r of set) r.clear();
    this.alarms.setCondition('deviceLost', false, now, 'medium', '');
    this.source = src;
    this.status = 'connecting';
    this.statusDetail = '';
    this.emit();

    const thisSource = src;
    try {
      await src.start({
        onLine: (l) => {
          if (this.source === thisSource) this.onLine(l);
        },
        onStatus: (s, detail) => {
          if (this.source === thisSource) this.onSourceStatus(s, detail);
        },
      });
    } catch (e) {
      if (this.source !== thisSource) return;
      this.source = null;
      this.fileChannels = null;
      const err = e as DOMException;
      const cancelled = err?.name === 'NotFoundError' || err?.name === 'AbortError';
      this.status = cancelled ? 'idle' : 'error';
      this.statusDetail = cancelled ? '' : err?.message || String(e);
      if (!cancelled) this.addLog(`Connection failed: ${this.statusDetail}`, 'error');
      this.emit();
      return;
    }
    if (kind === 'serial' || kind === 'bridge') {
      await src.send('INFO');
      await src.send('START');
    }
    this.addLog(`Connected: ${src.label}`);
    this.recorder.event(Date.now(), `CONNECTED ${src.label}`);
    void this.requestWakeLock();
    this.emit();
  }

  private onSourceStatus(s: SourceStatus, detail?: string): void {
    const now = Date.now();
    if (s === 'error') {
      this.status = 'error';
      this.statusDetail = detail ?? 'Connection error';
      this.alarms.setCondition('deviceLost', true, now, 'medium', `DEVICE DISCONNECTED: ${this.statusDetail}`);
      this.addLog(this.statusDetail, 'error');
      void this.stopSource(this.statusDetail, true);
    } else if (s === 'disconnected') {
      if (detail) {
        this.addLog(detail);
        void this.stopSource(detail);
      }
    } else {
      this.status = s;
    }
    this.emit();
  }

  /** User-initiated disconnect. */
  async disconnect(): Promise<void> {
    await this.stopSource('Disconnected');
  }

  private async stopSource(reason: string, keepError = false): Promise<void> {
    const src = this.source;
    if (!src) return;
    this.source = null;
    try {
      if (src.kind === 'serial') await src.send('STOP').catch(() => {});
      await src.stop();
    } catch {
      /* ignore */
    }
    const now = Date.now();
    // An intentional disconnect is not a technical fault.
    for (const id of ['noData', 'apnea', 'railCo2', 'railFlow', 'railPneumo', 'adcError', 'dropped', 'checksum'] as const) {
      if (this.alarms.get(id)?.state === 'active') this.alarms.remove(id);
    }
    if (this.fileChannels) {
      this.fileChannels = null;
      this.pipeline.setChannels(this.channels);
    }
    if (!keepError) {
      this.status = 'idle';
      this.statusDetail = reason === 'Disconnected' ? '' : reason;
    }
    this.recorder.event(now, `DISCONNECTED ${reason}`);
    await this.releaseWakeLock();
    this.emit();
  }

  async send(cmd: string): Promise<void> {
    await this.source?.send(cmd);
    this.addLog(`Sent ${cmd}`);
  }

  get sourceInstance(): Source | null {
    return this.source;
  }

  // ---------- data path ----------

  private onLine(line: string): void {
    this.recentLines.push(line);
    if (this.recentLines.length > 40) this.recentLines.splice(0, this.recentLines.length - 40);
    const p = parseLine(line);
    const now = Date.now();
    switch (p.kind) {
      case 'data':
        this.onData(p.seq, p.tMs, p.volts, now);
        break;
      case 'info': {
        this.info = p.fields;
        this.pipeline.setOptions(this.processingOptions());
        this.addLog(`Device: ${p.raw}`);
        break;
      }
      case 'error':
        if (p.code === 'ADC_NOT_FOUND') this.alarms.setCondition('adcError', true, now, 'medium', 'ADC NOT FOUND: check I2C wiring and power');
        this.addLog(`Device error: ${p.code}`, 'error');
        this.recorder.event(now, `DEVICE_ERROR ${p.code}`);
        break;
      case 'ack':
        this.addLog(`Device: ${p.raw}`);
        break;
      case 'bad':
        this.badLines++;
        this.badTimes.push(now);
        break;
      case 'empty':
        break;
    }
  }

  private onData(seq: number, tMs: number, volts: number[], now: number): void {
    this.lastDataWall = now;
    this.seq.push(seq, tMs);
    if (this.alarms.get('adcError')?.state === 'active') this.alarms.setCondition('adcError', false, now, 'medium', '');
    const pr = this.pipeline.process(tMs, volts);
    this.last = pr;
    for (const id of IDS) if (pr.valid[id]) this.lastValidWall[id] = now;

    this.rateCount++;
    if (now - this.rateWindowStart >= 2000) {
      this.measuredRate = (this.rateCount * 1000) / (now - this.rateWindowStart);
      this.rateCount = 0;
      this.rateWindowStart = now;
    }

    const co2Disp = this.settings.co2Unit === '%' ? pr.co2Pct : pr.co2MmHg;
    this.pushRenderers('co2', co2Disp);
    this.pushRenderers('flow', pr.flowLpm);
    this.pushRenderers('pneumo', pr.pneumoLpm);
    if (Number.isFinite(pr.flowLpm)) this.flowPeakCur = Math.max(this.flowPeakCur, pr.flowLpm);

    if (Number.isFinite(pr.co2MmHg)) this.traceMax = Math.max(this.traceMax, pr.co2MmHg);
    if (now - this.traceStart >= 500) {
      if (Number.isFinite(this.traceMax)) {
        this.traceT.push(now / 1000);
        this.traceV.push(this.traceMax);
        if (this.traceT.length > MAX_TRACE) {
          this.traceT.splice(0, 1000);
          this.traceV.splice(0, 1000);
        }
      }
      this.traceMax = -Infinity;
      this.traceStart = now;
    }

    if (pr.breath) this.onBreath(pr.breath, now);
    this.alarms.onLiveCo2(pr.co2MmHg, now);

    this.recorder.sample({ wallMs: now, tMs, seq, volts, co2MmHg: pr.co2MmHg, flowLpm: pr.flowLpm, pneumoLpm: pr.pneumoLpm, pneumoPa: pr.pneumoPa });
    for (const l of this.sampleListeners) l(pr);
  }

  private onBreath(b: Breath, now: number): void {
    this.breaths.push(b);
    if (this.breaths.length > 2000) this.breaths.splice(0, 500);
    this.lastBreathWall = now;
    this.flowPeakLast = this.flowPeakCur;
    this.flowPeakCur = -Infinity;
    const mv = minuteVentilation(this.breaths, b.tEnd);
    const rr = respiratoryRate(this.breaths);
    this.trend.push({ t: now / 1000, etco2: b.etco2, mv, vt: b.vtMl, rr });
    if (this.trend.length > MAX_TREND) this.trend.splice(0, 1000);
    this.trendVersion++;
    this.alarms.onBreath(b.etco2, now);
    const f = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : 'NA');
    this.recorder.event(now, `BREATH etco2_mmHg=${f(b.etco2)} rr=${f(rr)} vt_ml=${f(b.vtMl, 0)} mv_lpm=${f(mv, 2)} by=${b.detectedBy}`);
  }

  private pushRenderers(id: ChannelId, v: number): void {
    const set = this.renderers.get(id);
    if (set) for (const r of set) r.push(v);
  }

  // ---------- periodic checks (4 Hz) ----------

  private tick(): void {
    const now = Date.now();
    const connected = this.status === 'connected' && this.source !== null;
    const fresh = now - this.lastDataWall < 3000;
    const a = this.alarms;

    a.setCondition('noData', connected && !fresh, now, 'medium', `NO DATA FROM DEVICE for ${Math.round((now - this.lastDataWall) / 1000)} s`);
    const railNames: Record<ChannelId, ['railCo2' | 'railFlow' | 'railPneumo', string]> = {
      co2: ['railCo2', 'CO2 SENSOR'],
      flow: ['railFlow', 'FLOW SENSOR'],
      pneumo: ['railPneumo', 'PNEUMOTACH'],
    };
    for (const id of IDS) {
      const [aid, name] = railNames[id];
      a.setCondition(aid, connected && fresh && !!this.last?.stuck[id], now, 'medium', `${name} SIGNAL AT ADC RAIL: check sensor connection`);
    }

    const apneaSec = this.settings.alarms.apneaSec;
    const signal = this.last && (this.last.valid.pneumo || this.last.valid.co2);
    const sinceBreath = (now - this.lastBreathWall) / 1000;
    a.setCondition('apnea', apneaSec > 0 && connected && fresh && !!signal && sinceBreath > apneaSec, now, 'high', `APNEA: no breath for ${Math.floor(sinceBreath)} s`);

    a.setCondition('audioOff', !this.audio.armed, now, 'advisory', 'Audible alarm NOT armed. Press Arm audio.');
    const dr = this.seq.dropRate;
    a.setCondition('dropped', connected && this.seq.received > 500 && dr > 0.001, now, 'advisory', `Dropped samples ${(dr * 100).toFixed(2)} % (${this.seq.dropped})`);
    while (this.badTimes.length && this.badTimes[0] < now - 60000) this.badTimes.shift();
    a.setCondition('checksum', connected && this.badTimes.length > 0, now, 'advisory', `${this.badTimes.length} corrupt line${this.badTimes.length === 1 ? '' : 's'} in the last minute`);

    a.tick(now);

    // Audio: highest active priority, unless silenced.
    const pri = a.activePriority();
    if (pri && !a.isSilenced(now) && this.audio.armed) {
      if (pri === 'high' && now - this.lastHighTone >= 5000) {
        this.audio.high();
        this.lastHighTone = now;
      } else if (pri === 'medium' && now - this.lastMedTone >= 15000) {
        this.audio.medium();
        this.lastMedTone = now;
      }
    }

    // Tab title flashes during a high-priority alarm.
    if (typeof document !== 'undefined') {
      if (pri === 'high') {
        this.titleFlip = !this.titleFlip;
        document.title = this.titleFlip ? '▲ ALARM · CO2-Response' : 'CO2-Response Monitor';
      } else if (document.title !== 'CO2-Response Monitor') document.title = 'CO2-Response Monitor';
    }
    this.emit();
  }

  private onAlarmLog(e: AlarmLogEntry): void {
    const text = `${e.kind.toUpperCase()}${e.id ? ` ${e.id}` : ''}${e.priority ? ` (${e.priority})` : ''}: ${e.message}`;
    this.addLog(text, 'alarm');
    this.recorder.event(e.t, `ALARM_${e.kind.toUpperCase()}${e.id ? ` ${e.id}` : ''}${e.priority ? ` ${e.priority}` : ''} ${e.message}`);
    if (e.kind === 'start' && e.id !== 'test' && (e.priority === 'high' || e.priority === 'medium')) {
      this.alarmStarts.push({ t: e.t, priority: e.priority });
      if (this.alarmStarts.length > 2000) this.alarmStarts.splice(0, 500);
      this.trendVersion++;
    }
    if (e.kind === 'start') {
      // sound the new alarm right away rather than waiting for the repeat interval
      if (e.priority === 'high') this.lastHighTone = 0;
      if (e.priority === 'medium') this.lastMedTone = 0;
      if (e.priority === 'high' && typeof document !== 'undefined' && document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        try {
          new Notification('CO2-Response alarm', { body: e.message, tag: e.id, requireInteraction: true });
        } catch {
          /* notifications unavailable */
        }
      }
    }
  }

  // ---------- operator actions ----------

  async armAudio(): Promise<boolean> {
    const ok = await this.audio.arm();
    if (ok) {
      this.audio.confirm();
      this.addLog('Audio armed');
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        Notification.requestPermission().catch(() => {});
      }
    }
    this.tick();
    return ok;
  }

  acknowledge(id?: AlarmRecord['id']): void {
    this.alarms.acknowledge(Date.now(), id);
    this.emit();
  }

  toggleSilence(): void {
    const now = Date.now();
    if (this.alarms.isSilenced(now)) this.alarms.unsilence(now);
    else this.alarms.silence(now);
    this.emit();
  }

  testAlarm(): void {
    const now = Date.now();
    this.alarms.setCondition('test', true, now, 'high', 'TEST ALARM: confirm you can see and hear this');
    this.lastHighTone = 0;
    this.tick();
    setTimeout(() => {
      if (this.audio.armed) this.audio.medium();
    }, 4000);
    setTimeout(() => {
      this.alarms.setCondition('test', false, Date.now(), 'high', '');
      this.emit();
    }, 8000);
  }

  setFrozen(on: boolean): void {
    this.frozen = on;
    for (const set of this.renderers.values()) for (const r of set) r.frozen = on;
    this.emit();
  }

  addMarker(label?: string): Marker {
    const m = { id: this.markerId++, t: Date.now(), label: label?.trim() || `Event ${this.markerId - 1}` };
    this.markers.push(m);
    this.trendVersion++;
    this.recorder.event(m.t, `MARK ${m.label}`);
    this.addLog(`Marker: ${m.label}`);
    this.emit();
    return m;
  }

  renameMarker(id: number, label: string): void {
    const m = this.markers.find((x) => x.id === id);
    if (!m) return;
    m.label = label.trim() || m.label;
    this.trendVersion++;
    this.recorder.event(Date.now(), `MARK_RENAMED ${id} ${m.label}`);
    this.emit();
  }

  async startRecording(): Promise<boolean> {
    const ok = await this.recorder.start({
      studyCode: this.settings.studyCode,
      started: new Date().toISOString(),
      source: this.source?.label ?? 'none',
      firmware: this.info?.FW ?? 'unknown',
      rateHz: Number(this.info?.RATE) || 100,
      channels: this.effectiveChannels,
      processing: this.settings.processing,
    });
    if (ok) {
      this.addLog(`Recording to ${this.recorder.fileName}`);
      for (const r of this.alarms.list()) if (r.state === 'active' && r.priority !== 'advisory') this.recorder.event(Date.now(), `ALARM_ACTIVE_AT_START ${r.id} ${r.priority} ${r.message}`);
    }
    this.emit();
    return ok;
  }

  async stopRecording(): Promise<void> {
    await this.recorder.stop();
    this.addLog('Recording stopped');
    this.emit();
  }

  // ---------- calibration helpers ----------

  onSample(fn: (p: Processed) => void): () => void {
    this.sampleListeners.add(fn);
    return () => this.sampleListeners.delete(fn);
  }

  /** Average raw volts and calibrated value of one channel over `seconds`. */
  captureAverage(id: ChannelId, seconds = 2): Promise<{ volts: number; value: number; n: number }> {
    const index = this.effectiveChannels.channels[id].index;
    return new Promise((resolve, reject) => {
      let sv = 0;
      let sy = 0;
      let n = 0;
      let ny = 0;
      const off = this.onSample((p) => {
        const v = p.volts[index];
        if (Number.isFinite(v)) {
          sv += v;
          n++;
        }
        const y = id === 'co2' ? p.co2Pct : id === 'flow' ? p.flowLpm : p.pneumoPa;
        if (Number.isFinite(y)) {
          sy += y;
          ny++;
        }
      });
      setTimeout(() => {
        off();
        if (!n) reject(new Error('No data arrived. Connect the device (or the simulator) first.'));
        else resolve({ volts: sv / n, value: ny ? sy / ny : NaN, n });
      }, seconds * 1000);
    });
  }

  // ---------- rendering ----------

  registerRenderer(id: ChannelId, r: SweepRenderer): () => void {
    let set = this.renderers.get(id);
    if (!set) this.renderers.set(id, (set = new Set()));
    set.add(r);
    r.frozen = this.frozen;
    return () => set!.delete(r);
  }

  private startRenderLoop(): void {
    const frame = (ts: number) => {
      if (ts - this.lastFrame >= 33) {
        this.lastFrame = ts;
        for (const set of this.renderers.values()) for (const r of set) r.draw();
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  // ---------- misc ----------

  private addLog(text: string, level: LogLine['level'] = 'info'): void {
    this.log.push({ t: Date.now(), text, level });
    if (this.log.length > 2000) this.log.splice(0, 500);
  }

  private async requestWakeLock(): Promise<void> {
    try {
      const wl = (navigator as unknown as { wakeLock?: { request(type: 'screen'): Promise<{ release(): Promise<void> }> } }).wakeLock;
      if (wl && !this.wakeLock) {
        this.wakeLock = await wl.request('screen');
        (this.wakeLock as unknown as EventTarget).addEventListener?.('release', () => (this.wakeLock = null));
      }
    } catch {
      /* not granted (e.g. tab hidden); retried on visibilitychange */
    }
  }

  private async releaseWakeLock(): Promise<void> {
    try {
      await this.wakeLock?.release();
    } catch {
      /* ignore */
    }
    this.wakeLock = null;
  }

  private buildSnapshot(): Snapshot {
    const now = Date.now();
    const connected = this.status === 'connected';
    const stale = {} as Record<ChannelId, boolean>;
    for (const id of IDS) stale[id] = !connected || now - this.lastValidWall[id] > 2000;
    const lastB = this.breaths[this.breaths.length - 1];
    const lastPeriod = lastB?.periodSec ?? 4;
    const breathStale = !lastB || now - this.lastBreathWall > Math.max(15000, 3000 * lastPeriod) || !connected;
    const L = this.last;
    const nan = NaN;
    const numbers: Numbers = {
      co2Live: stale.co2 || !L ? nan : this.settings.co2Unit === '%' ? L.co2Pct : L.co2MmHg,
      etco2: breathStale || stale.co2 ? nan : lastB.etco2,
      etco2Median: breathStale || stale.co2 ? nan : etco2Median(this.breaths),
      rr: breathStale ? nan : respiratoryRate(this.breaths),
      fico2: breathStale || stale.co2 ? nan : lastB.fico2,
      flow: stale.flow || !L ? nan : L.flowLpm,
      peakFlow: breathStale || stale.flow ? nan : this.flowPeakLast,
      pneumoFlow: stale.pneumo || !L ? nan : L.pneumoLpm,
      pneumoPa: stale.pneumo || !L ? nan : L.pneumoPa,
      vt: breathStale || stale.pneumo ? nan : lastB.vtMl,
      mv: breathStale || stale.pneumo || !L ? nan : minuteVentilation(this.breaths, L.tSec),
      volts: L?.volts ?? [],
    };
    const rec = this.recorder;
    return {
      now,
      status: this.status,
      statusDetail: this.statusDetail,
      sourceKind: this.source?.kind ?? null,
      sourceLabel: this.source?.label ?? '',
      info: this.info,
      rateHz: Number(this.info?.RATE) || 100,
      measuredRateHz: connected ? this.measuredRate : 0,
      received: this.seq.received,
      dropped: this.seq.dropped,
      dropRate: this.seq.dropRate,
      badLines: this.badLines,
      numbers,
      stale,
      breathStale,
      alarms: this.alarms.list(),
      silenceMs: this.alarms.silenceRemainingMs(now),
      audioArmed: this.audio.armed,
      recording: { active: rec.active, startedAt: rec.startedAt, fileName: rec.fileName, mode: rec.mode, bytes: rec.bytes },
      frozen: this.frozen,
      trendVersion: this.trendVersion,
      settings: this.settings,
      channels: this.channels,
      usingFileCalibration: this.fileChannels !== null,
      replayProgress: this.source instanceof ReplaySource ? this.source.progress : null,
    };
  }
}

async function loadDefaultChannels(): Promise<ChannelsConfig> {
  try {
    const r = await fetch('config/channels.json', { cache: 'no-store' });
    if (r.ok) return validateChannels(await r.json());
  } catch {
    /* offline or file missing: use the bundled copy */
  }
  return cloneChannels(DEFAULT_CHANNELS);
}

export const monitor = new MonitorEngine();
