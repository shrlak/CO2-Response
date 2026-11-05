export const fmt = (v: number | undefined | null, digits = 0): string => (v === undefined || v === null || !Number.isFinite(v) ? '--' : v.toFixed(digits));

const pad2 = (n: number) => String(n).padStart(2, '0');
export const hms = (ms: number): string => {
  const d = new Date(ms);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
};
export const duration = (ms: number): string => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`;
};
export const mss = (ms: number): string => {
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
};
