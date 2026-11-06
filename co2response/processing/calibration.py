"""Volts → engineering units, and the fits used to calibrate a channel."""

from __future__ import annotations

import math

NAN = float("nan")


def apply_calibration(cal: dict, v: float) -> float:
    """Volts → engineering units for one channel."""
    kind = cal["type"]
    if kind == "linear":
        return cal["gain"] * v + cal["offset"]
    if kind == "polynomial":
        y = 0.0
        for c in reversed(cal["coeffs"]):
            y = y * v + c
        return y
    if kind == "table":
        return interpolate_table(cal["points"], v)
    raise ValueError(f"unknown calibration type {kind!r}")


def interpolate_table(points, v: float) -> float:
    """Piecewise-linear interpolation; extrapolates along the first and last segments."""
    p = sorted(points, key=lambda q: q[0])
    if not p:
        return NAN
    if len(p) == 1:
        return p[0][1]
    i = 1
    while i < len(p) - 1 and v > p[i][0]:
        i += 1
    x0, y0 = p[i - 1]
    x1, y1 = p[i]
    if x1 == x0:
        return y0
    return y0 + ((v - x0) * (y1 - y0)) / (x1 - x0)


def invert_calibration(cal: dict, y: float, v_min: float = 0, v_max: float = 5) -> float:
    """Engineering units → volts (the simulator uses this to fake a sensor).

    Exact for linear; bisection over [v_min, v_max] otherwise, which assumes the
    calibration is monotonic over that range.
    """
    if cal["type"] == "linear":
        return NAN if cal["gain"] == 0 else (y - cal["offset"]) / cal["gain"]
    lo, hi = v_min, v_max
    rising = apply_calibration(cal, hi) >= apply_calibration(cal, lo)
    for _ in range(50):
        mid = (lo + hi) / 2
        if (apply_calibration(cal, mid) < y) == rising:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def two_point_linear(v1: float, y1: float, v2: float, y2: float) -> tuple[float, float]:
    """Straight line (gain, offset) through two (volts, value) points, e.g. a zero and a span reading."""
    if abs(v2 - v1) < 1e-9:
        raise ValueError("The two readings have the same voltage. Use reference values that are further apart.")
    gain = (y2 - y1) / (v2 - v1)
    return gain, y1 - gain * v1


def fit_polynomial(points, degree: int) -> list[float]:
    """Least-squares polynomial fit, coefficients lowest order first."""
    n = degree + 1
    if len(points) < n:
        raise ValueError(f"A degree-{degree} fit needs at least {n} points.")
    # normal equations (XᵀX) c = Xᵀy, solved by Gauss-Jordan elimination with partial pivoting
    a = [[0.0] * (n + 1) for _ in range(n)]
    for x, y in points:
        pw = [x**k for k in range(2 * n)]
        for r in range(n):
            for c in range(n):
                a[r][c] += pw[r + c]
            a[r][n] += y * pw[r]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise ValueError("The points do not determine a unique fit. Spread the readings out.")
        a[col], a[piv] = a[piv], a[col]
        for r in range(n):
            if r != col:
                f = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= f * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def pneumo_flow(model: dict, pa: float) -> float:
    """Pneumotach flow (L/min) from differential pressure (Pa): k1·ΔP + k2·ΔP·|ΔP|."""
    return model["k1"] * pa + model["k2"] * pa * abs(pa)


_NO_PRESSURE = "No pressure was recorded during the stroke. Check the pneumotach tubing and push the syringe again."


def syringe_k(pa_seconds: float, volume_l: float) -> float:
    """Pneumotach constant k1 (L/min per Pa) from ∫ΔP dt over a calibration-syringe stroke (Pa·s)."""
    if abs(pa_seconds) < 1e-6:
        raise ValueError(_NO_PRESSURE)
    return (60 * volume_l) / abs(pa_seconds)


def syringe_k1(a1: float, a2: float, volume_l: float, k2: float) -> float:
    """k1 for the full flow model from one syringe stroke, holding k2.

    a1 = ∫ΔP dt (Pa·s), a2 = ∫ΔP·|ΔP| dt (Pa²·s).
    """
    if abs(a1) < 1e-6:
        raise ValueError(_NO_PRESSURE)
    return (60 * volume_l * math.copysign(1, a1) - k2 * a2) / a1
