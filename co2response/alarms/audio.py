"""Alarm tones, patterned after IEC 60601-1-8: high priority = 10 pulses (5 + 5)
repeated every 5 s, medium = 3 pulses every 15 s.

Tones are synthesized with numpy and played with ``sounddevice`` (PortAudio). If that
is not installed or there is no output device, a fallback beeper can be supplied (the
UI passes the system beep), so an alarm still makes some sound.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

SAMPLE_RATE = 44100


def pulse_train(
    freq: float,
    count: int,
    group_of: int,
    volume: float = 0.8,
    pulse_sec: float = 0.15,
    gap_sec: float = 0.1,
    group_gap_sec: float = 0.35,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Square-wave pulses with short ramps (no clicks), as float32 samples in [-1, 1]."""
    peak = 0.25 * max(0.1, min(1.0, volume))
    starts = []
    t = 0.02
    for i in range(count):
        starts.append(t)
        t += pulse_sec + gap_sec + (group_gap_sec if (i + 1) % group_of == 0 else 0)
    total = int((starts[-1] + pulse_sec + 0.05) * sample_rate)
    out = np.zeros(total, dtype=np.float32)
    n = int(pulse_sec * sample_rate)
    tt = np.arange(n) / sample_rate
    square = np.sign(np.sin(2 * np.pi * freq * tt))
    env = np.minimum(1.0, np.minimum(tt / 0.015, (pulse_sec - tt) / 0.02)).clip(0, 1)
    pulse = (peak * square * env).astype(np.float32)
    for s in starts:
        i0 = int(s * sample_rate)
        out[i0 : i0 + n] += pulse[: max(0, min(n, total - i0))]
    return out


class AlarmAudio:
    def __init__(self, fallback_beep: Callable[[], None] | None = None) -> None:
        self.volume = 0.8
        self.armed = False
        #: how the tones are made: 'speaker', 'system beep' or 'none'
        self.backend = "none"
        self.error = ""
        self._sd = None
        self._fallback = fallback_beep

    def arm(self, confirm: bool = True) -> bool:
        """Find a sound output. Returns True when alarm tones can be played."""
        try:
            import sounddevice as sd

            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
            self._sd = sd
            self.backend = "speaker"
            self.error = ""
        except Exception as e:  # missing PortAudio, no device, device busy
            self._sd = None
            self.error = str(e) or type(e).__name__
            self.backend = "system beep" if self._fallback else "none"
        self.armed = self.backend != "none"
        if self.armed and confirm:
            self.confirm()
        return self.armed

    def _play(self, buf: np.ndarray, beeps: int) -> None:
        if self._sd is not None:
            try:
                self._sd.play(buf, SAMPLE_RATE, blocking=False)
                return
            except Exception as e:
                self.error = str(e)
                self._sd = None
                self.backend = "system beep" if self._fallback else "none"
                self.armed = self.backend != "none"
        if self._fallback:
            for _ in range(beeps):
                self._fallback()

    def high(self) -> None:
        self._play(pulse_train(960, 10, 5, self.volume), 3)

    def medium(self) -> None:
        self._play(pulse_train(660, 3, 3, self.volume, 0.2, 0.15), 1)

    def confirm(self) -> None:
        """Short chirp after arming."""
        self._play(pulse_train(880, 1, 1, self.volume, 0.12), 1)


class SilentAudio:
    """Stand-in used by tests and scripts: records which tones would have played."""

    def __init__(self, armed: bool = True) -> None:
        self.volume = 0.8
        self.armed = armed
        self.backend = "test" if armed else "none"
        self.error = ""
        self.played: list[str] = []

    def arm(self, confirm: bool = True) -> bool:
        self.armed = True
        if confirm:
            self.played.append("confirm")
        return True

    def high(self) -> None:
        self.played.append("high")

    def medium(self) -> None:
        self.played.append("medium")

    def confirm(self) -> None:
        self.played.append("confirm")
