export interface SweepOptions {
  min: number;
  max: number;
  ticks: number[];
  signed?: boolean;
  /** CSS custom property for the trace color, e.g. '--co2' */
  colorVar: string;
  /** dashed limit line in display units, or null */
  limit?: number | null;
  seconds: number;
  rateHz: number;
}

const cssVar = (el: Element, name: string) => getComputedStyle(el).getPropertyValue(name).trim();

/**
 * Monitor-style sweep: the trace is drawn left to right and wraps, with an erase bar
 * ahead of the pen, so old data does not move. Two stacked canvases: a static
 * background (grid, scale, limit) and the trace.
 */
export class SweepRenderer {
  private pending: number[] = [];
  private pos = 0;
  private last: [number, number] | null = null;
  private W = 0;
  private H = 0;
  private dpr = 1;
  private color = '#fff';
  private ro: ResizeObserver;
  frozen = false;

  constructor(
    private fg: HTMLCanvasElement,
    private bg: HTMLCanvasElement,
    private opts: SweepOptions,
  ) {
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(fg);
    this.resize();
  }

  dispose(): void {
    this.ro.disconnect();
  }

  setOptions(o: Partial<SweepOptions>): void {
    this.opts = { ...this.opts, ...o };
    this.resize();
  }

  resize(): void {
    const r = this.fg.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    for (const c of [this.fg, this.bg]) {
      c.width = Math.max(1, Math.round(r.width * this.dpr));
      c.height = Math.max(1, Math.round(r.height * this.dpr));
    }
    this.W = this.fg.width;
    this.H = this.fg.height;
    this.last = null;
    this.color = cssVar(this.fg, this.opts.colorVar) || '#fff';
    this.drawBackground();
  }

  private y(v: number): number {
    const top = 26 * this.dpr;
    const bot = 8 * this.dpr;
    const { min, max } = this.opts;
    const c = Math.min(max, Math.max(min, v));
    return this.H - bot - ((c - min) / (max - min)) * (this.H - top - bot);
  }

  drawBackground(): void {
    const g = this.bg.getContext('2d');
    if (!g) return;
    const d = this.dpr;
    const grid = cssVar(this.bg, '--grid');
    const muted = cssVar(this.bg, '--muted');
    const alarm = cssVar(this.bg, '--alarm-high');
    const font = cssVar(this.bg, '--font-ui') || 'sans-serif';
    g.clearRect(0, 0, this.W, this.H);
    g.font = `${12 * d}px ${font}`;
    g.textAlign = 'right';
    g.textBaseline = 'middle';
    const right = this.W - 34 * d;
    for (const t of this.opts.ticks) {
      const y = Math.round(this.y(t)) + 0.5;
      g.strokeStyle = grid;
      g.lineWidth = d;
      g.setLineDash([]);
      g.beginPath();
      g.moveTo(0, y);
      g.lineTo(right, y);
      g.stroke();
      g.fillStyle = muted;
      g.fillText(t > 0 && this.opts.signed ? `+${t}` : String(t), this.W - 6 * d, y);
    }
    const lim = this.opts.limit;
    if (lim !== null && lim !== undefined && lim > this.opts.min && lim < this.opts.max) {
      const y = Math.round(this.y(lim)) + 0.5;
      g.strokeStyle = alarm;
      g.lineWidth = 1.5 * d;
      g.setLineDash([8 * d, 6 * d]);
      g.beginPath();
      g.moveTo(0, y);
      g.lineTo(right, y);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = alarm;
      g.fillText(String(+lim.toFixed(1)), this.W - 6 * d, y);
    }
  }

  push(v: number): void {
    if (this.frozen) return;
    this.pending.push(v);
    if (this.pending.length > 6000) this.pending.splice(0, this.pending.length - 6000);
  }

  clear(): void {
    this.fg.getContext('2d')?.clearRect(0, 0, this.W, this.H);
    this.pending.length = 0;
    this.pos = 0;
    this.last = null;
  }

  draw(): void {
    if (!this.pending.length || !this.W) return;
    const ctx = this.fg.getContext('2d');
    if (!ctx) return;
    const d = this.dpr;
    const N = Math.max(10, Math.round(this.opts.seconds * this.opts.rateHz));
    const gap = 16 * d;
    const lw = 2 * d;
    ctx.strokeStyle = this.color;
    ctx.lineWidth = lw;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    for (const v of this.pending) {
      const i = this.pos % N;
      const x = (i / N) * this.W;
      if (i === 0) this.last = null;
      ctx.clearRect(x + lw, 0, gap, this.H);
      if (x + lw + gap > this.W) ctx.clearRect(0, 0, x + lw + gap - this.W, this.H);
      this.pos++;
      if (!Number.isFinite(v)) {
        this.last = null;
        continue;
      }
      const y = this.y(v);
      if (this.last) {
        ctx.moveTo(this.last[0], this.last[1]);
        ctx.lineTo(x, y);
      }
      this.last = [x, y];
    }
    ctx.stroke();
    this.pending.length = 0;
  }
}
