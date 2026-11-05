/**
 * Alarm tones with the Web Audio API, patterned after IEC 60601-1-8:
 * high priority = 10 pulses (5 + 5) repeated every 5 s, medium = 3 pulses every 15 s.
 * Browsers only allow audio after a click, so `arm()` must run inside a click handler.
 */
export class AlarmAudio {
  private ctx: AudioContext | null = null;
  volume = 0.8;

  get armed(): boolean {
    return this.ctx !== null && this.ctx.state === 'running';
  }

  async arm(): Promise<boolean> {
    try {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx ??= new Ctor();
      await this.ctx.resume();
      return this.ctx.state === 'running';
    } catch {
      return false;
    }
  }

  private pulses(freq: number, count: number, groupOf: number, pulseSec = 0.15, gapSec = 0.1, groupGapSec = 0.35): void {
    const ctx = this.ctx;
    if (!ctx || ctx.state !== 'running') return;
    const peak = 0.25 * Math.max(0.1, Math.min(1, this.volume));
    let t = ctx.currentTime + 0.02;
    for (let i = 0; i < count; i++) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'square';
      o.frequency.value = freq;
      // a few harmonics-friendly envelopes so the tone is easy to localize
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(peak, t + 0.015);
      g.gain.setValueAtTime(peak, t + pulseSec - 0.02);
      g.gain.linearRampToValueAtTime(0, t + pulseSec);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + pulseSec + 0.01);
      t += pulseSec + gapSec + ((i + 1) % groupOf === 0 ? groupGapSec : 0);
    }
  }

  high(): void {
    this.pulses(960, 10, 5);
  }

  medium(): void {
    this.pulses(660, 3, 3, 0.2, 0.15);
  }

  /** Short confirmation chirp after arming. */
  confirm(): void {
    this.pulses(880, 1, 1, 0.12);
  }
}
