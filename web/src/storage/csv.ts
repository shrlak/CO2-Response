import { validateChannels, type ChannelsConfig } from '../config/channels';

/**
 * Session CSV format (one file per recording):
 *
 *   # CO2-Response session,v1
 *   # <key>,<value>            metadata; value may contain commas (JSON)
 *   wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,flow_lpm,pneumo_lpm,pneumo_pa,event
 *   <sample rows>              event column empty
 *   <event rows>               t_ms/seq/volts empty, event filled
 *
 * Raw volts are always stored, so a session can be replayed or re-processed with a
 * different calibration later. No patient identifiers are written.
 */

export const CSV_COLUMNS = ['wall_time_iso', 't_ms', 'seq', 'v0', 'v1', 'v2', 'co2_mmHg', 'flow_lpm', 'pneumo_lpm', 'pneumo_pa', 'event'] as const;

export interface SessionMeta {
  studyCode: string;
  started: string;
  source: string;
  firmware: string;
  rateHz: number;
  channels: ChannelsConfig;
  processing: unknown;
}

const num = (v: number, d: number) => (Number.isFinite(v) ? v.toFixed(d) : '');

export function csvEscape(s: string): string {
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function formatHeader(m: SessionMeta): string {
  return [
    '# CO2-Response session,v1',
    '# research use only,not for clinical decision-making',
    `# study_code,${m.studyCode.replace(/[\r\n]/g, ' ')}`,
    `# started,${m.started}`,
    `# source,${m.source}`,
    `# firmware,${m.firmware}`,
    `# rate_hz,${m.rateHz}`,
    `# channels,${JSON.stringify(m.channels)}`,
    `# processing,${JSON.stringify(m.processing)}`,
    CSV_COLUMNS.join(','),
  ].join('\n') + '\n';
}

export interface SampleRow {
  wallMs: number;
  tMs: number;
  seq: number;
  volts: readonly number[];
  co2MmHg: number;
  flowLpm: number;
  pneumoLpm: number;
  pneumoPa: number;
}

export function formatSampleRow(r: SampleRow): string {
  const v = r.volts;
  return `${new Date(r.wallMs).toISOString()},${r.tMs},${r.seq},${num(v[0], 4)},${num(v[1], 4)},${num(v[2], 4)},${num(r.co2MmHg, 2)},${num(r.flowLpm, 2)},${num(r.pneumoLpm, 2)},${num(r.pneumoPa, 1)},\n`;
}

export function formatEventRow(wallMs: number, text: string): string {
  return `${new Date(wallMs).toISOString()},,,,,,,,,,${csvEscape(text)}\n`;
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

export interface ParsedSession {
  meta: Record<string, string>;
  channels: ChannelsConfig | null;
  samples: { wallMs: number; tMs: number; seq: number; volts: number[]; co2MmHg: number; flowLpm: number; pneumoLpm: number }[];
  events: { wallMs: number; text: string }[];
}

export function parseSessionCsv(text: string): ParsedSession {
  const meta: Record<string, string> = {};
  const samples: ParsedSession['samples'] = [];
  const events: ParsedSession['events'] = [];
  let header: string[] | null = null;

  for (const raw of text.split(/\r?\n/)) {
    if (!raw) continue;
    if (raw.startsWith('#')) {
      const body = raw.replace(/^#\s?/, '');
      const comma = body.indexOf(',');
      if (comma > 0) meta[body.slice(0, comma)] = body.slice(comma + 1);
      continue;
    }
    if (!header) {
      header = splitCsvLine(raw);
      if (header[1] !== 't_ms' || header[3] !== 'v0') throw new Error('This file is not a CO2-Response session (missing t_ms / v0 columns).');
      continue;
    }
    const f = splitCsvLine(raw);
    const wallMs = Date.parse(f[0]);
    if (f[1] === '') {
      if (f[10]) events.push({ wallMs, text: f[10] });
      continue;
    }
    const n = (s: string) => (s === '' ? NaN : Number(s));
    samples.push({
      wallMs,
      tMs: Number(f[1]),
      seq: Number(f[2]),
      volts: [n(f[3]), n(f[4]), n(f[5])],
      co2MmHg: n(f[6]),
      flowLpm: n(f[7]),
      pneumoLpm: n(f[8]),
    });
  }
  if (!header) throw new Error('This file has no data rows.');

  let channels: ChannelsConfig | null = null;
  if (meta.channels) {
    try {
      channels = validateChannels(JSON.parse(meta.channels));
    } catch {
      channels = null;
    }
  }
  return { meta, channels, samples, events };
}

/** co2r_<study>_<YYYYMMDD-HHMMSS>.csv; the study code is reduced to safe characters. */
export function sessionFileName(studyCode: string, d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const code = studyCode.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'session';
  return `co2r_${code}_${stamp}.csv`;
}
