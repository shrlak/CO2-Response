"""Volts in; cleaned engineering units and screened breaths out.

    raw volts ─► despike (Hampel) ─► calibrate ─► low-pass ─► CO2 units / pneumotach flow
                                         │                          │
                                         └─► noise meters           └─► breath detection ─► breath screen

Everything that is interpreted (breaths, EtCO2, RR, VT, MV, alarms, trend) comes after
the cleaning. The raw volts travel alongside untouched, so recordings keep them and
can be re-analysed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..channels import CHANNEL_IDS, DEFAULT_FLOW_MODEL
from ..settings import ProcessingOptions
from .breath import Breath, BreathDetector, BreathOptions
from .calibration import apply_calibration, pneumo_flow
from .co2 import pct_to_mmhg
from .denoise import BreathScreen, Despiker, NoiseMeter
from .filter import LowPass
from .quality import RailMonitor, at_rail

NAN = float("nan")

DEFAULT_PROCESSING = ProcessingOptions()


@dataclass
class Processed:
    t_sec: float
    #: the raw volts of this sample, as the device sent them
    volts: list[float]
    co2_pct: float
    co2_mmhg: float
    flow_lpm: float
    pneumo_pa: float
    pneumo_lpm: float
    #: False when the channel is at a rail (stuck) or not a number
    valid: dict[str, bool] = field(default_factory=dict)
    stuck: dict[str, bool] = field(default_factory=dict)
    #: a breath that finished on this sample, already screened (see ``Breath.quality``)
    breath: Breath | None = None
    #: an earlier breath that this sample's breath showed to be an isolated outlier
    outlier: Breath | None = None
    #: device time and sequence number of this sample
    t_ms: float = 0.0
    seq: int = 0
    #: the volts after spike removal (what the numbers are computed from)
    clean: list[float] = field(default_factory=list)


class Pipeline:
    """Calibrate → filter → CO2 units → pneumotach flow → breath detection, with the
    noise removal of ``denoise.py`` in front.

    With spike removal on, each call returns the sample from 3 calls earlier (the
    filter needs to see both sides of a sample), and None for the first 3 calls.
    Pure apart from its own filter and detector state.
    """

    DESPIKE_HALF_WINDOW = 3

    def __init__(self, channels: dict, opts: ProcessingOptions | None = None) -> None:
        self.channels = channels
        self.opts = opts or ProcessingOptions()
        self.detector = BreathDetector(BreathOptions(self.opts.deadband_lpm, self.opts.min_breath_sec))
        self.screen = BreathScreen(self.opts.reject_bad_breaths)
        self.despiker: Despiker | None = None
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
        self.screen.enabled = o.reject_bad_breaths
        want = self.DESPIKE_HALF_WINDOW if o.despike else 0
        if (self.despiker.h if self.despiker else 0) != want:
            self.despiker = Despiker(want) if want else None
        self.noise = {"co2": NoiseMeter(), "flow": NoiseMeter()}

    def set_channels(self, c: dict) -> None:
        self.channels = c
        self._rebuild()

    def set_options(self, o: ProcessingOptions) -> None:
        self.opts = o
        self._rebuild()

    @property
    def delay(self) -> int:
        """How many samples the output lags the input."""
        return self.despiker.h if self.despiker else 0

    def process(self, t_ms: float, volts: list[float], seq: int = 0) -> Processed | None:
        """Feed one sample; get back a processed sample (with spike removal on, the one
        from 3 calls earlier; None for the first 3 calls)."""
        if self.despiker is None:
            return self._process(t_ms, seq, volts, list(volts))
        out = self.despiker.push(volts, (t_ms, seq))
        if out is None:
            return None
        (t_ms, seq), volts, clean = out
        return self._process(t_ms, seq, volts, clean)

    def flush(self) -> list[Processed]:
        """The samples the spike filter still holds (call when a stream ends)."""
        if self.despiker is None:
            return []
        return [self._process(t, s, v, c) for (t, s), v, c in self.despiker.flush()]

    def _process(self, t_ms: float, seq: int, volts: list[float], clean: list[float]) -> Processed:
        t_sec = t_ms / 1000
        a = self.channels["adc"]
        chans = self.channels["channels"]
        valid: dict[str, bool] = {}
        stuck: dict[str, bool] = {}
        val: dict[str, float] = {}
        unfiltered: dict[str, float] = {}

        for cid in CHANNEL_IDS:
            ch = chans[cid]
            i = int(ch["index"])
            v = clean[i] if i < len(clean) else NAN
            ok = math.isfinite(v)
            stuck[cid] = self.rails[cid].push(t_sec, v) if ok else False
            # A sample at the rail is not trusted even before the 2 s hold elapses.
            rail = ok and at_rail(v, a["railLowV"], a["railHighV"], a["railToleranceV"], ch.get("railCheck", "both"))
            valid[cid] = ok and not rail
            y = apply_calibration(ch["calibration"], v) - ch["zeroOffset"] if valid[cid] else NAN
            unfiltered[cid] = y
            if self.opts.filter_on:
                if valid[cid]:
                    y = self.filters[cid].process(y)
                else:
                    self.filters[cid].reset()
                    y = NAN
            val[cid] = y

        p = self.opts
        model = chans["pneumo"].get("flowModel") or DEFAULT_FLOW_MODEL
        # noise is measured before the low-pass filter, which would hide it
        self.noise["co2"].push(pct_to_mmhg(unfiltered["co2"], p.barometric_mm_hg, p.water_vapor))
        self.noise["flow"].push(pneumo_flow(model, unfiltered["pneumo"]))

        co2_pct = val["co2"]
        co2_mmhg = pct_to_mmhg(co2_pct, p.barometric_mm_hg, p.water_vapor)
        pneumo_lpm = pneumo_flow(model, val["pneumo"])
        breath = self.detector.push(t_sec, pneumo_lpm, co2_mmhg)
        outlier = None
        if breath is not None:
            breath.noise_co2 = self.noise["co2"].take()
            breath.noise_flow = self.noise["flow"].take() if breath.detected_by == "flow" else NAN
            outlier = self.screen.screen(breath)
        return Processed(t_sec, volts, co2_pct, co2_mmhg, val["flow"], val["pneumo"], pneumo_lpm, valid, stuck, breath, outlier, t_ms, seq, clean)
