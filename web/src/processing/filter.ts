/** 2nd-order Butterworth low-pass (RBJ biquad), one per channel. */
export class LowPass {
  private b0 = 1;
  private b1 = 0;
  private b2 = 0;
  private a1 = 0;
  private a2 = 0;
  private x1 = 0;
  private x2 = 0;
  private y1 = 0;
  private y2 = 0;
  private primed = false;

  constructor(cutoffHz: number, sampleHz: number) {
    const fc = Math.min(cutoffHz, sampleHz * 0.45);
    const w0 = (2 * Math.PI * fc) / sampleHz;
    const alpha = Math.sin(w0) / (2 * Math.SQRT1_2); // Q = 1/√2
    const cos = Math.cos(w0);
    const a0 = 1 + alpha;
    this.b0 = (1 - cos) / 2 / a0;
    this.b1 = (1 - cos) / a0;
    this.b2 = (1 - cos) / 2 / a0;
    this.a1 = (-2 * cos) / a0;
    this.a2 = (1 - alpha) / a0;
  }

  process(x: number): number {
    if (!Number.isFinite(x)) {
      this.primed = false;
      return x;
    }
    if (!this.primed) {
      // start at steady state so a step at connect does not ring
      this.x1 = this.x2 = this.y1 = this.y2 = x;
      this.primed = true;
    }
    const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2;
    this.x2 = this.x1;
    this.x1 = x;
    this.y2 = this.y1;
    this.y1 = y;
    return y;
  }

  reset(): void {
    this.primed = false;
  }
}
