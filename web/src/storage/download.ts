/**
 * Saving files. In a normal browser tab this is an ordinary download. When the app is
 * shown inside a claude.ai artifact (where downloads are sandboxed) it uses the
 * viewer's `downloads` capability, which asks the viewer to confirm the save.
 */

interface DownloadsNs {
  save(r: { filename: string; data: Blob | string }): Promise<{ status: 'saved' | 'delivered' }>;
}

let downloadsP: Promise<DownloadsNs | null> | null = null;

/** Resolve the artifact capability early so a later save is not delayed. */
export function initDownloads(): void {
  const c = (globalThis as unknown as { claude?: { use?: (n: string) => Promise<unknown> } }).claude;
  if (c?.use) downloadsP = c.use('downloads').then((d) => (d as DownloadsNs) ?? null).catch(() => null);
}

export type SaveResult = 'downloaded' | 'saved' | 'declined';

function anchorDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function saveBlob(blob: Blob, filename: string): Promise<SaveResult> {
  const d = downloadsP ? await downloadsP : null;
  if (d) {
    try {
      await d.save({ filename, data: blob });
      return 'saved';
    } catch (e) {
      if ((e as { code?: string })?.code === 'declined') return 'declined';
      // any other refusal: fall back to a plain download
    }
  }
  anchorDownload(blob, filename);
  return 'downloaded';
}

export function downloadText(text: string, filename: string, type = 'text/plain'): Promise<SaveResult> {
  return saveBlob(new Blob([text], { type }), filename);
}
