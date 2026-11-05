# CO2-Response

CMU BME 26-27 Capstone Project

A research capnography and flow monitor. An Arduino with a 16-bit ADC samples three analog sensors (end-tidal CO2, a flow sensor and a pneumotachograph) and streams volts over USB. A Python desktop app shows them like a bedside patient monitor: sweeping waveforms, large numbers, an EtCO2 trend, a ventilatory-response plot, and visual + audible CO2 alarms. It runs on Windows, macOS and Linux.

> **Setting up the hardware or running a session?** Start with the step-by-step **[setup and operating guide](SETUP-GUIDE.md)**: wiring the sensors to the ADC board and the Arduino, loading the firmware, installing the app, checking, calibrating, and running a session.

> **Research use only.** This is not a medical device and must never be the only CO2 monitor on a person or animal. Keep a certified monitor as the primary alarm. The alarm here works only while the app is running, the computer is awake and a sound output is armed. See [docs/safety.md](docs/safety.md).

| | |
| --- | --- |
| **Setup and operating guide** | [SETUP-GUIDE.md](SETUP-GUIDE.md) |
| Build plan | [PLAN.md](PLAN.md) |
| Serial protocol | [docs/protocol.md](docs/protocol.md) |
| Wiring | [docs/wiring.md](docs/wiring.md) |
| Calibration | [docs/calibration.md](docs/calibration.md) |
| Safety | [docs/safety.md](docs/safety.md) |
| UI mockup (Phase 0) | [docs/design/monitor-mockup.html](docs/design/monitor-mockup.html) |
| Session files, import/export, analysis | [docs/data-format.md](docs/data-format.md) |
| Sample session | [sample-data/synthetic_session.csv](sample-data/synthetic_session.csv) |

## Quick start (no hardware)

You need Python 3.10 or newer.

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

(On Windows: `.venv\Scripts\activate`.)

```bash
pip install -r requirements.txt
```

```bash
python -m co2response --sim
```

The monitor opens and the simulator starts. Without `--sim`, press **Start simulator** on the start screen. In **Settings > Connection** you can switch scenarios: normal breathing, CO2 ramp (rebreathing), apnea, CO2 sensor disconnected, device stops sending, and noisy signal.

To try a recorded session: `python -m co2response --replay sample-data/synthetic_session.csv`, or **Settings > Connection > Replay a file…**.

`pip install -e .` also works and adds a `co2response` command.

## Using it with the Arduino

Full walkthrough with a wiring diagram and troubleshooting: [SETUP-GUIDE.md](SETUP-GUIDE.md). In short:

1. Wire the sensors to the ADS1115 and the ADS1115 to the Arduino ([docs/wiring.md](docs/wiring.md)).
2. Flash the firmware. Either:
   - PlatformIO: `cd firmware && pio run -e uno -t upload` (environments: `uno`, `mega`, `nano`), or
   - Arduino IDE: open `firmware/CO2Response/CO2Response.ino`, install **Adafruit ADS1X15** from the Library Manager, choose the board and upload.
3. Start the app (`python -m co2response`), press **Connect Arduino (USB)** and pick the port (likely Arduinos are listed first), or start it with `--port COM3` / `--port /dev/ttyACM0`. `python -m co2response --list-ports` lists the ports. The app sends `INFO` and `START`; the header shows the sample rate and dropped-sample count.
4. Calibrate each channel in **Settings > Calibration** ([docs/calibration.md](docs/calibration.md)).
5. Run **Settings > Diagnostics > Hardware check** and fix anything it flags.
6. Check the **Audio armed** key is green, then **Alarm limits > Test alarm** to confirm you can see and hear the alarm.

If the USB cable comes out, the device-disconnected alarm sounds, and plugging the same Arduino back in reconnects by itself (recording continues in the same file).

### Watching from a second computer

Run the Python bridge on the computer with the Arduino, and connect to it from **Settings > Connection > Python bridge** on the other:

```bash
pip install -r bridge/requirements.txt
```

```bash
python bridge/serial_ws_bridge.py --port COM3 --host 0.0.0.0
```

`--simulate` runs the bridge without hardware. It listens on localhost only unless you pass `--host 0.0.0.0` (only do that on a trusted network).

## The monitor screen

- **Header:** research-use tag, study code, source and connection, measured sample rate, dropped samples, recording status, alarm-system status (`ARMED`, `ALARM`, `SILENCED 1:42`, `AUDIO OFF`, `DISARMED`) and the clock.
- **Alarm strip:** a fixed-height strip under the header, so an alarm never moves the waveforms. Red flashing for high priority, amber for medium (technical), blue for advisories. Alarms latch until you press **Acknowledge** or **Clear**. A high alarm also flashes the window title and taskbar entry.
- **Waveforms** sweep left to right like a bedside monitor: CO2 (yellow, with the high limit dashed), flow sensor (cyan) and pneumotach (green).
- **Numbers:** EtCO2 (with its limits beside it), RR, FiCO2, live and peak flow, VT and MV. A channel with no valid data for 2 s shows `---`.
- **Trend:** one EtCO2 point per breath over 15 min, 30 min, 2 h or the whole session, with the limit band, alarm markers and your event markers. The **Ventilatory response** tab plots MV against EtCO2 with a linear fit (slope in L/min/mmHg).
- **Soft keys:** Silence (120 s), Arm audio, Mark event (or press `M`), Record, Freeze, Alarm limits, Settings.
- **Settings:** Connection, Calibration, Alarms, Display & units (dark/light theme, mmHg/%, sweep speed, scales, barometric pressure, filter), Data & recordings, Diagnostics.

