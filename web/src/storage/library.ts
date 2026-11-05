import { SessionParser, summarize, type ParsedSession, type SessionSummary } from './csv';

/**
 * Session library in IndexedDB. Every recording is written here in ~1 s chunks while
 * it runs (so a crashed tab loses at most a second), and imported files are stored
 * the same way. A session can be turned back into its single CSV file at any time.
 */

export type SessionStatus = 'recording' | 'complete' | 'interrupted' | 'imported';

export interface SessionRecord {
  id: string;
  fileName: string;
  studyCode: string;
  startedAt: number;
  endedAt: number | null;
  status: SessionStatus;
  source: string;
  bytes: number;
  chunks: number;
  lastWriteAt: number;
  stats: Partial<SessionSummary>;
}

const DB_NAME = 'co2r-data';
const DB_VERSION = 1;

const req = <T>(r: IDBRequest<T>) =>
  new Promise<T>((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });

const done = (t: IDBTransaction) =>
  new Promise<void>((resolve, reject) => {
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error ?? new Error('Storage transaction aborted'));
  });

export class SessionLibrary {
  private dbp: Promise<IDBDatabase> | null = null;

  get available(): boolean {
    return typeof indexedDB !== 'undefined';
  }

  private db(): Promise<IDBDatabase> {
    if (!this.available) return Promise.reject(new Error('This browser has no IndexedDB, so sessions cannot be kept. Recordings still download when you stop.'));
    this.dbp ??= new Promise((resolve, reject) => {
      const open = indexedDB.open(DB_NAME, DB_VERSION);
      open.onupgradeneeded = () => {
        const db = open.result;
        if (!db.objectStoreNames.contains('sessions')) db.createObjectStore('sessions', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('chunks')) db.createObjectStore('chunks', { keyPath: ['sid', 'i'] });
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
    return this.dbp;
  }

  async list(): Promise<SessionRecord[]> {
    const db = await this.db();
    const all = await req(db.transaction('sessions').objectStore('sessions').getAll() as IDBRequest<SessionRecord[]>);
    return all.sort((a, b) => b.startedAt - a.startedAt);
  }

  async get(id: string): Promise<SessionRecord | undefined> {
    const db = await this.db();
    return req(db.transaction('sessions').objectStore('sessions').get(id) as IDBRequest<SessionRecord | undefined>);
  }

  async put(rec: SessionRecord): Promise<void> {
    const db = await this.db();
    const t = db.transaction('sessions', 'readwrite');
    t.objectStore('sessions').put(rec);
    await done(t);
  }

  /** Append one chunk of file text and update the record in the same transaction. */
  async appendChunk(rec: SessionRecord, text: string): Promise<void> {
    const db = await this.db();
    const t = db.transaction(['sessions', 'chunks'], 'readwrite');
    t.objectStore('chunks').put({ sid: rec.id, i: rec.chunks, text });
    rec.chunks += 1;
    rec.bytes += text.length;
    rec.lastWriteAt = Date.now();
    t.objectStore('sessions').put(rec);
    await done(t);
  }

  /** Stream a session's text, in order, chunk by chunk. */
  async readChunks(id: string, onChunk: (text: string) => void): Promise<void> {
    const db = await this.db();
    // read in batches so a long session never needs one huge transaction result
    let next = 0;
    for (;;) {
      const batch = await req(db.transaction('chunks').objectStore('chunks').getAll(IDBKeyRange.bound([id, next], [id, Number.MAX_SAFE_INTEGER]), 500) as IDBRequest<{ i: number; text: string }[]>);
      if (!batch.length) break;
      for (const c of batch) onChunk(c.text);
      next = batch[batch.length - 1].i + 1;
      if (batch.length < 500) break;
    }
  }

  /** The session as its single CSV file. */
  async blob(id: string): Promise<Blob> {
    const blobs: Blob[] = [];
    let parts: string[] = [];
    await this.readChunks(id, (t) => {
      parts.push(t);
      if (parts.length >= 500) {
        blobs.push(new Blob(parts, { type: 'text/csv' }));
        parts = [];
      }
    });
    if (parts.length) blobs.push(new Blob(parts, { type: 'text/csv' }));
    return new Blob(blobs, { type: 'text/csv' });
  }

  /** Parse a stored session for review or replay. */
  async parse(id: string): Promise<ParsedSession> {
    const p = new SessionParser();
    await this.readChunks(id, (t) => p.push(t));
    return p.finish();
  }

  async delete(id: string): Promise<void> {
    const db = await this.db();
    const t = db.transaction(['sessions', 'chunks'], 'readwrite');
    t.objectStore('sessions').delete(id);
    t.objectStore('chunks').delete(IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]));
    await done(t);
  }

  /**
   * Store a session file (v1 or v2). It is validated while it is read; a file that
   * is not a session is rejected and nothing is kept.
   */
  async importFile(file: Blob, fileName: string): Promise<{ rec: SessionRecord; parsed: ParsedSession }> {
    const parser = new SessionParser();
    const rec: SessionRecord = {
      id: newId(),
      fileName,
      studyCode: '',
      startedAt: Date.now(),
      endedAt: null,
      status: 'imported',
      source: 'Imported file',
      bytes: 0,
      chunks: 0,
      lastWriteAt: Date.now(),
      stats: {},
    };
    const reader = file.stream().pipeThrough(new TextDecoderStream()).getReader();
    let pending = '';
    try {
      for (;;) {
        const { value, done: end } = await reader.read();
        if (value) {
          parser.push(value);
          pending += value;
        }
        if (pending.length >= 1 << 21 || (end && pending)) {
          await this.appendChunk(rec, pending);
          pending = '';
        }
        if (end) break;
      }
      const parsed = parser.finish();
      rec.studyCode = parsed.studyCode;
      rec.startedAt = parsed.startedMs || rec.startedAt;
      rec.endedAt = rec.startedAt + parsed.durationS * 1000;
      rec.source = `Imported · ${parsed.meta.source ?? 'unknown source'}`;
      rec.stats = summarize(parsed);
      await this.put(rec);
      return { rec, parsed };
    } catch (e) {
      await this.delete(rec.id).catch(() => {});
      throw e;
    }
  }

  /**
   * Recordings left in 'recording' state by a tab that closed or crashed. Anything
   * not written to for 15 s is marked interrupted; its data up to then is kept.
   */
  async recoverInterrupted(now = Date.now()): Promise<SessionRecord[]> {
    const out: SessionRecord[] = [];
    for (const r of await this.list()) {
      if (r.status === 'recording' && now - r.lastWriteAt > 15000) {
        r.status = 'interrupted';
        r.endedAt = r.lastWriteAt;
        await this.put(r);
        out.push(r);
      }
    }
    return out;
  }

  async usage(): Promise<{ usage: number; quota: number; persisted: boolean } | null> {
    try {
      const est = await navigator.storage?.estimate?.();
      const persisted = (await navigator.storage?.persisted?.()) ?? false;
      return est ? { usage: est.usage ?? 0, quota: est.quota ?? 0, persisted } : null;
    } catch {
      return null;
    }
  }

  /** Ask the browser not to evict saved sessions when disk space runs low. */
  async persist(): Promise<boolean> {
    try {
      return (await navigator.storage?.persist?.()) ?? false;
    } catch {
      return false;
    }
  }
}

export function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
