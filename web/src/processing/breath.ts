export interface BreathOptions {
  /** Flow must pass ±deadband (L/min) to count as inspiration/expiration. 0.05 L/s = 3 L/min. */
  deadbandLpm: number;
  /** Breaths shorter than this (s) are treated as noise. */
  minBreathSec: number;
}

export const DEFAULT_BREATH: BreathOptions = { deadbandLpm: 3, minBreathSec: 1 };

export interface Breath {
  /** start of this breath's inspiration (device clock, s) */
  tStart: number;
  /** start of the next inspiration, when the breath was finalized (s) */
  tEnd: number;
  periodSec: number;
  /** peak CO2 in the expiratory phase, mmHg (NaN if no CO2 signal) */
  etco2: number;
  /** minimum CO2 during inspiration, mmHg */
  fico2: number;
  /** inspired volume, mL (NaN in CO2-only mode) */
  vtMl: number;
  /** peak inspiratory flow, L/min (NaN in CO2-only mode) */
  peakInspLpm: number;
  /** which signal found the breath */
  detectedBy: 'flow' | 'co2';
}

type Phase = 'unknown' | 'insp' | 'exp';

/**
 * Breath detection on pneumotach flow, falling back to the capnogram when there is
 * no flow signal. A breath runs from one inspiration start to the next and is
 * reported when the next inspiration begins.
 */
export class BreathDetector {
  private mode: 'flow' | 'co2' | null = null;
  private phase: Phase = 'unknown';
  private start: number | null = null;
  private lastT: number | null = null;
  private lastFlow = 0;
  private vtL = 0;
  private peak = 0;
  private fi = NaN;
  private et = NaN;
  private lastEt = NaN;

  constructor(public opts: BreathOptions = DEFAULT_BREATH) {}

  reset(): void {
    this.mode = null;
    this.phase = 'unknown';
    this.start = null;
    this.lastT = null;
  }

  private beginBreath(t: number): Breath | null {
    let out: Breath | null = null;
    if (this.start !== null && this.mode !== null) {
      out = {
        tStart: this.start,
        tEnd: t,
        periodSec: t - this.start,
        etco2: this.et,
        fico2: this.fi,
        vtMl: this.mode === 'flow' ? this.vtL * 1000 : NaN,
        peakInspLpm: this.mode === 'flow' ? this.peak : NaN,
        detectedBy: this.mode,
      };
      if (Number.isFinite(this.et)) this.lastEt = this.et;
    }
    this.start = t;
    this.phase = 'insp';
    this.vtL = 0;
    this.peak = 0;
    this.fi = NaN;
    this.et = NaN;
    return out;
  }

  /**
   * @param t     device time, s
   * @param flow  pneumotach flow, L/min (NaN when there is no valid flow signal)
   * @param co2   CO2, mmHg (NaN when there is no valid CO2 signal)
   */
  push(t: number, flow: number, co2: number): Breath | null {
    const mode = Number.isFinite(flow) ? 'flow' : Number.isFinite(co2) ? 'co2' : null;
    if (mode !== this.mode) {
      this.reset();
      this.mode = mode;
    }
    if (mode === null) return null;

    const dt = this.lastT === null ? 0 : Math.max(0, Math.min(0.5, t - this.lastT));
    this.lastT = t;
    const longEnough = this.start === null || t - this.start >= this.opts.minBreathSec;
    let breath: Breath | null = null;

    if (mode === 'flow') {
      const db = this.opts.deadbandLpm;
      if (flow > db && this.phase !== 'insp' && longEnough) breath = this.beginBreath(t);
      else if (flow < -db && this.phase === 'insp') this.phase = 'exp';
      if (this.phase === 'insp') {
        this.vtL += ((Math.max(0, flow) + Math.max(0, this.lastFlow)) / 2 / 60) * dt;
        this.peak = Math.max(this.peak, flow);
      }
      this.lastFlow = flow;
    } else {
      const ref = Number.isFinite(this.lastEt) ? this.lastEt : 20;
      const hi = Math.max(4, 0.5 * ref);
      const lo = Math.max(2, 0.3 * ref);
      if (co2 > hi && this.phase !== 'exp') this.phase = 'exp';
      else if (co2 < lo && this.phase === 'exp' && longEnough) breath = this.beginBreath(t);
    }

    if (Number.isFinite(co2)) {
      if (this.phase === 'exp') this.et = Number.isFinite(this.et) ? Math.max(this.et, co2) : co2;
      if (this.phase === 'insp') this.fi = Number.isFinite(this.fi) ? Math.min(this.fi, co2) : co2;
    }
    return breath;
  }
}
