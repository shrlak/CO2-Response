import { mulberry32 } from './rng';

export type Scenario = 'normal' | 'co2Ramp' | 'apnea' | 'co2Disconnect' | 'noData' | 'noisy';

export const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: 'normal', label: 'Normal breathing' },
  { id: 'co2Ramp', label: 'CO2 ramp (rebreathing)' },
  { id: 'apnea', label: 'Apnea' },
  { id: 'co2Disconnect', label: 'CO2 sensor disconnected' },
  { id: 'noData', label: 'Device stops sending' },
  { id: 'noisy', label: 'Noisy signal + line errors' },
];

export interface SimParams {
  /** breaths/min */
  rr: number;
  /** baseline end-tidal CO2, mmHg */
  etco2: number;
  /** baseline tidal volume, mL */
  vtMl: number;
  /** inspiratory fraction of the breath (1:2 I:E = 1/3) */
  inspFraction: number;
  /** CO2 ramp speed in mmHg/min and the level it stops at */
  rampMmHgPerMin: number;
  rampMaxMmHg: number;
  /** pneumotach constant used to turn flow into ΔP, L/min per Pa */
  kPneumo: number;
  /** barometric pressure the "true" gas is at, mmHg (converts mmHg → %) */
  barometricMmHg: number;
  scenario: Scenario;
  seed: number;
}

export const DEFAULT_SIM: SimParams = {
  rr: 15,
  etco2: 38,
  vtMl: 500,
  inspFraction: 1 / 3,
  rampMmHgPerMin: 15,
  rampMaxMmHg: 62,
  kPneumo: 0.2,
  barometricMmHg: 760,
  scenario: 'normal',
  seed: 1,
};

export interface PhysSample {
  /** CO2 in % at the sensor, or null when the sensor is disconnected */
  co2Pct: number | null;
  /** flow-sensor reading, L/min (positive = inspiration) */
  flowLpm: number;
  /** pneumotach differential pressure, Pa */
  pneumoPa: number;
}

/**
 * Synthetic breathing: half-sine inspiratory flow, exponential expiratory flow,
 * and a capnogram with a fast upstroke and a slowly rising alveolar plateau whose
 * end value is exactly the breath's end-tidal CO2.
 */
export class PhysioModel {
  params: SimParams;
  private rand: () => number;
  private t = 0;
  private breathStart = 0;
  private period = 4;
  private ti = 4 / 3;
  private et = 38;
  private prevEnd = 38;
  private target = 38;
  private qpk = 0;
  private qe = 0;
  private tau = 0.5;
  private apneaCo2 = 0;

  constructor(params: Partial<SimParams> = {}) {
    this.params = { ...DEFAULT_SIM, ...params };
    this.rand = mulberry32(this.params.seed);
    this.resetBreathing();
  }

  setParams(p: Partial<SimParams>): void {
    const scenarioChanged = p.scenario !== undefined && p.scenario !== this.params.scenario;
    this.params = { ...this.params, ...p };
    if (scenarioChanged || p.etco2 !== undefined) this.target = this.params.etco2;
  }

  /** Current target end-tidal CO2 (rises during the ramp scenario). */
  get targetEtco2(): number {
    return this.target;
  }

  private resetBreathing(): void {
    this.et = this.params.etco2;
    this.prevEnd = this.et;
    this.target = this.params.etco2;
    this.planBreath();
  }

  private planBreath(): void {
    const p = this.params;
    const drive = Math.max(0, this.et - p.etco2 - 2);
    const rr = p.rr + 0.2 * drive;
    this.period = 60 / rr;
    this.ti = this.period * p.inspFraction;
    const vtL = (p.vtMl / 1000) * (1 + 0.04 * Math.max(0, this.et - p.etco2));
    this.qpk = (vtL * 60 * Math.PI) / (2 * this.ti);
    const te = this.period - this.ti;
    this.tau = te / 5;
    const c = 0.06;
    this.qe = (vtL * 60) / ((this.tau * this.tau) / (this.tau + c));
  }

  private noise(scale: number): number {
    return (this.rand() - 0.5) * 2 * scale;
  }

  /** Advance by dt seconds and return the sensor values at the new time. */
  step(dt: number): PhysSample {
    const p = this.params;
    this.t += dt;
    if (p.scenario === 'co2Ramp') this.target = Math.min(p.rampMaxMmHg, this.target + (p.rampMmHgPerMin / 60) * dt);

    const noisy = p.scenario === 'noisy' ? 5 : 1;
    let flow: number;
    let co2: number;

    if (p.scenario === 'apnea') {
      this.apneaCo2 *= Math.exp(-dt / 3);
      flow = 0;
      co2 = this.apneaCo2;
      this.breathStart = this.t;
    } else {
      let x = this.t - this.breathStart;
      if (x >= this.period) {
        this.breathStart += this.period;
        this.prevEnd = this.et;
        this.et += (this.target - this.et) * 0.5;
        this.planBreath();
        x = this.t - this.breathStart;
      }
      if (x < this.ti) {
        flow = this.qpk * Math.sin((Math.PI * x) / this.ti);
        co2 = this.prevEnd * Math.exp(-x / 0.05);
      } else {
        const e = x - this.ti;
        const te = this.period - this.ti;
        flow = -this.qe * Math.exp(-e / this.tau) * (1 - Math.exp(-e / 0.06));
        const up = 1 / (1 + Math.exp(-(e - 0.28) / 0.04));
        co2 = up * (this.et - 3 + 3 * (e / te));
      }
      this.apneaCo2 = co2;
    }

    const flowLpm = flow + this.noise(0.6 * noisy);
    const pneumoPa = (flow + this.noise(0.4 * noisy)) / p.kPneumo;
    const co2Mm = Math.max(0, co2 + this.noise(0.25 * noisy));
    const co2Pct = p.scenario === 'co2Disconnect' ? null : (co2Mm / p.barometricMmHg) * 100;
    return { co2Pct, flowLpm, pneumoPa };
  }
}
