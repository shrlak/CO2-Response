"""2nd-order Butterworth low-pass (RBJ biquad), one per channel."""

from __future__ import annotations

import math


class LowPass:
    def __init__(self, cutoff_hz: float, sample_hz: float) -> None:
        fc = min(cutoff_hz, sample_hz * 0.45)
        w0 = (2 * math.pi * fc) / sample_hz
        alpha = math.sin(w0) / (2 * math.sqrt(0.5))  # Q = 1/√2
        cos = math.cos(w0)
        a0 = 1 + alpha
        self.b0 = (1 - cos) / 2 / a0
        self.b1 = (1 - cos) / a0
        self.b2 = (1 - cos) / 2 / a0
        self.a1 = (-2 * cos) / a0
        self.a2 = (1 - alpha) / a0
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0
        self.primed = False

    def process(self, x: float) -> float:
        if not math.isfinite(x):
            self.primed = False
            return x
        if not self.primed:
            # start at steady state so a step at connect does not ring
            self.x1 = self.x2 = self.y1 = self.y2 = x
            self.primed = True
        y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        return y

    def reset(self) -> None:
        self.primed = False
