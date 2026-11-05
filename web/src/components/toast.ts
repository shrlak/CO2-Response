type Listener = (msg: string | null) => void;
const listeners = new Set<Listener>();
let timer: ReturnType<typeof setTimeout> | null = null;

export function showToast(msg: string, ms = 3200): void {
  for (const l of listeners) l(msg);
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => listeners.forEach((l) => l(null)), ms);
}

export function onToast(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
