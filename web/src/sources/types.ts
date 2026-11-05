export type SourceKind = 'serial' | 'simulator' | 'replay' | 'bridge';
export type SourceStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface SourceEvents {
  /** One raw protocol line, without the newline. */
  onLine(line: string): void;
  onStatus(status: SourceStatus, detail?: string): void;
}

/** Anything that produces protocol lines: the Arduino, the simulator, a CSV replay, the bridge. */
export interface Source {
  readonly kind: SourceKind;
  readonly label: string;
  start(events: SourceEvents): Promise<void>;
  stop(): Promise<void>;
  send(command: string): Promise<void>;
}
