import { LineSplitter } from '../protocol/protocol';
import type { Source, SourceEvents } from './types';

export const webSerialSupported = (): boolean => typeof navigator !== 'undefined' && 'serial' in navigator;

/** Arduino over USB with the Web Serial API (desktop Chrome / Edge, HTTPS or localhost). */
export class SerialSource implements Source {
  readonly kind = 'serial' as const;
  label = 'Arduino (USB)';
  private port: SerialPort | null = null;
  private reader: ReadableStreamDefaultReader<string> | null = null;
  private readDone: Promise<void> | null = null;
  private closing = false;
  private events: SourceEvents | null = null;
  private onDisconnect = (e: Event) => {
    if ((e as Event & { target: SerialPort }).target === this.port && !this.closing) {
      this.events?.onStatus('error', 'Device disconnected (USB unplugged or reset)');
    }
  };

  constructor(private baudRate = 115200) {}

  async start(events: SourceEvents): Promise<void> {
    if (!webSerialSupported()) throw new Error('This browser has no Web Serial. Use desktop Chrome or Edge, or the Python bridge.');
    this.events = events;
    // requestPort must be the first await so it still counts as part of the click.
    const port = await navigator.serial.requestPort();
    events.onStatus('connecting');
    await port.open({ baudRate: this.baudRate, bufferSize: 65536 });
    this.port = port;
    this.closing = false;
    const info = port.getInfo();
    if (info.usbVendorId) this.label = `Arduino (USB ${info.usbVendorId.toString(16).padStart(4, '0')}:${(info.usbProductId ?? 0).toString(16).padStart(4, '0')})`;
    navigator.serial.addEventListener('disconnect', this.onDisconnect);
    events.onStatus('connected');
    this.readDone = this.readLoop();
  }

  private async readLoop(): Promise<void> {
    const port = this.port;
    if (!port?.readable) return;
    const split = new LineSplitter();
    const decoder = new TextDecoderStream();
    const pipe = port.readable.pipeTo(decoder.writable as WritableStream<Uint8Array>).catch(() => {});
    this.reader = decoder.readable.getReader();
    try {
      for (;;) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value) split.push(value, (l) => this.events?.onLine(l));
      }
    } catch (e) {
      if (!this.closing) this.events?.onStatus('error', `Serial read failed: ${(e as Error).message}`);
    } finally {
      this.reader.releaseLock();
      await pipe;
    }
  }

  async send(command: string): Promise<void> {
    const w = this.port?.writable?.getWriter();
    if (!w) return;
    try {
      await w.write(new TextEncoder().encode(command.trim() + '\n'));
    } finally {
      w.releaseLock();
    }
  }

  async stop(): Promise<void> {
    this.closing = true;
    navigator.serial?.removeEventListener('disconnect', this.onDisconnect);
    try {
      await this.reader?.cancel();
    } catch {
      /* already closed */
    }
    await this.readDone;
    try {
      await this.port?.close();
    } catch {
      /* already closed */
    }
    this.port = null;
    this.events?.onStatus('disconnected');
    this.events = null;
  }
}
