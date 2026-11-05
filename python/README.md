# co2r: CO2-Response data acquisition in Python

Reads an Arduino or another data-acquisition system (NI-DAQmx, LabJack, simulated) and feeds the CO2-Response monitor in the browser over WebSocket. Also loads recorded sessions into pandas.

```bash
pip install -e python
co2r-daq serve --driver simulated
```

Then in the dashboard: Settings → Connection → Python acquisition → Connect to Python.

Full guide: [docs/python-daq.md](../docs/python-daq.md). Research use only; not a medical device.
