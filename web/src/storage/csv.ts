import { validateChannels, type ChannelsConfig } from '../config/channels';
import { SeqTracker } from '../protocol/protocol';

/**
 * Session file format v2: ONE self-contained CSV per recording, built for analysis.
 *
 *   # CO2-Response session,v2            metadata lines start with '#'
 *   # <key>,<value>                       (value may contain commas, e.g. JSON)
 *   time_s,wall_time_iso,t_ms,seq,v0,…    header row
 *   <one row per sample>                  every row has the same columns
 *
 * - Breath metrics (breath … peak_insp_lpm) are filled only on the sample row where
 *   that breath was completed; they are empty elsewhere.
 * - `event` holds alarms, markers, connections etc. on the sample row where they
 *   happened (several are joined with " | "). Events while no data was flowing get
 *   a row of their own with only time_s, wall_time_iso and event.
 * - time_s is seconds since the recording started, from the device clock (anchored
 *   to the computer clock at the first sample of each connection), so sample spacing
 *   is exact. wall_time_iso is the same instant on the computer clock.
 *
 * pandas:  pd.read_csv(path, comment='#')     MATLAB: readtable(path, 'CommentStyle', '#')
 * Free text never contains '#' (it is replaced), so comment='#' is safe on every row.
 *
 * v1 files (the first format, with breaths as event rows) still import.
 */

export const FORMAT_VERSION = 2;

export const COLUMNS = [
  'time_s',
  'wall_time_iso',
  't_ms',
  'seq',
  'v0',
  'v1',
  'v2',
  'co2_mmHg',
  'co2_pct',
  'flow_lpm',
  'pneumo_pa',
  'pneumo_lpm',
  'breath',
  'etco2_mmHg',
  'fico2_mmHg',
  'rr_bpm',
  'vt_ml',
  'mv_lpm',
  'peak_insp_lpm',
  'event',
] as const;

export interface SessionMeta {
  studyCode: string;
  startedMs: number;
  appVersion: string;
  source: string;
  firmware: string;
  rateHz: number;
  channels: ChannelsConfig;
  processing: unknown;
  alarmSettings?: unknown;
}

export interface BreathCells {
  n: number;
  etco2: number;
  fico2: number;
  rr: number;
  vt: number;
  mv: number;
  peak: number;
}

export interface RowIn {
  timeS: number;
  wallMs: number;
  tMs?: number;
  seq?: number;
  volts?: readonly number[];
  co2MmHg?: number;
  co2Pct?: number;
  flowLpm?: number;
  pneumoPa?: number;
  pneumoLpm?: number;
  breath?: BreathCells | null;
  events?: readonly string[];
}

const num = (v: number | undefined, d: number) => {
  if (v === undefined || !Number.isFinite(v)) return '';
  const s = v.toFixed(d);
  return /^-0(\.0*)?$/.test(s) ? s.slice(1) : s; // no "-0.0"
};

