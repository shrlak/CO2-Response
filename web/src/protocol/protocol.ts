/** Serial protocol shared with the firmware. Spec: docs/protocol.md */

export type ParsedLine =
  | { kind: 'data'; seq: number; tMs: number; volts: number[] }
  | { kind: 'info'; fields: Record<string, string>; raw: string }
  | { kind: 'error'; code: string; raw: string }
  | { kind: 'ack'; fields: string[]; raw: string }
  | { kind: 'bad'; reason: 'checksum' | 'format'; raw: string }
  | { kind: 'empty' };

/** XOR of every character code in `payload`, as 2 upper-case hex digits. */
export function xorChecksum(payload: string): string {
  let x = 0;
  for (let i = 0; i < payload.length; i++) x ^= payload.charCodeAt(i) & 0xff;
  return x.toString(16).toUpperCase().padStart(2, '0');
}

/** Build a data line exactly as the firmware does (without the trailing newline). */
export function formatDataLine(seq: number, tMs: number, volts: readonly number[]): string {
  const payload = `,${seq & 0xffff},${Math.round(tMs) >>> 0},${volts.map((v) => v.toFixed(4)).join(',')}`;
  return `D${payload}*${xorChecksum(payload)}`;
}

const NUM = /^-?\d+(\.\d+)?$/;

export function parseLine(input: string): ParsedLine {
  const line = input.replace(/\r$/, '').trim();
  if (!line) return { kind: 'empty' };
  const head = line[0];

  if (head === 'D') {
    const star = line.lastIndexOf('*');
    if (star < 0 || line.length - star - 1 !== 2) return { kind: 'bad', reason: 'checksum', raw: line };
    const payload = line.slice(1, star);
    const given = line.slice(star + 1).toUpperCase();
    if (given !== xorChecksum(payload)) return { kind: 'bad', reason: 'checksum', raw: line };
    const parts = payload.split(',');
    // parts[0] is '' (payload starts with a comma)
    if (parts[0] !== '' || parts.length < 4) return { kind: 'bad', reason: 'format', raw: line };
    const [, seqS, tS, ...vs] = parts;
    if (!/^\d+$/.test(seqS) || !/^\d+$/.test(tS) || !vs.every((v) => NUM.test(v))) {
      return { kind: 'bad', reason: 'format', raw: line };
    }
    const seq = Number(seqS);
    if (seq > 0xffff) return { kind: 'bad', reason: 'format', raw: line };
    return { kind: 'data', seq, tMs: Number(tS), volts: vs.map(Number) };
  }

  if (line.startsWith('I,')) {
    const items = line.slice(2).split(',');
    const fields: Record<string, string> = {};
    for (let i = 0; i + 1 < items.length; i += 2) fields[items[i].toUpperCase()] = items[i + 1];
    return { kind: 'info', fields, raw: line };
  }
  if (line.startsWith('E,')) return { kind: 'error', code: line.slice(2).split(',')[0], raw: line };
  if (line.startsWith('A,')) return { kind: 'ack', fields: line.slice(2).split(','), raw: line };
  return { kind: 'bad', reason: 'format', raw: line };
}

/**
 * Counts dropped samples from gaps in the 16-bit rolling sequence number.
 * The device clock going backwards, or a jump of more than half the range,
 * is treated as a device reset, not a loss.
 */
export class SeqTracker {
  received = 0;
  dropped = 0;
  private last: number | null = null;
  private lastT: number | null = null;

  /** Returns the number of samples missing before this one. */
  push(seq: number, tMs?: number): number {
    this.received++;
    const reset = tMs !== undefined && this.lastT !== null && tMs < this.lastT;
    if (tMs !== undefined) this.lastT = tMs;
    if (this.last === null || reset) {
      this.last = seq;
      return 0;
    }
    const gap = (seq - this.last - 1 + 0x10000) & 0xffff;
    this.last = seq;
    if (gap === 0xffff) return 0; // duplicate
    if (gap > 0x8000) return 0; // reset or out of order
    this.dropped += gap;
    return gap;
  }

  /** Fraction of expected samples that never arrived. */
  get dropRate(): number {
    const expected = this.received + this.dropped;
    return expected ? this.dropped / expected : 0;
  }

  reset(): void {
    this.received = 0;
    this.dropped = 0;
    this.last = null;
    this.lastT = null;
  }
}

/** Splits a stream of text chunks into complete lines. */
export class LineSplitter {
  private buf = '';
  push(chunk: string, onLine: (line: string) => void): void {
    this.buf += chunk;
    let i: number;
    while ((i = this.buf.indexOf('\n')) >= 0) {
      onLine(this.buf.slice(0, i));
      this.buf = this.buf.slice(i + 1);
    }
    if (this.buf.length > 4096) this.buf = ''; // garbage with no newline
  }
}
