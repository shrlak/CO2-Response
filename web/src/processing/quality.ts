/** Flags a channel that has sat at an ADC rail (sensor unplugged, saturated or shorted). */
export class RailMonitor {
  private since: number | null = null;

  constructor(
    private lowV: number,
    private highV: number,
    private tolV: number,
    private holdSec = 2,
  ) {}

  /** @returns true while the channel is at a rail and has been for holdSec */
  push(tSec: number, v: number): boolean {
    const atRail = v <= this.lowV + this.tolV || v >= this.highV - this.tolV;
    if (!atRail) {
      this.since = null;
      return false;
    }
    if (this.since === null) this.since = tSec;
    return tSec - this.since >= this.holdSec;
  }

  /** true as soon as the sample is at a rail, before the hold time */
  static atRail(v: number, lowV: number, highV: number, tolV: number): boolean {
    return v <= lowV + tolV || v >= highV - tolV;
  }
}