export function csvEscape(s: string): string {
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Free text in a cell: one line, no '#' (so comment='#' readers never cut a row). */
export function cleanText(s: string): string {
  return s.replace(/[\r\n]+/g, ' ').replace(/#/g, 'no.').trim();
}

export function formatHeader(m: SessionMeta): string {
  const lines = [
    `# CO2-Response session,v${FORMAT_VERSION}`,
    '# research use only,not for clinical decision-making',
    `# study_code,${cleanText(m.studyCode)}`,
    `# started,${new Date(m.startedMs).toISOString()}`,
    `# app_version,${m.appVersion}`,
    `# source,${cleanText(m.source)}`,
    `# firmware,${cleanText(m.firmware)}`,
    `# rate_hz,${m.rateHz}`,
    '# time_base,time_s = seconds since start from the device clock; wall_time_iso = same instant on the computer clock',
    '# units,volts V; co2 mmHg and %; flow L/min; pneumo_pa Pa; vt mL; mv L/min; rr breaths/min',
    `# channels,${JSON.stringify(m.channels).replace(/#/g, 'no.')}`,
    `# processing,${JSON.stringify(m.processing)}`,
  ];
  if (m.alarmSettings !== undefined) lines.push(`# alarm_settings,${JSON.stringify(m.alarmSettings)}`);
  lines.push(COLUMNS.join(','));
  return lines.join('\n') + '\n';
}

export function formatRow(r: RowIn): string {
  const v = r.volts;
  const b = r.breath;
  const ev = r.events?.length ? csvEscape(r.events.map(cleanText).join(' | ')) : '';
  return [
    r.timeS.toFixed(3),
    new Date(r.wallMs).toISOString(),
    r.tMs !== undefined ? String(Math.round(r.tMs)) : '',
    r.seq !== undefined ? String(r.seq) : '',
    num(v?.[0], 4),
    num(v?.[1], 4),
    num(v?.[2], 4),
    num(r.co2MmHg, 2),
    num(r.co2Pct, 3),
    num(r.flowLpm, 2),
    num(r.pneumoPa, 1),
    num(r.pneumoLpm, 2),
    b ? String(b.n) : '',
    num(b?.etco2, 1),
    num(b?.fico2, 1),
    num(b?.rr, 1),
    num(b?.vt, 0),
    num(b?.mv, 2),
    num(b?.peak, 1),
    ev,
  ].join(',') + '\n';
}

/** Split one CSV line, honoring double-quoted fields. */
export function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') {
      out.push(cur);
      cur = '';
    } else cur += c;
  }
  out.push(cur);
  return out;
}

// ------------------------------------------------------------------ parsing

export interface BreathRec {
  timeS: number;
  n: number;
  etco2: number;
  fico2: number;
  rr: number;
  vt: number;
  mv: number;
  peak: number;
}

export interface EventRec {
  timeS: number;
  text: string;
}

export interface ParsedSession {
  version: number;
  meta: Record<string, string>;
  channels: ChannelsConfig | null;
  studyCode: string;
  startedMs: number;
  /** number of sample rows */
  n: number;
  timeS: Float64Array;
  tMs: Float64Array;
  seq: Int32Array;
  v0: Float64Array;
  v1: Float64Array;
  v2: Float64Array;
  co2MmHg: Float32Array;
  co2Pct: Float32Array;
  flowLpm: Float32Array;
  pneumoPa: Float32Array;
  pneumoLpm: Float32Array;
  breaths: BreathRec[];
  events: EventRec[];
  durationS: number;
}

class Col<T extends Float64Array | Float32Array | Int32Array> {
  n = 0;
  constructor(public a: T) {}
  push(v: number) {
    if (this.n === this.a.length) {
      const b = new (this.a.constructor as { new (n: number): T })(this.a.length * 2);
      b.set(this.a);
      this.a = b;
    }
    this.a[this.n++] = v;
  }
  done(): T {
    return this.a.slice(0, this.n) as T;
  }
}

const f64 = () => new Col(new Float64Array(4096));
const f32 = () => new Col(new Float32Array(4096));

/** Pull numbers out of a v1 "BREATH etco2_mmHg=38.2 rr=15.0 …" event. */
function parseV1Breath(text: string, timeS: number, n: number): BreathRec {
  const kv: Record<string, number> = {};
  for (const m of text.matchAll(/(\w+)=([-\d.]+|NA)/g)) kv[m[1]] = m[2] === 'NA' ? NaN : Number(m[2]);
  return { timeS, n, etco2: kv.etco2_mmHg ?? NaN, fico2: NaN, rr: kv.rr ?? NaN, vt: kv.vt_ml ?? NaN, mv: kv.mv_lpm ?? NaN, peak: NaN };
}

/**
 * Streaming parser: push text in any size pieces (lines may be split across pieces),
 * then call finish(). Handles v1 and v2 session files.
 */
