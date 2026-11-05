import { describe, expect, it } from 'vitest';
import { formatDataLine, LineSplitter, parseLine, SeqTracker, xorChecksum } from '../src/protocol/protocol';

describe('checksum', () => {
  it('XORs every byte between D and *', () => {
    const payload = ',1,10,1.0000,2.0000,3.0000';
    let x = 0;
    for (const ch of payload) x ^= ch.charCodeAt(0);
    expect(xorChecksum(payload)).toBe(x.toString(16).toUpperCase().padStart(2, '0'));
    expect(formatDataLine(1, 10, [1, 2, 3])).toBe(`D${payload}*${xorChecksum(payload)}`);
  });
});

describe('parseLine', () => {
  it('parses a valid data line', () => {
    const line = formatDataLine(1042, 10420, [2.6316, 2.5, 2.5]);
    expect(parseLine(line)).toEqual({ kind: 'data', seq: 1042, tMs: 10420, volts: [2.6316, 2.5, 2.5] });
  });

  it('accepts CRLF endings and lower-case checksums', () => {
    const line = formatDataLine(7, 70, [0.1, 0.2, 0.3]);
    const lower = line.slice(0, -2) + line.slice(-2).toLowerCase();
    expect(parseLine(lower + '\r').kind).toBe('data');
  });

  it('rejects a bad checksum', () => {
    const line = formatDataLine(1, 10, [1, 2, 3]);
    const bad = line.slice(0, -2) + (line.endsWith('00') ? '01' : '00');
    expect(parseLine(bad)).toMatchObject({ kind: 'bad', reason: 'checksum' });
  });

  it('rejects a missing checksum', () => {
    expect(parseLine('D,1,10,1.0,2.0,3.0')).toMatchObject({ kind: 'bad', reason: 'checksum' });
  });

  it('rejects a corrupted field even when the checksum matches', () => {
    const payload = ',1,10,1.0x00,2.0000,3.0000';
    expect(parseLine(`D${payload}*${xorChecksum(payload)}`)).toMatchObject({ kind: 'bad', reason: 'format' });
  });

  it('rejects a sequence number above 16 bits', () => {
    const payload = ',70000,10,1.0000';
    expect(parseLine(`D${payload}*${xorChecksum(payload)}`)).toMatchObject({ kind: 'bad', reason: 'format' });
  });

  it('parses info, error and ack lines', () => {
    expect(parseLine('I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100')).toMatchObject({
      kind: 'info',
      fields: { FW: '1.0.0', ADC: 'ADS1115', CH: '3', RATE: '100' },
    });
    expect(parseLine('E,ADC_NOT_FOUND')).toMatchObject({ kind: 'error', code: 'ADC_NOT_FOUND' });
    expect(parseLine('A,RATE,100')).toMatchObject({ kind: 'ack', fields: ['RATE', '100'] });
  });

  it('treats blank lines as empty and junk as bad', () => {
    expect(parseLine('   ').kind).toBe('empty');
    expect(parseLine('hello')).toMatchObject({ kind: 'bad', reason: 'format' });
  });
});

describe('SeqTracker', () => {
  it('counts dropped samples from gaps', () => {
    const s = new SeqTracker();
    for (const q of [1, 2, 3, 6, 7, 10]) s.push(q);
    expect(s.dropped).toBe(4);
    expect(s.received).toBe(6);
    expect(s.dropRate).toBeCloseTo(4 / 10);
  });

  it('handles 16-bit wraparound', () => {
    const s = new SeqTracker();
    for (const q of [65534, 65535, 0, 1, 3]) s.push(q);
    expect(s.dropped).toBe(1);
  });

  it('does not count a device reset (clock going backwards) as a loss', () => {
    const s = new SeqTracker();
    s.push(40000, 400000);
    s.push(40001, 400010);
    s.push(0, 0);
    s.push(1, 10);
    expect(s.dropped).toBe(0);
  });

  it('ignores duplicates and out-of-order samples', () => {
    const s = new SeqTracker();
    for (const q of [10, 11, 11, 5, 6]) s.push(q);
    expect(s.dropped).toBe(0);
  });
});

describe('LineSplitter', () => {
  it('reassembles lines split across chunks', () => {
    const out: string[] = [];
    const sp = new LineSplitter();
    sp.push('D,1,1', (l) => out.push(l));
    sp.push('0,1*AA\nI,FW', (l) => out.push(l));
    sp.push(',1\n', (l) => out.push(l));
    expect(out).toEqual(['D,1,10,1*AA', 'I,FW,1']);
  });
});
