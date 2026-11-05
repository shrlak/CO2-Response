import { SimulatorCore, type Encoders } from '../sim/simulatorCore';
import type { SimParams } from '../sim/physiology';
import type { Source, SourceEvents } from './types';

/** Runs SimulatorCore against the wall clock, like a device streaming in real time. */
export class SimulatorSource implements Source {
  readonly kind = 'simulator' as const;
  readonly label = 'Simulator';
  readonly core: SimulatorCore;
  private timer: ReturnType<typeof setInterval> | null = null;
  private events: SourceEvents | null = null;
  private due = 0;
  private last = 0;

  constructor(encoders: Encoders, params: Partial<SimParams> = {}, railLowV = 0, railHighV = 5) {
    this.core = new SimulatorCore(encoders, params, railLowV, railHighV);
  }

  async start(events: SourceEvents): Promise<void> {
    this.events = events;
    events.onStatus('connected');
    for (const l of this.core.command('INFO')) events.onLine(l);
    this.last = performance.now();
    this.due = 0;
    this.timer = setInterval(() => this.pump(), 20);
  }

  private pump(): void {
    const now = performance.now();
    // Catch up after background-tab throttling, but never more than 2 s at once.
    this.due = Math.min(this.due + ((now - this.last) / 1000) * this.core.rateHz, 2 * this.core.rateHz);
    this.last = now;
    while (this.due >= 1) {
      this.due -= 1;
      for (const l of this.core.nextSample()) this.events?.onLine(l);
    }
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.events?.onStatus('disconnected');
    this.events = null;
  }

  async send(command: string): Promise<void> {
    for (const l of this.core.command(command)) this.events?.onLine(l);
  }
}
