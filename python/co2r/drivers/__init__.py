"""Registry of data-acquisition drivers. Add new boards here."""
from __future__ import annotations

from .base import DaqDriver, DaqError, DaqOverrun, DriverInfo
from .labjack import LabJackDriver
from .nidaq import NiDaqmxDriver
from .simulated import SimulatedDriver

#: --driver NAME -> class. "arduino" is not here: it relays the firmware's own lines
#: (co2r.sources.SerialRelaySource) instead of producing volts.
DRIVERS: dict[str, type[DaqDriver]] = {
    SimulatedDriver.name: SimulatedDriver,
    NiDaqmxDriver.name: NiDaqmxDriver,
    LabJackDriver.name: LabJackDriver,
}

__all__ = ["DRIVERS", "DaqDriver", "DaqError", "DaqOverrun", "DriverInfo"]