## Recording, import and export

**Record** saves each session as **one CSV file** with every sample (raw volts and calibrated values), every breath (EtCO2, FiCO2, RR, VT, MV, peak flow), every alarm event and every marker, plus the calibration and settings in use. Every row has the same columns, so it opens directly in Excel, pandas (`pd.read_csv(f, comment='#')`), MATLAB or R. The file `co2r_<study code>_<YYYYMMDD-HHMMSS>.csv` is written to **Documents/CO2-Response/sessions** (you can pick another folder) every second while recording, so a crash loses at most a second; the next start lists an unfinished file as **Recovered**. **Never put patient identifiers in the study code.**

**Settings > Data & recordings** lists saved sessions and lets you:

- **Import** session files from another computer (older v1 files too)
- **Open** a session to review it: summary statistics, zoomable EtCO2/RR, VT/MV and waveform charts, and the event list
- **Export** the whole session, the breath table, or the alarms/events table, for the whole session or a zoomed time range
- **Replay** it through the monitor, or **delete** it

`analysis/load_session.py` loads a session into pandas tables (samples, breaths, events). The format and analysis examples are in [docs/data-format.md](docs/data-format.md).

## Development

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

```bash
ruff check .
```

```bash
python scripts/make_sample_session.py
```

`pytest` runs everything, including the window (headless; set `QT_QPA_PLATFORM=offscreen` if there is no display) and a serial test that uses a pseudo-terminal as the Arduino (Linux/macOS). The last command regenerates `sample-data/synthetic_session.csv`; a test checks that the code still reproduces it byte for byte.

Firmware: `cd firmware && pio run` builds every board; `g++ -std=c++11 -I CO2Response test/test_line_format.cpp -o t && ./t` checks the firmware's data lines against the app's formatter.

CI (`.github/workflows/ci.yml`) runs lint and the full test suite on Linux, Windows and macOS; the firmware builds and line-format test; and a bridge smoke test.

### Layout

```
co2response/                 the monitor app (python -m co2response)
  protocol.py                line format, checksum, parser, sequence tracking
  channels.py, config/       sensor scaling defaults (config/channels.json, edit without rebuilding)
  settings.py                settings, saved as JSON in the per-user data folder
  sim/                       simulated physiology and firmware emulation
  processing/                calibration, filter, breath detection, EtCO2/RR/VT/MV, rail checks
  alarms/                    alarm state machine and tones
  sources/                   serial (Arduino), simulator, CSV replay, WebSocket bridge
  storage/                   session file format, session folder, recorder
  engine/                    data path: source → parser → processing → alarms → recorder; hardware check
  ui/                        PySide6 window: monitor, settings, session review
tests/                       pytest suites (core, engine, window)
scripts/                     sample-session generator
firmware/                    PlatformIO project; sketch in CO2Response/ (also opens in Arduino IDE)
bridge/                      optional Python serial → WebSocket relay
analysis/                    Python loader for session files
docs/                        protocol, wiring, calibration, safety, data format, UI mockup
```

Everything apart from `firmware/` is Python. The data path is in `co2response/engine/monitor.py`, which has no UI code and can be driven from a script or a test.

## Assumptions

These are placeholders until the open questions in [PLAN.md](PLAN.md) are answered. Each is marked `TODO` in code or config.

- **CO2 sensor:** analog output 0–5 V linear, 0–10 % CO2 (`channels.json`: gain 2 %/V). Replace with the real capnograph scaling.
- **Flow sensor:** bidirectional, 2.5 V = 0 L/min, 40 L/min per volt.
- **Pneumotach transducer:** 2.5 V = 0 Pa, 200 Pa per volt; flow = k1·ΔP with k1 = 0.2 L/min/Pa until a syringe calibration is done.
- **ADC:** ADS1115 at I2C address 0x48, PGA ±6.144 V, 5 V supply (rails 0 and 5 V). CO2 is checked only at the high rail because a capnograph reads 0 V at 0 % CO2.
- **Arduino:** Uno by default (Mega and Nano also build).
- **Sample rate:** 100 Hz per channel.
- **Alarm defaults:** high EtCO2 50 mmHg, low off, 2-breath delay, 2 mmHg hysteresis, 120 s silence, apnea after 20 s, keyed on EtCO2 per breath.
- **Barometric pressure:** 760 mmHg default. Pittsburgh is usually near 735 mmHg; set the lab's value in Display & units.
- **Flow sensor role:** treated as a second airway flow signal shown in its own panel. If it measures a gas-delivery line instead, relabel it in `channels.json`.
- **License:** none chosen yet (see LICENSE). The bundled Barlow Semi Condensed font is under the SIL Open Font License (`co2response/fonts/OFL.txt`).
