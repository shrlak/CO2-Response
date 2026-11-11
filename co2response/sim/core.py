"""Emulates the firmware: produces protocol lines sample by sample, including the
corrupt and missing lines the "noisy" scenario injects."""

from __future__ import annotations

import math
from typing import Callable, NamedTuple

from ..protocol import format_data_line
from .physiology import PhysioModel, mulberry32


class Encoders(NamedTuple):
    """Physical value → volts for each channel (the inverse of the channel calibration)."""

    co2: Callable[[float], float]
    flow: Callable[[float], float]
    pneumo: Callable[[float], float]
    #: SpO2 % → volts; None = no oximeter, so data lines carry three values
    spo2: Callable[[float], float] | None = None


FIRMWARE_INFO = "I,FW,sim-1.2.0,ADC,SIMULATED,CH,3,VALVE,1,RATE,"
#: the Uno R4 board with digital sensors (firmware/CO2ResponseR4): its data lines carry
#: % CO2 and L/min, which UNITS tells the host
DIGITAL_INFO = "I,FW,sim-2.0.0,BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR,FLOW,SFM3304,CH,2,VALVE,1,REED,1,ALARM,1,RATE,"
#: what that board sends for a sensor that is not answering
NOT_ANSWERING = 9999.0
#: the SprintIR reports a new CO2 value this often; the board repeats it in between
CO2_UPDATE_HZ = 20
#: measuring ranges of the digital sensors: % CO2, and L/min either way
CO2_RANGE_PCT = (0.0, 20.0)
FLOW_RANGE_LPM = 250.0


