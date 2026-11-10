"""Alarm state machine. Pure: time (wall-clock ms) is always passed in.

- Physiological CO2 alarms need ``delay_breaths`` consecutive breaths (EtCO2 source)
  or ``delay_sec`` continuous seconds (live source) beyond the limit.
- They clear only past the limit by ``hysteresis_mm_hg``.
- Two tiers: ``high_mm_hg`` is the warning and ``stop_mm_hg`` the stop limit. The stop
  limit is checked on every sample (CO2 at or above it for ``stop_delay_sec``) and on
  every breath, so it does not wait for a second breath. While a stop limit is set,
  the warning is medium priority and the stop is high, so the two sound different.
- A new high-priority alarm cancels a silence: silencing the warning never mutes the
  stop alarm that follows it.
- High and medium alarms latch: once the condition clears they stay, marked resolved,
  until acknowledged. Advisories come and go on their own.
- Silence mutes audio for ``silence_sec`` (≤ 120 s) and then re-arms by itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ..jsfmt import js_num, to_fixed
from ..settings import AlarmSettings

# Alarm ids: co2Stop co2High co2Low apnea noData deviceLost adcError railCo2 railFlow
#            railPneumo testTime test audioOff dropped checksum leak calibration settingsSave
PRIORITIES = ("high", "medium", "advisory")
_RANK = {"high": 0, "medium": 1, "advisory": 2}


@dataclass
class AlarmRecord:
    id: str
    priority: str  # 'high' | 'medium' | 'advisory'
    message: str
    state: str  # 'active' | 'resolved'
    #: wall-clock ms
    since: float
    resolved_at: float | None = None
    acknowledged: bool = False
    value: float | None = None
    peak: float | None = None


@dataclass
class AlarmLogEntry:
    t: float
    #: 'start' | 'resolved' | 'acknowledged' | 'cleared' | 'silenced' | 'unsilenced' | 'rearmed'
    kind: str
    message: str
    id: str | None = None
    priority: str | None = None


class AlarmEngine:
    def __init__(self, settings: AlarmSettings, log: Callable[[AlarmLogEntry], None] | None = None) -> None:
        self.settings = settings
        self._log = log or (lambda e: None)
        self._records: dict[str, AlarmRecord] = {}
        self._high_count = 0
        self._low_count = 0
        self._high_since: float | None = None
        self._low_since: float | None = None
        self._stop_since: float | None = None
        self._stop_clear_since: float | None = None
        self._silenced_until = 0.0

    def set_settings(self, s: AlarmSettings, now: float) -> None:
        self.settings = s
        if s.stop_mm_hg is None:
            self._stop_since = self._stop_clear_since = None
            if self.is_active("co2Stop"):
                self._resolve("co2Stop", now)
        if s.low_mm_hg is None:
            self._low_count = 0
            self._low_since = None
            low = self._records.get("co2Low")
            if low and low.state == "active":
                self._resolve("co2Low", now)

    # ---------- generic conditions ----------

    def set_condition(self, aid: str, active: bool, t: float, priority: str, message: str, value: float | None = None) -> None:
        """Raise or clear a condition. High/medium latch on clear; advisories just disappear."""
        r = self._records.get(aid)
        if active:
            if r is None or r.state == "resolved":
                self._records[aid] = AlarmRecord(aid, priority, message, "active", t, value=value, peak=value)
                if priority == "high" and aid != "test" and self.is_silenced(t):
                    # a silence covers the alarms that were sounding, not a new, worse one
                    self._silenced_until = 0
                    self._log(AlarmLogEntry(t, "unsilenced", "Silence cancelled by a new high-priority alarm"))
                if priority != "advisory":
                    self._log(AlarmLogEntry(t, "start", message, aid, priority))
            else:
                r.message = message
                r.value = value
                if value is not None:
                    if r.peak is None:
                        r.peak = value
                    else:
                        r.peak = min(r.peak, value) if aid == "co2Low" else max(r.peak, value)
        elif r is not None and r.state == "active":
            self._resolve(aid, t)

    def _resolve(self, aid: str, t: float) -> None:
        r = self._records.get(aid)
        if r is None:
            return
        if r.priority == "advisory" or aid == "test":
            del self._records[aid]
            return
        r.state = "resolved"
        r.resolved_at = t
        self._log(AlarmLogEntry(t, "resolved", r.message, aid, r.priority))

    # ---------- CO2 limits ----------

    def _high_msg(self, v: float) -> str:
        return f"EtCO2 HIGH {to_fixed(v, 0)} mmHg > {js_num(self.settings.high_mm_hg)}"

    def _low_msg(self, v: float) -> str:
        return f"EtCO2 LOW {to_fixed(v, 0)} mmHg < {js_num(self.settings.low_mm_hg)}"

    @property
    def _high_priority(self) -> str:
        """The warning limit is medium priority while a stop limit stands behind it."""
        return "medium" if self.settings.stop_mm_hg is not None else "high"

    def _stop_msg(self, v: float, what: str = "CO2") -> str:
        return f"STOP TEST: {what} {to_fixed(v, 0)} mmHg at the stop limit {js_num(self.settings.stop_mm_hg)}: turn the valve to room air"

    def _check_stop_sample(self, co2: float, t: float) -> None:
        """The stop limit on the live CO2: every sample, whatever the alarm source."""
        s = self.settings
        if s.stop_mm_hg is None:
            return
        active = self.is_active("co2Stop")
        if co2 >= s.stop_mm_hg:
            if self._stop_since is None:
                self._stop_since = t
            self._stop_clear_since = None
            if active or t - self._stop_since >= s.stop_delay_sec * 1000:
                self.set_condition("co2Stop", True, t, "high", self._stop_msg(co2), co2)
        else:
            self._stop_since = None
            # CO2 falls on every inspiration, so the alarm is resolved by a breath back
            # under the limit (on_breath), or by 20 s without a sample near it
            if active and co2 < s.stop_mm_hg - s.hysteresis_mm_hg:
                if self._stop_clear_since is None:
                    self._stop_clear_since = t
                elif t - self._stop_clear_since >= 20000:
                    self._resolve("co2Stop", t)
            else:
                self._stop_clear_since = None

    def _check_stop_breath(self, etco2: float, t: float) -> None:
        s = self.settings
        if s.stop_mm_hg is None:
            return
        if etco2 >= s.stop_mm_hg:
            self.set_condition("co2Stop", True, t, "high", self._stop_msg(etco2, "EtCO2"), etco2)
        elif self.is_active("co2Stop") and etco2 < s.stop_mm_hg - s.hysteresis_mm_hg:
            self._resolve("co2Stop", t)

    def on_breath(self, etco2: float, t: float) -> None:
        """Call once per detected breath with its EtCO2 (mmHg)."""
        s = self.settings
        if not math.isfinite(etco2):
            return
        self._check_stop_breath(etco2, t)
        if s.source != "etco2":
            return
        pri = self._high_priority
        high = self._records.get("co2High")
        high_active = high is not None and high.state == "active"
        if etco2 > s.high_mm_hg:
            self._high_count += 1
            if self._high_count >= s.delay_breaths or high_active:
                self.set_condition("co2High", True, t, pri, self._high_msg(etco2), etco2)
        else:
            self._high_count = 0
            if high_active:
                if etco2 < s.high_mm_hg - s.hysteresis_mm_hg:
                    self._resolve("co2High", t)
                else:
                    self.set_condition("co2High", True, t, pri, self._high_msg(etco2), etco2)

        if s.low_mm_hg is not None:
            low = self._records.get("co2Low")
            low_active = low is not None and low.state == "active"
            if etco2 < s.low_mm_hg:
                self._low_count += 1
                if self._low_count >= s.delay_breaths or low_active:
                    self.set_condition("co2Low", True, t, "medium", self._low_msg(etco2), etco2)
            else:
                self._low_count = 0
                if low_active:
                    if etco2 > s.low_mm_hg + s.hysteresis_mm_hg:
                        self._resolve("co2Low", t)
                    else:
                        self.set_condition("co2Low", True, t, "medium", self._low_msg(etco2), etco2)

    def on_live_co2(self, co2: float, t: float) -> None:
        """Call on every sample with the live CO2 (mmHg). The stop limit is always
        checked; the warning and low limits only when the alarm source is the waveform."""
        s = self.settings
        if not math.isfinite(co2):
            return
        self._check_stop_sample(co2, t)
        if s.source != "live":
            return
        ms = s.delay_sec * 1000
        pri = self._high_priority

        def live(v: float, hi: bool) -> str:
            lim = s.high_mm_hg if hi else s.low_mm_hg
            return f"CO2 {'HIGH' if hi else 'LOW'} {to_fixed(v, 0)} mmHg {'>' if hi else '<'} {js_num(lim)}"

        high = self._records.get("co2High")
        high_active = high is not None and high.state == "active"
        if co2 > s.high_mm_hg:
            if self._high_since is None:
                self._high_since = t
            if t - self._high_since >= ms or high_active:
                self.set_condition("co2High", True, t, pri, live(co2, True), co2)
        else:
            self._high_since = None
            if high_active and co2 < s.high_mm_hg - s.hysteresis_mm_hg:
                self._resolve("co2High", t)

        if s.low_mm_hg is not None:
            low = self._records.get("co2Low")
            low_active = low is not None and low.state == "active"
            if co2 < s.low_mm_hg:
                if self._low_since is None:
                    self._low_since = t
                if t - self._low_since >= ms or low_active:
                    self.set_condition("co2Low", True, t, "medium", live(co2, False), co2)
            else:
                self._low_since = None
                if low_active and co2 > s.low_mm_hg + s.hysteresis_mm_hg:
                    self._resolve("co2Low", t)

    # ---------- operator actions ----------

    def acknowledge(self, t: float, aid: str | None = None) -> None:
        """Acknowledge an alarm (the top one if no id). A resolved alarm is cleared; an
        active one is marked acknowledged and the audio is silenced."""
        if aid is not None:
            r = self._records.get(aid)
        else:
            r = next((x for x in self.list() if x.priority != "advisory"), None)
        if r is None or r.priority == "advisory":
            return
        if r.state == "resolved":
            del self._records[r.id]
            self._log(AlarmLogEntry(t, "cleared", r.message, r.id, r.priority))
        else:
            r.acknowledged = True
            self._log(AlarmLogEntry(t, "acknowledged", r.message, r.id, r.priority))
            self.silence(t)

    def _silence_sec(self) -> float:
        return min(120, self.settings.silence_sec)

    def silence(self, t: float) -> None:
        self._silenced_until = t + self._silence_sec() * 1000
        self._log(AlarmLogEntry(t, "silenced", f"Audio silenced for {js_num(self._silence_sec())} s"))

    def unsilence(self, t: float) -> None:
        if self._silenced_until <= t:
            return
        self._silenced_until = 0
        self._log(AlarmLogEntry(t, "unsilenced", "Silence cancelled"))

    def tick(self, t: float) -> None:
        """Call regularly (e.g. 4 Hz) so an expired silence re-arms and is logged."""
        if self._silenced_until and t >= self._silenced_until:
            self._silenced_until = 0
            self._log(AlarmLogEntry(t, "rearmed", "Silence expired; audio re-armed"))

    def is_silenced(self, t: float) -> bool:
        return t < self._silenced_until

    def silence_remaining_ms(self, t: float) -> float:
        return max(0, self._silenced_until - t)

    def get(self, aid: str) -> AlarmRecord | None:
        return self._records.get(aid)

    def is_active(self, aid: str) -> bool:
        r = self._records.get(aid)
        return r is not None and r.state == "active"

    def remove(self, aid: str) -> None:
        self._records.pop(aid, None)

    def list(self) -> list[AlarmRecord]:
        """Display order: active high, active medium, then latched (resolved but not yet
        acknowledged) high and medium, then advisories, newest first within each group.
        A latched alarm still needs someone to see it, so it outranks any advisory."""

        def group(r: AlarmRecord) -> int:
            return 4 if r.priority == "advisory" else (0 if r.state == "active" else 2) + _RANK[r.priority]

        return sorted(self._records.values(), key=lambda r: (group(r), -r.since))

    def active_priority(self) -> str | None:
        """Highest priority among active alarms, used to pick the tone."""
        best = None
        for r in self._records.values():
            if r.state != "active" or r.priority == "advisory":
                continue
            if best is None or _RANK[r.priority] < _RANK[best]:
                best = r.priority
        return best

    def reset(self) -> None:
        self._records.clear()
        self._high_count = self._low_count = 0
        self._high_since = self._low_since = None
        self._stop_since = self._stop_clear_since = None
