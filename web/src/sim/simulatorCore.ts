import { formatDataLine } from '../protocol/protocol';
import { mulberry32 } from './rng';
import { PhysioModel, type SimParams } from './physiology';

/** Physical value → volts for each channel (the inverse of the channel calibration). */
export interface Encoders {
  co2(pct: number): number;
  flow(lpm: number): number;
  pneumo(pa: number): number;
}

export const FIRMWARE_INFO = 'I,FW,sim-1.0.0,ADC,SIMULATED,CH,3,RATE,';

/**
 * Emulates the firmware: produces protocol lines sample by sample, including the
 * corrupt and missing lines the "noisy" scenario injects.
 */
export class SimulatorCore {
  readonly model: PhysioModel;
  rateHz = 100;
  streaming = true;
  private seq = 0;
  private tMs = 0;
  private rand: () => number;

  constructor(
    private encoders: Encoders,
    params: Partial<SimParams> = {},
    private railLowV = 0,
    private railHighV = 5,
  ) {
    this.model = new PhysioModel(params);
    this.rand = mulberry32((params.seed ?? 1) + 99);
  }

  setEncoders(e: Encoders): void {
    this.encoders = e;
  }

  private clampV(v: number): number {
    return Math.min(this.railHighV, Math.max(this.railLowV, v));
  }

  /** Produce the line(s) for one sample period. May return [] (dropped or stopped). */
  nextSample(): string[] {
    const dt = 1 / this.rateHz;
    const s = this.model.step(dt);
    const seq = this.seq;
    this.seq = (this.seq + 1) & 0xffff;
    this.tMs += 1000 * dt;
    if (!this.streaming || this.model.params.scenario === 'noData') return [];

    const volts = [
      s.co2Pct === null ? this.railLowV : this.clampV(this.encoders.co2(s.co2Pct)),
      this.clampV(this.encoders.flow(s.flowLpm)),
      this.clampV(this.encoders.pneumo(s.pneumoPa)),
    ];
    let line = formatDataLine(seq, this.tMs, volts);

    if (this.model.params.scenario === 'noisy') {
      const r = this.rand();
      if (r < 0.005) return []; // dropped sample → sequence gap
      if (r < 0.015) line = line.slice(0, -2) + (line.endsWith('00') ? '01' : '00'); // bad checksum
    }
    return [line];
  }

  /** Reply to a host command the way the firmware does. */
  command(cmd: string): string[] {
    const [name, arg] = cmd.trim().toUpperCase().split(/\s+/);
    switch (name) {
      case 'INFO':
        return [FIRMWARE_INFO + this.rateHz];
      case 'START':
        this.streaming = true;
        return ['A,START'];
      case 'STOP':
        this.streaming = false;
        return ['A,STOP'];
      case 'RATE': {
        const hz = Number(arg);
        if (!Number.isInteger(hz) || hz < 1 || hz > 200) return ['E,BAD_RATE'];
        this.rateHz = hz;
        return [`A,RATE,${hz}`];
      }
      default:
        return ['E,UNKNOWN_CMD'];
    }
  }
}
