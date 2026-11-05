"""Volts in, engineering units and breaths out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..channels import CHANNEL_IDS, DEFAULT_FLOW_MODEL
from ..settings import ProcessingOptions
from .breath import Breath, BreathDetector, BreathOptions
from .calibration import apply_calibration, pneumo_flow
from .co2 import pct_to_mmhg
from .filter import LowPass
from .quality import RailMonitor, at_rail

NAN = float("nan")

DEFAULT_PROCESSING = ProcessingOptions()


@dataclass
class Processed:
    t_sec: float
    volts: list[float]
    co2_pct: float
    co2_mmhg: float
    flow_lpm: float
    pneumo_pa: float
    pneumo_lpm: float
    #: False when the channel is at a rail (stuck) or not a number
    valid: dict[str, bool] = field(default_factory=dict)
    stuck: dict[str, bool] = field(default_factory=dict)
    breath: Breath | None = None


class Pipeline:
    """Calibrate → filter → CO2 units → pneumotach flow → breath detection.

    Pure apart from its own filter and detector state.
    """

    def __init__(self, channels: dict, opts: ProcessingOptions | None = None) -> None:
        self.channels = channels
        self.opts = opts or ProcessingOptions()
        self.detector = BreathDetector(BreathOptions(self.opts.deadband_lpm, self.opts.min_breath_sec))
        self._rebuild()

    def _rebuild(self) -> None:
        o = self.opts
        a = self.channels["adc"]
        self.filters = {cid: LowPass(o.filter_hz, o.sample_rate_hz) for cid in CHANNEL_IDS}
        self.rails = {
            cid: RailMonitor(a["railLowV"], a["railHighV"], a["railToleranceV"], 2, self.channels["channels"][cid].get("railCheck", "both"))
            for cid in CHANNEL_IDS
        }
        self.detector.opts = BreathOptions(o.deadband_lpm, o.min_breath_sec)

    def set_channels(self, c: dict) -> None:
        self.channels = c
        self._rebuild()

    def set_options(self, o: ProcessingOptions) -> None:
        self.opts = o
        self._rebuild()

    def process(self, t_ms: float, volts: list[float]) -> Processed:
        t_sec = t_ms / 1000
        a = self.channels["adc"]
        chans = self.channels["channels"]
        valid: dict[str, bool] = {}
        stuck: dict[str, bool] = {}
        val: dict[str, float] = {}

        for cid in CHANNEL_IDS:
            ch = chans[cid]
            i = int(ch["index"])
            v = volts[i] if i < len(volts) else NAN
            ok = math.isfinite(v)
            stuck[cid] = self.rails[cid].push(t_sec, v) if ok else False
            # A sample at the rail is not trusted even before the 2 s hold elapses.
            rail = ok and at_rail(v, a["railLowV"], a["railHighV"], a["railToleranceV"], ch.get("railCheck", "both"))
            valid[cid] = ok and not rail
            y = apply_calibration(ch["calibration"], v) - ch["zeroOffset"] if valid[cid] else NAN
            if self.opts.filter_on:
                if valid[cid]:
                    y = self.filters[cid].process(y)
                else:
                    self.filters[cid].reset()
                    y = NAN
            val[cid] = y

        co2_pct = val["co2"]
        co2_mmhg = pct_to_mmhg(co2_pct, self.opts.barometric_mm_hg, self.opts.water_vapor)
        model = chans["pneumo"].get("flowModel") or DEFAULT_FLOW_MODEL
        pneumo_lpm = pneumo_flow(model, val["pneumo"])
        breath = self.detector.push(t_sec, pneumo_lpm, co2_mmhg)
        return Processed(t_sec, volts, co2_pct, co2_mmhg, val["flow"], val["pneumo"], pneumo_lpm, valid, stuck, breath)