export class SessionParser {
  meta: Record<string, string> = {};
  private rest = '';
  private col: Record<string, number> | null = null;
  private version = 0;
  private startedMs = NaN;
  private timeS = f64();
  private tMs = f64();
  private seq = new Col(new Int32Array(4096));
  private v0 = f64();
  private v1 = f64();
  private v2 = f64();
  private co2 = f32();
  private co2Pct = f32();
  private flow = f32();
  private pPa = f32();
  private pL = f32();
  private breaths: BreathRec[] = [];
  private events: EventRec[] = [];
  private lineNo = 0;
  private lastTime = 0;

  push(text: string): void {
    const s = this.rest + text;
    let start = 0;
    let i: number;
    while ((i = s.indexOf('\n', start)) >= 0) {
      this.line(s.slice(start, s[i - 1] === '\r' ? i - 1 : i));
      start = i + 1;
    }
    this.rest = s.slice(start);
  }

  private line(raw: string): void {
    this.lineNo++;
    if (!raw) return;
    if (raw[0] === '#') {
      const body = raw.replace(/^#\s?/, '');
      const comma = body.indexOf(',');
      if (comma > 0) this.meta[body.slice(0, comma)] = body.slice(comma + 1);
      return;
    }
    if (!this.col) {
      const names = splitCsvLine(raw).map((x) => x.trim());
      const col: Record<string, number> = {};
      names.forEach((nm, i) => (col[nm] = i));
      if (col.t_ms === undefined || col.v0 === undefined) throw new Error('This is not a CO2-Response session file (no t_ms / v0 columns).');
      this.col = col;
      this.version = col.time_s !== undefined ? 2 : 1;
      const m = /v(\d+)/.exec(this.meta['CO2-Response session'] ?? '');
      if (m) this.version = Number(m[1]);
      this.startedMs = Date.parse(this.meta.started ?? '');
      return;
    }
    const c = this.col;
    const f = raw.includes('"') ? splitCsvLine(raw) : raw.split(',');
    const get = (k: string) => (c[k] === undefined ? '' : (f[c[k]] ?? ''));
    const n = (k: string) => {
      const s = get(k);
      return s === '' ? NaN : Number(s);
    };
    let t: number;
    if (this.version >= 2) t = n('time_s');
    else {
      const w = Date.parse(get('wall_time_iso'));
      if (!Number.isFinite(this.startedMs)) this.startedMs = w;
      t = (w - this.startedMs) / 1000;
    }
    if (!Number.isFinite(t)) t = this.lastTime;
    this.lastTime = t;

    const ev = get('event');
    if (get('t_ms') !== '') {
      this.timeS.push(t);
      this.tMs.push(n('t_ms'));
      this.seq.push(n('seq'));
      this.v0.push(n('v0'));
      this.v1.push(n('v1'));
      this.v2.push(n('v2'));
      this.co2.push(n('co2_mmHg'));
      this.co2Pct.push(n('co2_pct'));
      this.flow.push(n('flow_lpm'));
      this.pPa.push(n('pneumo_pa'));
      this.pL.push(n('pneumo_lpm'));
      if (get('breath') !== '') {
        this.breaths.push({ timeS: t, n: n('breath'), etco2: n('etco2_mmHg'), fico2: n('fico2_mmHg'), rr: n('rr_bpm'), vt: n('vt_ml'), mv: n('mv_lpm'), peak: n('peak_insp_lpm') });
      }
    }
    if (ev) {
      for (const text of ev.split(' | ')) {
        if (this.version < 2 && text.startsWith('BREATH ')) this.breaths.push(parseV1Breath(text, t, this.breaths.length + 1));
        else this.events.push({ timeS: t, text });
      }
    }
  }

  finish(): ParsedSession {
    if (this.rest) {
      this.line(this.rest);
      this.rest = '';
    }
    if (!this.col) throw new Error('This file has no data header. Is it a CO2-Response session file?');
    let channels: ChannelsConfig | null = null;
    if (this.meta.channels) {
      try {
        channels = validateChannels(JSON.parse(this.meta.channels));
      } catch {
        channels = null;
      }
    }
    const timeS = this.timeS.done();
    const lastEvent = this.events.at(-1)?.timeS ?? 0;
    return {
      version: this.version,
      meta: this.meta,
      channels,
      studyCode: this.meta.study_code ?? '',
      startedMs: Number.isFinite(this.startedMs) ? this.startedMs : 0,
      n: timeS.length,
      timeS,
      tMs: this.tMs.done(),
      seq: this.seq.done(),
      v0: this.v0.done(),
      v1: this.v1.done(),
      v2: this.v2.done(),
      co2MmHg: this.co2.done(),
      co2Pct: this.co2Pct.done(),
      flowLpm: this.flow.done(),
      pneumoPa: this.pPa.done(),
      pneumoLpm: this.pL.done(),
      breaths: this.breaths,
      events: this.events,
      durationS: Math.max(timeS.length ? timeS[timeS.length - 1] : 0, lastEvent),
    };
  }
}

export function parseSessionCsv(text: string): ParsedSession {
  const p = new SessionParser();
  p.push(text);
  return p.finish();
}

// ------------------------------------------------------------------ exports

export interface Range {
  t0: number;
  t1: number;
}

const inRange = (t: number, r?: Range | null) => !r || (t >= r.t0 && t <= r.t1);

/**
 * Rebuild a v2 session file from parsed data (optionally only a time range).
 * Also converts v1 files to v2. Returns string parts to wrap in a Blob.
 */
export function exportSessionParts(s: ParsedSession, range?: Range | null, appVersion = 'unknown'): string[] {
  const parts: string[] = [];
  const wall = (t: number) => s.startedMs + t * 1000;
  const header = formatHeader({
    studyCode: s.studyCode,
    startedMs: s.startedMs,
    appVersion,
    source: s.meta.source ?? '',
    firmware: s.meta.firmware ?? '',
    rateHz: Number(s.meta.rate_hz) || 100,
    channels: s.channels ?? (JSON.parse('{}') as ChannelsConfig),
    processing: safeJson(s.meta.processing),
    alarmSettings: s.meta.alarm_settings ? safeJson(s.meta.alarm_settings) : undefined,
  });
  let head = header;
  if (range) head = head.replace(/\n(?=time_s,)/, `\n# exported_range_s,${range.t0.toFixed(3)}-${range.t1.toFixed(3)}\n`);
  if (s.version < 2) head = head.replace(/\n(?=time_s,)/, `\n# converted_from,v${s.version}\n`);
  parts.push(head);

  let bi = 0;
  let ei = 0;
  let buf = '';
  const flushEvents = (upTo: number) => {
    // events before the next sample that will not attach to it get their own row
    while (ei < s.events.length && s.events[ei].timeS < upTo) {
      const e = s.events[ei++];
      if (inRange(e.timeS, range)) buf += formatRow({ timeS: e.timeS, wallMs: wall(e.timeS), events: [e.text] });
    }
  };
  for (let i = 0; i < s.n; i++) {
    const t = s.timeS[i];
    flushEvents(t - 1e-9);
    const evs: string[] = [];
    while (ei < s.events.length && Math.abs(s.events[ei].timeS - t) < 1e-9) evs.push(s.events[ei++].text);
    let breath: BreathCells | null = null;
    while (bi < s.breaths.length && s.breaths[bi].timeS <= t + 1e-9) {
      const b = s.breaths[bi++];
      breath = { n: b.n, etco2: b.etco2, fico2: b.fico2, rr: b.rr, vt: b.vt, mv: b.mv, peak: b.peak };
    }
    if (!inRange(t, range)) continue;
    buf += formatRow({
      timeS: t,
      wallMs: wall(t),
      tMs: s.tMs[i],
      seq: s.seq[i],
      volts: [s.v0[i], s.v1[i], s.v2[i]],
      co2MmHg: s.co2MmHg[i],
      co2Pct: s.co2Pct[i],
      flowLpm: s.flowLpm[i],
      pneumoPa: s.pneumoPa[i],
      pneumoLpm: s.pneumoLpm[i],
      breath,
      events: evs,
    });
    if (buf.length > 1 << 20) {
      parts.push(buf);
      buf = '';
    }
  }
  flushEvents(Infinity);
  parts.push(buf);
  return parts;
}

function safeJson(s: string | undefined): unknown {
  try {
    return s ? JSON.parse(s) : {};
  } catch {
    return s;
  }
}

const iso = (s: ParsedSession, t: number) => new Date(s.startedMs + t * 1000).toISOString();

/** One row per breath: the table most analyses start from. */
export function breathsCsv(s: ParsedSession, range?: Range | null): string {
  const rows = ['breath,time_s,wall_time_iso,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm'];
  for (const b of s.breaths) {
    if (!inRange(b.timeS, range)) continue;
    rows.push([b.n, b.timeS.toFixed(3), iso(s, b.timeS), num(b.etco2, 1), num(b.fico2, 1), num(b.rr, 1), num(b.vt, 0), num(b.mv, 2), num(b.peak, 1)].join(','));
  }
  return rows.join('\n') + '\n';
}

export function eventType(text: string): string {
  const m = /^([A-Z_]+)/.exec(text);
  return m ? m[1] : 'NOTE';
}

/** Alarms, markers, connections and calibration changes. */
export function eventsCsv(s: ParsedSession, range?: Range | null): string {
  const rows = ['time_s,wall_time_iso,type,event'];
  for (const e of s.events) if (inRange(e.timeS, range)) rows.push([e.timeS.toFixed(3), iso(s, e.timeS), eventType(e.text), csvEscape(e.text)].join(','));
  return rows.join('\n') + '\n';
}

export interface SessionSummary {
  durationS: number;
  samples: number;
  breaths: number;
  alarms: number;
  markers: number;
  dropped: number;
  etco2: { mean: number; min: number; max: number };
  rr: number;
  vt: number;
  mv: number;
  rateHz: number;
}

const mean = (xs: number[]) => {
  const v = xs.filter(Number.isFinite);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN;
};

export function summarize(s: ParsedSession): SessionSummary {
  const seq = new SeqTracker();
  for (let i = 0; i < s.n; i++) if (Number.isFinite(s.seq[i])) seq.push(s.seq[i], s.tMs[i]);
  const et = s.breaths.map((b) => b.etco2).filter(Number.isFinite);
  const span = s.n > 1 ? s.tMs[s.n - 1] - s.tMs[0] : 0;
  return {
    durationS: s.durationS,
    samples: s.n,
    breaths: s.breaths.length,
    alarms: s.events.filter((e) => e.text.startsWith('ALARM_START') && !e.text.includes(' test ')).length,
    markers: s.events.filter((e) => e.text.startsWith('MARK ')).length,
    dropped: seq.dropped,
    etco2: { mean: mean(et), min: et.length ? Math.min(...et) : NaN, max: et.length ? Math.max(...et) : NaN },
    rr: mean(s.breaths.map((b) => b.rr)),
    vt: mean(s.breaths.map((b) => b.vt)),
    mv: mean(s.breaths.map((b) => b.mv)),
    rateHz: span > 0 ? Math.round(((s.n - 1) * 1000) / span) : 0,
  };
}

/** co2r_<study>_<YYYYMMDD-HHMMSS>.csv; the study code is reduced to safe characters. */
export function sessionFileName(studyCode: string, d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const code = studyCode.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'session';
  return `co2r_${code}_${stamp}.csv`;
}
