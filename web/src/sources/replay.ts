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
    return this.session.n ? this.i / this.session.n : 1;
  }

  get rateHz(): number {
    const s = this.session;
    if (s.n < 2) return 100;
    const k = Math.min(s.n - 1, 200);
    const span = s.tMs[k] - s.tMs[0];
    return span > 0 ? Math.round((k * 1000) / span) : 100;
  }

  async start(events: SourceEvents): Promise<void> {
    this.events = events;
    events.onStatus('connected');
    events.onLine(`I,FW,${this.session.meta.firmware || 'replay'},ADC,FILE,CH,3,RATE,${this.rateHz}`);
    this.i = 0;
    this.t0Device = this.session.n ? this.session.tMs[0] : 0;
    this.t0Wall = performance.now();
    this.timer = setInterval(() => this.pump(), 20);
  }

  private pump(): void {
    const s = this.session;
    let deviceNow = this.t0Device + (performance.now() - this.t0Wall) * this.speed;
    let n = 0;
    while (this.i < s.n && n < 5000) {
      const i = this.i;
      // the device clock restarts if the recording spans a reconnect: re-anchor
      if (i > 0 && s.tMs[i] < s.tMs[i - 1]) {
        this.t0Device = s.tMs[i];
        this.t0Wall = performance.now();
        deviceNow = this.t0Device;
      }
      if (s.tMs[i] > deviceNow) break;
      this.events?.onLine(formatDataLine(s.seq[i], s.tMs[i], [s.v0[i], s.v1[i], s.v2[i]]));
      this.i++;
      n++;
    }
    if (this.i >= s.n) {
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