class SimulatorCore:
    def __init__(self, encoders: Encoders, rail_low_v: float = 0, rail_high_v: float = 5, digital: bool = False, **params) -> None:
        self.encoders = encoders
        #: emulate the Uno R4 board with digital sensors (units on the line) instead of
        #: the ADC board (volts on the line)
        self.digital = digital
        self._co2_held = 0.0
        self._co2_age = 0.0
        self.model = PhysioModel(**params)
        self.rate_hz = 100
        self.streaming = True
        self._seq = 0
        self._t_ms = 0.0
        self._rand = mulberry32(params.get("seed", 1) + 99)
        self._rail_low_v = rail_low_v
        self._rail_high_v = rail_high_v
        #: the emulated valve output: True = to the bag (the host's VALVE command)
        self.valve_to_bag = False

    def _clamp(self, v: float) -> float:
        return min(self._rail_high_v, max(self._rail_low_v, v))

    def next_sample(self) -> list[str]:
        """The line(s) for one sample period. May be empty (dropped or stopped)."""
        dt = 1 / self.rate_hz
        s = self.model.step(dt)
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        self._t_ms += 1000 * dt
        p = self.model.params
        if not self.streaming or p.stop_data_on:
            return []

        if self.digital:
            volts = self._digital_values(s, p, dt)
            return self._send(seq, volts, p, spike_v=(1.0, 10.0))

        # an unplugged sensor: the recommended pull-up drives its input to the high rail
        volts = [
            self._rail_high_v if s.co2_pct is None else self._clamp(self.encoders.co2(s.co2_pct)),
            self._rail_high_v if p.flow_disconnected else self._clamp(self.encoders.flow(s.flow_lpm)),
            self._rail_high_v if p.pneumo_disconnected else self._clamp(self.encoders.pneumo(s.pneumo_pa)),
        ]
        if self.encoders.spo2 is not None:  # an oximeter on the fourth input
            volts.append(self._clamp(self.encoders.spo2(s.spo2_pct)))
        return self._send(seq, volts, p)

    def _digital_values(self, s, p, dt: float) -> list[float]:
        """What the Uno R4 board puts on a line: % CO2 as the SprintIR last reported it
        (20 times a second) and the SFM3304's flow in L/min. One flow sensor, so either
        "unplugged" switch of the panel unplugs it."""
        self._co2_age += dt
        if self._co2_age >= 1 / CO2_UPDATE_HZ - 1e-9:
            self._co2_age = 0.0
            self._co2_held = NOT_ANSWERING if s.co2_pct is None else min(CO2_RANGE_PCT[1], max(CO2_RANGE_PCT[0], s.co2_pct))
        flow = NOT_ANSWERING if p.flow_disconnected or p.pneumo_disconnected else min(FLOW_RANGE_LPM, max(-FLOW_RANGE_LPM, s.flow_lpm))
        values = [self._co2_held, flow]
        if self.encoders.spo2 is not None:  # an oximeter is not part of this board: its volts ride along as value 3
            values += [0.0, self.encoders.spo2(s.spo2_pct)]
        return values

    def _send(self, seq: int, volts: list[float], p, spike_v: tuple[float, ...] = (1.0, 1.0, 1.0)) -> list[str]:
        """One data line, with the spikes and line errors the panel asks for."""
        spikes = max(0.01 if p.scenario == "artifacts" else 0, p.spike_pct / 100)
        if spikes > 0 and self._rand() < spikes:
            # a glitchy conversion: one sample on one channel jumps (by 1–2 V on the ADC board)
            n = len(spike_v)
            ch = min(n - 1, int(self._rand() * n))
            if volts[ch] != NOT_ANSWERING or not self.digital:
                jump = spike_v[ch] * (1 + self._rand()) * (1 if self._rand() < 0.5 else -1)
                volts[ch] = volts[ch] + jump if self.digital else self._clamp(volts[ch] + jump)
        line = format_data_line(seq, self._t_ms, volts)

        # lost and corrupted lines: the "noisy" scenario drops 0.5 % and corrupts 1 %
        drop, bad = (0.005, 0.015) if p.scenario == "noisy" else (0.0, 0.0)
        if p.line_error_pct / 100 > bad:
            bad = p.line_error_pct / 100
            drop = bad / 3
        if bad > 0:
            r = self._rand()
            if r < drop:
                return []  # dropped sample → sequence gap
            if r < bad:
                line = line[:-2] + ("01" if line.endswith("00") else "00")  # bad checksum
        return [line]

    def command(self, cmd: str) -> list[str]:
        """Reply to a host command the way the firmware does."""
        parts = cmd.strip().upper().split()
        name = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if name == "HB":
            # the host's heartbeat (firmware with a watchdog): no reply unless it is switched off
            return ["A,HB,OFF"] if arg == "OFF" else []
        if name == "VALVE":
            if arg not in ("0", "1"):
                return ["E,UNKNOWN_CMD"]
            self.valve_to_bag = arg == "1"
            return [f"A,VALVE,{arg}"]
        if name == "INFO":
            if self.digital:
                info = DIGITAL_INFO.replace("CH,2", "CH,4") if self.encoders.spo2 is not None else DIGITAL_INFO
            else:
                info = FIRMWARE_INFO.replace("CH,3", "CH,4") if self.encoders.spo2 is not None else FIRMWARE_INFO
            return [info + str(self.rate_hz)]
        if self.digital and name in ("ALARM", "PUMP", "HEAT"):
            # the alarm LED and buzzer, the sample pump and the flow sensor's heater
            return [f"A,{name},{arg}"] if arg.isdigit() else ["E,UNKNOWN_CMD"]
        if name == "START":
            self.streaming = True
            return ["A,START"]
        if name == "STOP":
            self.streaming = False
            return ["A,STOP"]
        if name == "RATE":
            try:
                hz = float(arg)
            except ValueError:
                hz = math.nan
            if not math.isfinite(hz) or not hz.is_integer() or hz < 1 or hz > 200:
                return ["E,BAD_RATE"]
            self.rate_hz = int(hz)
            return [f"A,RATE,{self.rate_hz}"]
        return ["E,UNKNOWN_CMD"]
