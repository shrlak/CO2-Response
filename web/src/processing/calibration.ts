import type { Calibration, FlowModel } from '../config/channels';

/** Volts → engineering units for one channel. */
export function applyCalibration(cal: Calibration, v: number): number {
  switch (cal.type) {
    case 'linear':
      return cal.gain * v + cal.offset;
    case 'polynomial': {
      let y = 0;
      for (let i = cal.coeffs.length - 1; i >= 0; i--) y = y * v + cal.coeffs[i];
      return y;
    }
    case 'table':
      return interpolateTable(cal.points, v);
  }
}

/** Piecewise-linear interpolation; extrapolates along the first and last segments. */
export function interpolateTable(points: readonly [number, number][], v: number): number {
  const p = [...points].sort((a, b) => a[0] - b[0]);
  if (p.length === 0) return NaN;
  if (p.length === 1) return p[0][1];
  let i = 1;
  while (i < p.length - 1 && v > p[i][0]) i++;
  const [x0, y0] = p[i - 1];
  const [x1, y1] = p[i];
  if (x1 === x0) return y0;
  return y0 + ((v - x0) * (y1 - y0)) / (x1 - x0);
}

/**
 * Engineering units → volts (used by the simulator to fake a sensor).
 * Exact for linear; bisection over [vMin, vMax] for the others, which assumes the
 * calibration is monotonic over that range.
 */
export function invertCalibration(cal: Calibration, y: number, vMin = 0, vMax = 5): number {
  if (cal.type === 'linear') return cal.gain === 0 ? NaN : (y - cal.offset) / cal.gain;
  let lo = vMin;
  let hi = vMax;
  const rising = applyCalibration(cal, hi) >= applyCalibration(cal, lo);
  for (let i = 0; i < 50; i++) {
    const mid = (lo + hi) / 2;
    const ym = applyCalibration(cal, mid);
    if (ym < y === rising) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

/** Straight line through two (volts, value) points, e.g. a zero and a span reading. */
export function twoPointLinear(v1: number, y1: number, v2: number, y2: number): { gain: number; offset: number } {
  if (Math.abs(v2 - v1) < 1e-9) throw new Error('The two readings have the same voltage. Use reference values that are further apart.');
  const gain = (y2 - y1) / (v2 - v1);
  return { gain, offset: y1 - gain * v1 };
}

/** Least-squares polynomial fit, coefficients lowest order first. */
export function fitPolynomial(points: readonly [number, number][], degree: number): number[] {
  const n = degree + 1;
  if (points.length < n) throw new Error(`A degree-${degree} fit needs at least ${n} points.`);
  // Normal equations: (XᵀX) c = Xᵀy
  const A = Array.from({ length: n }, () => new Array<number>(n + 1).fill(0));
  for (const [x, y] of points) {
    const pow = Array.from({ length: 2 * n }, (_, k) => x ** k);
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) A[r][c] += pow[r + c];
      A[r][n] += y * pow[r];
    }
  }
  // Gaussian elimination with partial pivoting
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(A[r][col]) > Math.abs(A[piv][col])) piv = r;
    if (Math.abs(A[piv][col]) < 1e-12) throw new Error('The points do not determine a unique fit. Spread the readings out.');
    [A[col], A[piv]] = [A[piv], A[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = A[r][col] / A[col][col];
      for (let c = col; c <= n; c++) A[r][c] -= f * A[col][c];
    }
  }
  return A.map((row, i) => row[n] / A[i][i]);
}

/** Pneumotach flow (L/min) from differential pressure (Pa). */
export function pneumoFlow(model: FlowModel, pa: number): number {
  return model.k1 * pa + model.k2 * pa * Math.abs(pa);
}

/**
 * Pneumotach constant from a calibration-syringe stroke.
 * @param paSeconds ∫ΔP dt over the stroke, Pa·s (after zeroing)
 * @param volumeL   syringe volume, L (usually 3)
 * @returns k1 in L/min per Pa
 */
export function syringeK(paSeconds: number, volumeL: number): number {
  if (Math.abs(paSeconds) < 1e-6) throw new Error('No pressure was recorded during the stroke. Check the pneumotach tubing and push the syringe again.');
  return (60 * volumeL) / Math.abs(paSeconds);
}
