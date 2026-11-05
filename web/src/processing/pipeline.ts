import type { ChannelId, ChannelsConfig } from '../config/channels';
import { BreathDetector, type Breath } from './breath';
import { applyCalibration, pneumoFlow } from './calibration';
import { pctToMmHg } from './co2';
import { LowPass } from './filter';
import { RailMonitor } from './quality';

export interface ProcessingOptions {
  barometricMmHg: number;
  waterVapor: boolean;
  filterOn: boolean;
  filterHz: number;
  sampleRateHz: number;
  deadbandLpm: number;
  minBreathSec: number;
}

export const DEFAULT_PROCESSING: ProcessingOptions = {
  barometricMmHg: 760,
  waterVapor: false,
  filterOn: true,
  filterHz: 10,
  sampleRateHz: 100,
  deadbandLpm: 3,
  minBreathSec: 1,
};

export interface Processed {
  tSec: number;
  volts: readonly number[];
  co2Pct: number;
  co2MmHg: number;
  flowLpm: number;
  pneumoPa: number;
  pneumoLpm: number;
  /** false when the channel is at a rail (stuck) or not a number */
  valid: Record<ChannelId, boolean>;
  stuck: Record<ChannelId, boolean>;
  breath: Breath | null;
}

const IDS: ChannelId[] = ['co2', 'flow', 'pneumo'];

/** Volts in, engineering units and breaths out. Pure apart from its own filter/detector state. */
export class Pipeline {
  private filters!: Record<ChannelId, LowPass>;
  private rails!: Record<ChannelId, RailMonitor>;
  readonly detector: BreathDetector;

  constructor(
    private channels: ChannelsConfig,
    private opts: ProcessingOptions = DEFAULT_PROCESSING,
  ) {
    this.detector = new BreathDetector({ deadbandLpm: opts.deadbandLpm, minBreathSec: opts.minBreathSec });
    this.rebuild();
  }

  private rebuild(): void {
    const { filterHz, sampleRateHz } = this.opts;
    const a = this.channels.adc;
    this.filters = { co2: new LowPass(filterHz, sampleRateHz), flow: new LowPass(filterHz, sampleRateHz), pneumo: new LowPass(filterHz, sampleRateHz) };
    const rail = (id: ChannelId) => new RailMonitor(a.railLowV, a.railHighV, a.railToleranceV, 2, this.channels.channels[id].railCheck ?? 'both');
    this.rails = { co2: rail('co2'), flow: rail('flow'), pneumo: rail('pneumo') };
    this.detector.opts = { deadbandLpm: this.opts.deadbandLpm, minBreathSec: this.opts.minBreathSec };
  }

  setChannels(c: ChannelsConfig): void {
    this.channels = c;
    this.rebuild();
  }

  setOptions(o: ProcessingOptions): void {
    this.opts = o;
    this.rebuild();
  }

  get options(): ProcessingOptions {
    return this.opts;
  }

  process(tMs: number, volts: readonly number[]): Processed {
    const tSec = tMs / 1000;
    const a = this.channels.adc;
    const valid = {} as Record<ChannelId, boolean>;
    const stuck = {} as Record<ChannelId, boolean>;
    const val = {} as Record<ChannelId, number>;

    for (const id of IDS) {
      const ch = this.channels.channels[id];
      const v = volts[ch.index];
      const finite = Number.isFinite(v);
      stuck[id] = finite ? this.rails[id].push(tSec, v) : false;
      // A sample at the rail is not trusted even before the 2 s hold elapses.
      const atRail = finite && RailMonitor.atRail(v, a.railLowV, a.railHighV, a.railToleranceV, ch.railCheck ?? 'both');
      valid[id] = finite && !atRail;
      let y = valid[id] ? applyCalibration(ch.calibration, v) - ch.zeroOffset : NaN;
      if (this.opts.filterOn) y = valid[id] ? this.filters[id].process(y) : (this.filters[id].reset(), NaN);
      val[id] = y;
    }

    const co2Pct = val.co2;
    const co2MmHg = pctToMmHg(co2Pct, this.opts.barometricMmHg, this.opts.waterVapor);
    const model = this.channels.channels.pneumo.flowModel ?? { k1: 0.2, k2: 0 };
    const pneumoLpm = pneumoFlow(model, val.pneumo);
    const breath = this.detector.push(tSec, pneumoLpm, co2MmHg);

    return { tSec, volts, co2Pct, co2MmHg, flowLpm: val.flow, pneumoPa: val.pneumo, pneumoLpm, valid, stuck, breath };
  }
}
