import type { BreathCells, SessionMeta } from './csv';
import { formatHeader, formatRow, sessionFileName } from './csv';
import { newId, type SessionLibrary, type SessionRecord } from './library';

export interface RecorderSample {
  wallMs: number;
  tMs: number;
  seq: number;
  volts: readonly number[];
  co2MmHg: number;
  co2Pct: number;
  flowLpm: number;
  pneumoPa: number;
  pneumoLpm: number;
}

/**
 * Writes one session file. Rows are buffered and written to the session library
 * (IndexedDB) once a second, so the recording survives a crashed or closed tab.
 * On stop the whole session is available as a single CSV.
 */
export class SessionRecorder {
  active = false;
  startedAt = 0;
  fileName = '';
  rows = 0;
  breaths = 0;
  alarms = 0;
  markers = 0;
  rec: SessionRecord | null = null;
  /** set when the library is unavailable: the file is kept in memory instead */
  private memory: string[] | null = null;
  private buf: string[] = [];
  private bufBytes = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private writing: Promise<void> = Promise.resolve();
  private anchor: { devT0: number; wall0: number } | null = null;
  private lastT = -Infinity;
  private pendingBreath: BreathCells | null = null;
  private pendingEvents: string[] = [];
  private pendingSince = 0;
  writeError: string | null = null;

  constructor(private lib: SessionLibrary) {}

  get bytes(): number {
    return (this.rec?.bytes ?? 0) + this.bufBytes + (this.memory?.reduce((a, s) => a + s.length, 0) ?? 0);
  }

  async start(meta: Omit<SessionMeta, 'startedMs'>, startedMs = Date.now()): Promise<SessionRecord | null> {
    if (this.active) return this.rec;
    this.startedAt = startedMs;
    this.fileName = sessionFileName(meta.studyCode, new Date(startedMs));
    this.rows = this.breaths = this.alarms = this.markers = 0;
    this.anchor = null;
    this.lastT = -Infinity;
    this.pendingBreath = null;
    this.pendingEvents = [];
    this.buf = [];
    this.bufBytes = 0;
    this.writeError = null;
    this.memory = null;
    this.rec = {
      id: newId(),
      fileName: this.fileName,
      studyCode: meta.studyCode,
      startedAt: startedMs,
      endedAt: null,
      status: 'recording',
      source: meta.source,
      bytes: 0,
      chunks: 0,
      lastWriteAt: Date.now(),
      stats: {},
    };
    const header = formatHeader({ ...meta, startedMs });
    try {
      await this.lib.put(this.rec);
      await this.lib.appendChunk(this.rec, header);
    } catch (e) {
      // no IndexedDB (or it is blocked): keep the file in memory instead
      this.writeError = (e as Error).message;
      this.rec = null;
      this.memory = [header];
    }
    this.active = true;
    this.timer = setInterval(() => this.flush(), 1000);
    return this.rec;
  }

  private add(s: string): void {
    this.buf.push(s);
    this.bufBytes += s.length;
  }

  /** Breath completed on the sample about to be recorded. */
  breath(b: Omit<BreathCells, 'n'>): void {
    if (!this.active) return;
    this.breaths++;
    this.pendingBreath = { ...b, n: this.breaths };
  }

  event(wallMs: number, text: string): void {
    if (!this.active) return;
    if (text.startsWith('ALARM_START') && !text.includes(' test ')) this.alarms++;
    if (text.startsWith('MARK ')) this.markers++;
    if (!this.pendingEvents.length) this.pendingSince = wallMs;
    this.pendingEvents.push(text);
  }

  sample(s: RecorderSample): void {
    if (!this.active) return;
    if (!this.anchor || s.tMs < this.lastT) this.anchor = { devT0: s.tMs, wall0: s.wallMs };
    this.lastT = s.tMs;
    const wall = this.anchor.wall0 + (s.tMs - this.anchor.devT0);
    this.add(
      formatRow({
        timeS: (wall - this.startedAt) / 1000,
        wallMs: wall,
        tMs: s.tMs,
        seq: s.seq,
        volts: s.volts,
        co2MmHg: s.co2MmHg,
        co2Pct: s.co2Pct,
        flowLpm: s.flowLpm,
        pneumoPa: s.pneumoPa,
        pneumoLpm: s.pneumoLpm,
        breath: this.pendingBreath,
        events: this.pendingEvents,
      }),
    );
    this.rows++;
    this.pendingBreath = null;
    this.pendingEvents = [];
  }

  /** Events with no sample to ride on (e.g. while disconnected) get their own row. */
  private flushLoneEvents(force: boolean): void {
    if (this.pendingEvents.length && (force || Date.now() - this.pendingSince > 1000)) {
      this.add(formatRow({ timeS: (this.pendingSince - this.startedAt) / 1000, wallMs: this.pendingSince, events: this.pendingEvents }));
      this.pendingEvents = [];
    }
  }

  private flush(force = false): Promise<void> {
    this.flushLoneEvents(force);
    if (!this.buf.length) return this.writing;
    const text = this.buf.join('');
    this.buf = [];
    this.bufBytes = 0;
    if (this.memory) {
      this.memory.push(text);
      return this.writing;
    }
    const rec = this.rec!;
    this.writing = this.writing
      .then(() => this.lib.appendChunk(rec, text))
      .catch((e: Error) => {
        // storage full or blocked: keep everything from here on in memory
        this.writeError = e.message;
        this.memory ??= [];
        this.memory.push(text);
      });
    return this.writing;
  }

  /** Finish the file. Returns the library record, or an in-memory Blob if storage failed. */
  async stop(): Promise<{ rec: SessionRecord | null; memoryBlob: Blob | null }> {
    if (!this.active) return { rec: this.rec, memoryBlob: null };
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.flush(true);
    this.active = false;
    const rec = this.rec;
    if (rec) {
      rec.status = 'complete';
      rec.endedAt = Date.now();
      rec.stats = { durationS: (rec.endedAt - this.startedAt) / 1000, samples: this.rows, breaths: this.breaths, alarms: this.alarms, markers: this.markers };
      await this.lib.put(rec).catch(() => {});
    }
    if (this.memory) {
      // storage failed part-way: hand back the whole file from storage + memory
      const head = rec ? await this.lib.blob(rec.id).catch(() => null) : null;
      const blob = new Blob([...(head ? [head] : []), ...this.memory], { type: 'text/csv' });
      this.memory = null;
      return { rec, memoryBlob: blob };
    }
    return { rec, memoryBlob: null };
  }
}
