import { LineSplitter } from '../protocol/protocol';
import type { Source, SourceEvents } from './types';

/**
 * Lines from the Python acquisition server (python/, `co2r-daq serve`) over a WebSocket:
 * the Arduino relayed, or another DAQ (NI, LabJack, simulated). Also how Safari/Firefox
 * or a second screen see the data.
 */
export class BridgeSource implements Source {
  readonly kind = 'bridge' as const;
  readonly label: string;
  private ws: WebSocket | null = null;
  private events: SourceEvents | null = null;
  private closing = false;

  constructor(private url: string) {
    this.label = `Python ${url}`;
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
      ws.onerror = () => reject(new Error(`Could not reach ${this.url}. Is \`co2r-daq serve\` running?`));
      ws.onclose = () => {
        if (!this.closing) this.events?.onStatus('error', 'Python acquisition server closed the connection');
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
