import { downloadBlob } from './download';
import { formatEventRow, formatHeader, formatSampleRow, sessionFileName, type SampleRow, type SessionMeta } from './csv';

interface FileSystemWritableLike {
  write(data: string): Promise<void>;
  close(): Promise<void>;
}
type SaveFilePicker = (opts: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<{
  name: string;
  createWritable(): Promise<FileSystemWritableLike>;
}>;

/**
 * Writes a session CSV. In Chrome/Edge it streams straight to a file the user picks,
 * so overnight sessions do not sit in memory. Elsewhere it buffers and downloads on stop.
 */
export class SessionRecorder {
  active = false;
  startedAt = 0;
  fileName = '';
  mode: 'file' | 'memory' = 'memory';
  bytes = 0;
  private chunks: string[] = [];
  private pending: string[] = [];
  private writable: FileSystemWritableLike | null = null;
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private writing: Promise<void> = Promise.resolve();

  /** Must be called from a click handler (the file picker needs a user gesture). */
  async start(meta: SessionMeta, preferFile = true): Promise<boolean> {
    if (this.active) return true;
    this.fileName = sessionFileName(meta.studyCode, new Date(meta.started));
    this.chunks = [];
    this.pending = [];
    this.bytes = 0;
    this.writable = null;
    this.mode = 'memory';

    const picker = (window as unknown as { showSaveFilePicker?: SaveFilePicker }).showSaveFilePicker;
    if (preferFile && picker) {
      try {
        const handle = await picker({ suggestedName: this.fileName, types: [{ description: 'CSV', accept: { 'text/csv': ['.csv'] } }] });
        this.writable = await handle.createWritable();
        this.fileName = handle.name;
        this.mode = 'file';
      } catch (e) {
        if ((e as DOMException)?.name === 'AbortError') return false; // user cancelled
        this.writable = null; // picker unavailable in this context: fall back to memory
      }
    }
    this.active = true;
    this.startedAt = Date.now();
    this.append(formatHeader(meta));
    this.flushTimer = setInterval(() => this.flush(), 1000);
    return true;
  }

  private append(s: string): void {
    this.bytes += s.length;
    if (this.mode === 'file') this.pending.push(s);
    else this.chunks.push(s);
  }

  private flush(): void {
    if (!this.writable || !this.pending.length) return;
    const data = this.pending.join('');
    this.pending = [];
    const w = this.writable;
    this.writing = this.writing.then(() => w.write(data)).catch(() => {});
  }

  sample(r: SampleRow): void {
    if (this.active) this.append(formatSampleRow(r));
  }

  event(wallMs: number, text: string): void {
    if (this.active) this.append(formatEventRow(wallMs, text));
  }

  async stop(): Promise<void> {
    if (!this.active) return;
    this.active = false;
    if (this.flushTimer) clearInterval(this.flushTimer);
    this.flushTimer = null;
    if (this.mode === 'file' && this.writable) {
      this.flush();
      const w = this.writable;
      this.writable = null;
      await this.writing;
      await w.close();
    } else {
      downloadBlob(new Blob(this.chunks, { type: 'text/csv' }), this.fileName);
      this.chunks = [];
    }
  }
}
