import defaults from '../../public/config/channels.json';

export type ChannelId = 'co2' | 'flow' | 'pneumo';
export const CHANNEL_IDS: readonly ChannelId[] = ['co2', 'flow', 'pneumo'];

export type Calibration =
  | { type: 'linear'; gain: number; offset: number }
  /** value = c0 + c1·V + c2·V² + … */
  | { type: 'polynomial'; coeffs: number[] }
  /** Piecewise-linear lookup, points sorted by volts; extrapolates from the end segments. */
  | { type: 'table'; points: [number, number][] };

export interface FlowModel {
  /** L/min per Pa */
  k1: number;
  /** L/min per Pa², applied as k2·ΔP·|ΔP| so the curve stays odd-symmetric */
  k2: number;
}

export interface ChannelConfig {
  index: number;
  label: string;
  /** Unit the calibration produces: % for CO2, L/min for flow, Pa for the pneumotach transducer. */
  unit: string;
  calibration: Calibration;
  /** Subtracted after calibration (set by "Zero" buttons). */
  zeroOffset: number;
  flowModel?: FlowModel;
  calibratedAt: string | null;
  note?: string;
}

export interface AdcConfig {
  type: string;
  railLowV: number;
  railHighV: number;
  railToleranceV: number;
  note?: string;
}

export interface ChannelsConfig {
  version: number;
  adc: AdcConfig;
  channels: Record<ChannelId, ChannelConfig>;
}

/** Defaults bundled at build time from public/config/channels.json. */
export const DEFAULT_CHANNELS = defaults as ChannelsConfig;

export function cloneChannels(c: ChannelsConfig): ChannelsConfig {
  return JSON.parse(JSON.stringify(c)) as ChannelsConfig;
}

/** Throws with a readable message if `x` is not a usable channels config. */
export function validateChannels(x: unknown): ChannelsConfig {
  const c = x as ChannelsConfig;
  if (!c || typeof c !== 'object' || !c.channels || !c.adc) throw new Error('Missing "adc" or "channels".');
  for (const id of CHANNEL_IDS) {
    const ch = c.channels[id];
    if (!ch) throw new Error(`Missing channel "${id}".`);
    const cal = ch.calibration;
    if (!cal) throw new Error(`Channel "${id}" has no calibration.`);
    if (cal.type === 'linear') {
      if (!Number.isFinite(cal.gain) || !Number.isFinite(cal.offset)) throw new Error(`Channel "${id}": gain and offset must be numbers.`);
    } else if (cal.type === 'polynomial') {
      if (!Array.isArray(cal.coeffs) || !cal.coeffs.length || !cal.coeffs.every(Number.isFinite)) throw new Error(`Channel "${id}": coefficients must be numbers.`);
    } else if (cal.type === 'table') {
      if (!Array.isArray(cal.points) || cal.points.length < 2) throw new Error(`Channel "${id}": a table needs at least 2 points.`);
      if (!cal.points.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) throw new Error(`Channel "${id}": table points must be [volts, value] pairs.`);
    } else {
      throw new Error(`Channel "${id}": unknown calibration type.`);
    }
    if (!Number.isFinite(ch.zeroOffset)) ch.zeroOffset = 0;
  }
  if (!c.channels.pneumo.flowModel) c.channels.pneumo.flowModel = { k1: 0.2, k2: 0 };
  return c;
}
