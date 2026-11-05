/** Flags a channel that has sat at an ADC rail (sensor unplugged, saturated or shorted). */
import type { RailCheck } from '../config/channels';

export class RailMonitor {
  private since: number | null = null;

  constructor(
    private lowV: number,
    private highV: number,
    private tolV: number,
    private holdSec = 2,
    private check: RailCheck = 'both',
  ) {}

  /** @returns true while the channel is at a rail and has been for holdSec */
  push(tSec: number, v: number): boolean {
    const atRail = RailMonitor.atRail(v, this.lowV, this.highV, this.tolV, this.check);
    if (!atRail) {
      this.since = null;
      return false;
    }
    if (this.since === null) this.since = tSec;
    return tSec - this.since >= this.holdSec;
  }

  /** true as soon as the sample is at a rail, before the hold time */
  static atRail(v: number, lowV: number, highV: number, tolV: number, check: RailCheck = 'both'): boolean {
    const low = (check === 'both' || check === 'low') && v <= lowV + tolV;
    const high = (check === 'both' || check === 'high') && v >= highV - tolV;
    return low || high;
  }
}
