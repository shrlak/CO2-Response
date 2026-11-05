import { DEFAULT_CHANNELS } from '../src/config/channels';
import type { Encoders } from '../src/sim/simulatorCore';

const lin = (id: 'co2' | 'flow' | 'pneumo') => {
  const cal = DEFAULT_CHANNELS.channels[id].calibration;
  if (cal.type !== 'linear') throw new Error('default calibration must be linear');
  return (y: number) => (y - cal.offset) / cal.gain;
};

/** Encoders that invert the default (linear) channel calibration. */
export const defaultEncoders: Encoders = { co2: lin('co2'), flow: lin('flow'), pneumo: lin('pneumo') };
