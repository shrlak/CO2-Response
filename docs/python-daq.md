# Python acquisition and other data-acquisition systems

The `co2r` Python package (in `python/`) reads the hardware and feeds the same CO2-Response monitor in the browser. It is the way to run CO2-Response on a **data-acquisition system other than the Arduino + ADS1115**, and it also works with the Arduino.

```
 sensors ──► DAQ (Arduino, NI, LabJack, …) ──USB──► co2r-daq (Python) ──WebSocket──► monitor (desktop app or browser)
                                                    ws://localhost:8765               (monitor, alarms, calibration,
                                                                                         recording: all unchanged)
```

Python only moves voltages. The monitor still does everything clinical: calibration, noise removal, breath detection, EtCO2, alarms, recording and the hardware check. So a new DAQ needs no change to the monitor, and calibrations, recordings and analysis work the same way.

> **Research use only.** Not a medical device. The NI and LabJack drivers have been tested against stand-ins for their vendor libraries, **not yet on real hardware**. Run `co2r-daq check` on the device before relying on it.

## Supported DAQs

| `--driver` | Hardware | Needs | Status |
| --- | --- | --- | --- |
| `arduino` | The CO2-Response Arduino firmware | `pyserial` (installed with the package) | Same data as Web Serial; passes the firmware's lines through untouched |
| `nidaq` | National Instruments: USB-6001, USB-6009, USB-6211, cDAQ and other NI-DAQmx devices | [NI-DAQmx](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) driver + `pip install "co2r[nidaq]"` | Untested on hardware |
| `labjack` | LabJack T7, T4, T8 | [LJM](https://labjack.com/pages/support?doc=/software-driver/ljm-users-guide/) driver + `pip install "co2r[labjack]"` | Untested on hardware |
| `simulated` | none: realistic breathing signals | nothing | Tested |
| your board | any DAQ with a Python library | see [Adding a DAQ](#adding-a-different-daq) | one small file |

The lab has not chosen the new DAQ yet. Any board with a Python library can be added by copying the template driver.

## Install

You need **Python 3.9 or newer** (3.9–3.13 are tested on Windows, macOS and Linux). From the repository folder:

```bash
pip install -e python
```

Add the extra for your DAQ, and `analysis` for pandas:

```bash
pip install -e "python[nidaq,analysis]"
```

This installs the `co2r-daq` command. `python -m co2r` does the same thing if the command is not on your PATH.

Check what is installed and what is plugged in:

```bash
co2r-daq drivers
```

```bash
co2r-daq list
```

## Quick start without hardware

```bash
co2r-daq serve --driver simulated
```

In the monitor (`python -m co2response`, or the GitHub Pages site on the same computer): **Settings → Connection → Python acquisition → Connect to Python** (address `ws://localhost:8765`). The header shows **Simulated DAQ (Python)** and the monitor runs. `--ramp 15` makes CO2 rise 15 mmHg per minute, to try the alarms.

## Wiring a DAQ

The three signals go to the first three analog inputs, in this order:

| Signal | NI (`--channels`) | LabJack | Dashboard channel |
| --- | --- | --- | --- |
| Capnograph analog out | `ai0` | `AIN0` | CH0, CO2 |
| Flow sensor out | `ai1` | `AIN1` | CH1, flow |
| Pneumotach pressure transducer out | `ai2` | `AIN2` | CH2, pneumotach |
| All sensor signal grounds | `AI GND` | `GND` | |

- Use **single-ended** inputs referenced to the DAQ's ground: `--terminal RSE` on NI (the default); LabJack inputs are set single-ended automatically.
- Use an input range that covers your sensors. `--range 0,5` for 0–5 V sensors on NI (the default; on boards without a 0–5 V range, NI picks the next range that covers it). LabJack ranges are symmetric, e.g. `--range -10,10`.
- The same rules as the Arduino apply: one common ground, every output inside the input range, twisted or shielded wire for the pressure transducer. See the [setup guide](../SETUP-GUIDE.md#23-rules-that-prevent-most-problems).
- Sensor power: use the DAQ's +5 V output if it has one and the sensor draws little current; otherwise give the sensor its own supply and join its ground.
- **Unplugged-sensor detection** works the same if you keep the 1 MΩ pull-up from the CO2 input to +5 V. Keep `railLowV` 0 and `railHighV` 5 in `co2response/config/channels.json` for 0–5 V sensors, even if the DAQ's range is ±10 V: the "rails" are the limits of the signal, not of the DAQ.

## Running with an NI DAQ

1. Install NI-DAQmx, plug in the DAQ, and note its name in **NI MAX** (for example `Dev1`), or run `co2r-daq list`.
2. Test it without the monitor:

   ```bash
   co2r-daq check --driver nidaq --device Dev1 --seconds 10
   ```

   Expect `PASS` for the rate and `0 samples lost`, and sensible voltages on each channel.
3. Serve it to the monitor:

   ```bash
   co2r-daq serve --driver nidaq --device Dev1
   ```

4. In the monitor: **Settings → Connection → Python acquisition → Connect to Python**. Then run **Settings → Diagnostics → Hardware check** and calibrate as usual (the monitor's calibrations work on volts, so the steps are the same as for the Arduino).

Or put the settings in a file and start with `co2r-daq serve --config python/examples/nidaq-usb6001.json`:

```json
{ "driver": "nidaq", "device": "Dev1", "channels": ["ai0", "ai1", "ai2"], "range": [0, 5], "terminal": "RSE", "rate": 100, "oversample": 10 }
```

## Running with a LabJack

```bash
co2r-daq check --driver labjack --seconds 10
```

```bash
co2r-daq serve --driver labjack
```

`--device` takes a serial number or IP address when more than one LabJack is connected. See `python/examples/labjack-t7.json`.

## Running the Arduino through Python

```bash
co2r-daq serve --driver arduino --port COM3
```

The firmware's lines go through unchanged, so dropped samples, `ADC_NOT_FOUND` and the I2C scan all reach the monitor as with a direct USB connection. This is how to use the Arduino from Safari or Firefox, or show the monitor on a second computer (`--host 0.0.0.0`, on a trusted network only). The old `python bridge/serial_ws_bridge.py --port COM3` still works and runs the same code.

## Settings

| Option | Default | What it does |
| --- | --- | --- |
| `--rate` | 100 | Samples per second per channel sent to the monitor |
| `--oversample` | 10 | The DAQ samples this many times faster and averages each group: lower noise at the same output rate |
| `--range` | `0,5` | Input range in volts |
| `--terminal` | `RSE` | NI input mode: `RSE`, `NRSE`, `DIFF`, `PSEUDO_DIFF` |
| `--channels` | `ai0,ai1,ai2` / `AIN0,AIN1,AIN2` | Inputs for CO2, flow and pneumotach, in that order |
| `--device` | `Dev1` (NI), `ANY` (LabJack) | Which device |
| `--ws-port`, `--host` | 8765, 127.0.0.1 | Where the monitor connects. Only change `--host` on a trusted network. |
| `--config` | | JSON file with any of the above; command-line values win |

The monitor's **RATE** command (Settings → Connection) changes the rate on the fly, up to 200 Hz.

## What the Python server does for every DAQ

`co2r.sources.SampledSource` turns any driver's voltages into the firmware's [line protocol](protocol.md), so the monitor cannot tell the difference:

- **Sequence numbers and time** come from the sample clock. Samples lost by the DAQ (buffer overflow) leave a gap in the sequence and send `E,OVERRUN`, so the monitor counts them as dropped.
- **INFO** names the device (`ADC` field), the rate, the input range (`FSR`) and the oversampling. The header shows it, e.g. **NI Dev1 (Python)**.
- **A missing or failing device** sends `E,ADC_NOT_FOUND,<reason>`, which raises a technical alarm with the driver's reason. The server retries every 2 seconds and carries on when the device comes back.
- **Commands** `INFO`, `START`, `STOP` and `RATE n` behave as on the Arduino.
- **Lines are byte-identical** to the monitor's simulator and the firmware's; all three are tested against the same vectors in `docs/protocol-fixtures.json`.

## Adding a different DAQ

1. Copy `python/co2r/drivers/template.py` to, say, `mydaq.py`, and rename the class and its `name`.
2. Fill in three methods:
   - `open(rate_hz)`: connect, set up 3 single-ended voltage inputs, start continuous hardware-timed sampling, and return a `DriverInfo`.
   - `read(timeout)`: return the samples that arrived, oldest first: `[[v_co2, v_flow, v_pneumo], ...]`.
   - `close()`: stop and release the device.
   Raise `DaqError` when the device is missing or fails, and `DaqOverrun(lost)` when it reports lost samples. Use `average_groups` from `drivers/base.py` for oversampling; `nidaq.py` and `labjack.py` are worked examples.
3. Register it in `python/co2r/drivers/__init__.py` (and in `build_driver` in `cli.py` if it takes options).
4. Add a test with a stand-in for the vendor library, like `python/tests/test_drivers.py`.
5. Try it: `co2r-daq check --driver mydaq`, then `co2r-daq serve --driver mydaq` and the monitor's hardware check.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `co2r-daq: command not found` | Use `python -m co2r …`, or add Python's Scripts folder to PATH |
| `nidaq  not installed` in `co2r-daq drivers` | Install the NI-DAQmx driver from ni.com, then `pip install "co2r[nidaq]"` |
| `DAQ NOT FOUND: …` alarm on the monitor | The message is the driver's own reason. Check the device name (`co2r-daq list`, NI MAX), the USB cable, and that no other program (NI MAX test panel, LabVIEW, LJLogM) is using the device. |
| Dashboard cannot connect to `ws://localhost:8765` | Start `co2r-daq serve` first; it prints `serving on ws://…` when ready. Check `--ws-port`. |
| Lost samples or OVERRUN | Lower `--rate` or `--oversample`, keep the DAQ on a direct USB port, close heavy programs |
| Noisy readings | Raise `--oversample`, check the common ground and input mode (`RSE` vs `DIFF`), and keep the monitor's spike removal and low-pass filter on (Settings → Display & units) |
| Readings off by a constant or a factor | Recalibrate in the monitor: a new DAQ has its own offsets and input impedance |

## Analysis in Python

`co2r.session.load_session("file.csv")` loads a recording into pandas tables (`samples`, `breaths`, `events`). See [data-format.md](data-format.md).

## Development

```bash
pip install -e "python[test]"
```

```bash
python -m pytest python/tests
```

CI runs the tests on Python 3.9–3.13 on Linux, on Windows (3.9, 3.13) and macOS (3.10, 3.13), plus Python 3.9 with the oldest supported `websockets` and `pandas`. `tests/test_python_daq.py` runs the monitor against `co2r-daq serve --driver simulated` (and against a DAQ that cannot be opened) on Linux, Windows and macOS.
