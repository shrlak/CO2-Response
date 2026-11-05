import type { Breath } from './breath';

export function median(xs: readonly number[]): number {
  const v = xs.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return NaN;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

/** EtCO2 shown large: median of the last `n` breaths (default 3). */
export function etco2Median(breaths: readonly Breath[], n = 3): number {
  return median(breaths.slice(-n).map((b) => b.etco2));
}

/** Breaths/min: 60 / mean period of the last `n` breaths (default 4). */
export function respiratoryRate(breaths: readonly Breath[], n = 4): number {
  const p = breaths.slice(-n).map((b) => b.periodSec);
  if (!p.length) return NaN;
  return 60 / (p.reduce((a, b) => a + b, 0) / p.length);
}

/**
 * Minute ventilation, L/min, from breaths that ended in the trailing window.
 * Σ VT / Σ period, which equals Σ VT over 60 s once the window is full and is
 * not under-read during the first minute.
 */
export function minuteVentilation(breaths: readonly Breath[], nowSec: number, windowSec = 60): number {
  let vt = 0;
  let span = 0;
  for (let i = breaths.length - 1; i >= 0; i--) {
    const b = breaths[i];
    if (b.tEnd <= nowSec - windowSec) break;
    if (!Number.isFinite(b.vtMl)) continue;
    vt += b.vtMl;
    span += b.periodSec;
  }
  return span > 0 ? ((vt / 1000) * 60) / span : NaN;
}

/** Ordinary least squares y = slope·x + intercept. */
export function linearFit(xs: readonly number[], ys: readonly number[]): { slope: number; intercept: number; r2: number } | null {
  const n = Math.min(xs.length, ys.length);
  if (n < 3) return null;
  let sx = 0, sy = 0, sxx = 0, sxy = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    sx += xs[i];
    sy += ys[i];
    sxx += xs[i] * xs[i];
    sxy += xs[i] * ys[i];
    syy += ys[i] * ys[i];
  }
  const den = n * sxx - sx * sx;
  if (Math.abs(den) < 1e-9) return null;
  const slope = (n * sxy - sx * sy) / den;
  const intercept = (sy - slope * sx) / n;
  const r2den = den * (n * syy - sy * sy);
  const r2 = r2den > 0 ? (n * sxy - sx * sy) ** 2 / r2den : 0;
  return { slope, intercept, r2 };
}
