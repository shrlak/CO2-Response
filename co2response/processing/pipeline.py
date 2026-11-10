"""Volts in; cleaned engineering units and screened breaths out.

    raw volts ─► despike (Hampel) ─► calibrate ─► low-pass ─► CO2 units / pneumotach flow
                                         │                          │
                                         └─► noise meters           ├─► align CO2 with flow (CO2 delay)
                                                                    └─► breath detection ─► breath screen

Everything that is interpreted (breaths, EtCO2, RR, VT, MV, alarms, trend) comes after
the cleaning. The raw volts travel alongside untouched, so recordings keep them and
can be re-analysed.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field

from ..channels import CHANNEL_IDS, DEFAULT_FLOW_MODEL
from ..settings import ProcessingOptions, Spo2Settings
from .breath import Breath, BreathDetector, BreathOptions
from .calibration import apply_calibration, pneumo_flow
from .co2 import btps_factor, pct_to_mmhg
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
    #: oxygen saturation, % (NaN without an oximeter, or while it has no reading)
    spo2: float = NAN


class Pipeline:
    """Calibrate → filter → CO2 units → pneumotach flow → breath detection, with the
    noise removal of ``denoise.py`` in front.

    With spike removal on, each call returns the sample from 3 calls earlier (the
    filter needs to see both sides of a sample), and None for the first 3 calls.
    Pure apart from its own filter and detector state.
    """

    DESPIKE_HALF_WINDOW = 3

    #: an oximeter reading outside this is "no reading" (probe off, searching), not a saturation
    SPO2_RANGE = (30.0, 100.5)

    def __init__(self, channels: dict, opts: ProcessingOptions | None = None, spo2: Spo2Settings | None = None) -> None:
        self.channels = channels
        self.opts = opts or ProcessingOptions()
        #: the SpO2 safety channel (a pulse oximeter on the spare input), if there is one
        self.spo2 = spo2
        self._sat = NAN
        self.detector = BreathDetector(BreathOptions(self.opts.deadband_lpm, self.opts.min_breath_sec))
        self.screen = BreathScreen(self.opts.reject_bad_breaths)
        self.despiker: Despiker | None = None
        #: True between the Rebreathing and End-of-test markers (set by the engine):
        #: the bag's gas mix is in the pneumotach, so its gas factor applies
        self.rebreathing = False
        self._held: collections.deque = collections.deque()
        self._hold = 0
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
        # CO2 arrives late (sample line, analyser): the flow channels wait for it
        hold = max(0, round(o.co2_delay_ms / 1000 * o.sample_rate_hz))
        if hold != self._hold:
            self._hold = hold
            self._held.clear()
        self._btps = btps_factor(o.ambient_temp_c, o.barometric_mm_hg) if o.volume_btps else 1.0

    @property
    def flow_factor(self) -> float:
        """What the pneumotach flow is multiplied by: gas conditions (BTPS) and, while
        rebreathing, the bag gas."""
        return self._btps * (self.opts.rebreathing_gas_factor if self.rebreathing else 1.0)

    @property
    def co2_delay_sec(self) -> float:
        """The CO2 delay actually applied (a whole number of samples)."""
        return self._hold / self.opts.sample_rate_hz

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

        if self._hold:
            # each CO2 sample is paired with the flow from co2_delay_ms earlier: the gas
            # it measured left the airway then. Raw volts are recorded untouched.
            self._held.append((val["flow"], val["pneumo"], valid["flow"], valid["pneumo"]))
            if len(self._held) > self._hold:
                val["flow"], val["pneumo"], valid["flow"], valid["pneumo"] = self._held.popleft()
            else:
                val["flow"] = val["pneumo"] = NAN
                valid["flow"] = valid["pneumo"] = False

        co2_pct = val["co2"]
        co2_mmhg = pct_to_mmhg(co2_pct, p.barometric_mm_hg, p.water_vapor)
        pneumo_lpm = pneumo_flow(model, val["pneumo"]) * self.flow_factor
        breath = self.detector.push(t_sec, pneumo_lpm, co2_mmhg)
        outlier = None
        if breath is not None:
            breath.noise_co2 = self.noise["co2"].take()
            breath.noise_flow = self.noise["flow"].take() if breath.detected_by == "flow" else NAN
            outlier = self.screen.screen(breath)
        return Processed(t_sec, volts, co2_pct, co2_mmhg, val["flow"], val["pneumo"], pneumo_lpm, valid, stuck, breath, outlier, t_ms, seq, clean, self._saturation(clean))

    def _saturation(self, clean: list[float]) -> float:
        """SpO2 % from the oximeter's volts, smoothed over about a second."""
        o = self.spo2
        if o is None or not o.enabled or o.index >= len(clean) or not math.isfinite(clean[o.index]):
            self._sat = NAN
            return NAN
        v = o.gain_pct_per_v * clean[o.index] + o.offset_pct
        if not (self.SPO2_RANGE[0] <= v <= self.SPO2_RANGE[1]):
            self._sat = NAN
            return NAN
        v = min(100.0, v)
        self._sat = v if math.isnan(self._sat) else self._sat + (v - self._sat) / max(1.0, self.opts.sample_rate_hz)
        return self._sat
