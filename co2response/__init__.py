"""CO2-Response: a research capnography and flow monitor.

An Arduino samples three analog sensors (CO2, a flow sensor and a pneumotach) and
streams volts over USB. This package reads that stream, turns it into calibrated
waveforms, breaths and alarms, records sessions, and shows it all on a bedside-style
monitor window (PySide6).

    python -m co2response          # open the monitor
    python -m co2response --sim    # open it and start the simulator

RESEARCH USE ONLY. Not a medical device.
"""

__version__ = "0.4.0"
APP_NAME = "CO2-Response"
