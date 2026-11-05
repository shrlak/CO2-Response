import { LineSplitter } from '../protocol/protocol';
import type { Source, SourceEvents } from './types';

/** Lines relayed by bridge/serial_ws_bridge.py over a WebSocket (for Safari/Firefox or a second screen). */
export class BridgeSource implements Source {
  readonly kind = 'bridge' as const;
  readonly label: string;
  private ws: WebSocket | null = null;
  private events: SourceEvents | null = null;
  private closing = false;

  constructor(private url: string) {
    this.label = `Bridge ${url}`;
  }

  start(events: SourceEvents): Promise<void> {
    this.events = events;
    this.closing = false;
    events.onStatus('connecting');
    return new Promise((resolve, reject) => {
      let ws: WebSocket;
      try {
        ws = new WebSocket(this.url);
      } catch (e) {
        reject(new Error(`Bad bridge address: ${(e as Error).message}`));
        return;
      }
      this.ws = ws;
      const split = new LineSplitter();
      ws.onopen = () => {
        events.onStatus('connected');
        resolve();
      };
      ws.onmessage = (m) => split.push(String(m.data).endsWith('\n') ? String(m.data) : `${m.data}\n`, (l) => this.events?.onLine(l));
      ws.onerror = () => reject(new Error(`Could not reach the bridge at ${this.url}. Is serial_ws_bridge.py running?`));
      ws.onclose = () => {
        if (!this.closing) this.events?.onStatus('error', 'Bridge connection closed');
      };
    });
  }

  async send(command: string): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(command.trim());
  }

  async stop(): Promise<void> {
    this.closing = true;
    this.ws?.close();
    this.ws = null;
    this.events?.onStatus('disconnected');
    this.events = null;
  }
}
