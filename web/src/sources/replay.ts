import { formatDataLine } from '../protocol/protocol';
import type { ParsedSession } from '../storage/csv';
import type { Source, SourceEvents } from './types';

/** Plays a recorded session back through the normal parser and processing path. */
export class ReplaySource implements Source {
  readonly kind = 'replay' as const;
  readonly label: string;
  speed = 1;
  private timer: ReturnType<typeof setInterval> | null = null;
  private events: SourceEvents | null = null;
  private i = 0;
  private t0Device = 0;
  private t0Wall = 0;

  constructor(
    private session: ParsedSession,
    fileName: string,
  ) {
    this.label = `Replay ${fileName}`;
  }

  get progress(): number {
    return this.session.samples.length ? this.i / this.session.samples.length : 1;
  }

  get rateHz(): number {
    const s = this.session.samples;
    if (s.length < 2) return 100;
    const span = s[Math.min(s.length - 1, 200)].tMs - s[0].tMs;
    return span > 0 ? Math.round((Math.min(s.length - 1, 200) * 1000) / span) : 100;
  }

  async start(events: SourceEvents): Promise<void> {
    this.events = events;
    events.onStatus('connected');
    events.onLine(`I,FW,${this.session.meta.firmware || 'replay'},ADC,FILE,CH,3,RATE,${this.rateHz}`);
    this.i = 0;
    this.t0Device = this.session.samples[0]?.tMs ?? 0;
    this.t0Wall = performance.now();
    this.timer = setInterval(() => this.pump(), 20);
  }

  private pump(): void {
    const s = this.session.samples;
    const deviceNow = this.t0Device + (performance.now() - this.t0Wall) * this.speed;
    let n = 0;
    while (this.i < s.length && s[this.i].tMs <= deviceNow && n < 5000) {
      const r = s[this.i++];
      this.events?.onLine(formatDataLine(r.seq, r.tMs, r.volts));
      n++;
    }
    if (this.i >= s.length) {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      this.events?.onStatus('disconnected', 'Replay finished');
    }
  }

  async send(): Promise<void> {}

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.events?.onStatus('disconnected');
    this.events = null;
  }
}
