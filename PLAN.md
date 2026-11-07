# CO2-Response: Capnography & Flow Monitor Build Plan

CMU BME 26-27 Capstone · Spencer Kim · plan revised Nov 5, 2026

This plan builds on the original "Capnography & Flow Dashboard: Build Plan for Claude Code" (Nov 5, 2026). The engineering scope is the same. Three things change:

1. **The UI follows a bedside patient monitor**, not a generic web dashboard. It is one screen, black background (with a light theme for daytime), a fixed color for each parameter, large numbers with their alarm limits beside them, waveforms that sweep, and a row of soft keys along the bottom. The full spec is in [Clinical UI design](#clinical-ui-design).
2. **The UI has two modes.** *Monitor* is what a clinician sees: waveforms, numbers, alarms and nothing else. *Setup* is what the engineer uses: calibration, raw volts, channel config, diagnostics. Keeping calibration off the monitor screen is what keeps the monitor screen simple.
3. **The repo already exists** (`shrlak/co2-response`). Phase 1 scaffolds inside it and skips `gh repo create`.

A clickable mockup of the Monitor screen is in [`docs/design/monitor-mockup.html`](docs/design/monitor-mockup.html). Open it in any web browser and press **Simulate CO2 rise** to see the alarm states. **Phase 0 is signing off on that mockup** before any production code is written.

## Build status (Nov 5, 2026)

All seven phases are built and pushed. What is verified and what still needs hardware:

> **Update: the app is now Python.** The browser app (TypeScript, React, Vite, Web Serial) was rewritten as a Python desktop app: `co2response/` (PySide6 + pyqtgraph for the window, pyserial for the Arduino, numpy). The protocol, simulator, processing, alarms, recorder and session-file format are line-for-line ports. The port was checked by reproducing the TypeScript version's `sample-data/synthetic_session.csv` byte for byte; the sample has since been regenerated for format v3 (noise removal, below), and a test keeps the code and the file in step. What changed for users: install with `pip install -r requirements.txt` and run `python -m co2response`; it works on Windows, macOS and Linux without Chrome/Edge; sessions are written straight to a folder (Documents/CO2-Response/sessions) instead of browser storage. The same Python also runs in the browser under Pyodide and is still published to GitHub Pages, now by `.github/workflows/deploy.yml` (README §7).
>
> **Update: noise removal.** Samples pass a Hampel spike filter before calibration and the low-pass filter, and every breath is screened (`ok`, `noisy`, `artifact`, `outlier`) before it reaches the numbers, the trend, the response plot, the alarms, review or analysis. Rejected breaths stay in the file, marked (`breath_quality`, session format v3). Details: README §5.7 and [docs/data-format.md](docs/data-format.md#noise-removal). The phase notes below describe the original build; the [tech stack](#7-tech-stack-and-repo-layout) is current.

| Phase | Status |
| --- | --- |
| 0 UI sign-off | Mockup done; the real app follows it, with **dark and light themes** and a desktop layout (1280×720 and up). |
| 1 Scaffold | Done. CI runs ruff and pytest (including the headless window) on Linux, Windows and macOS, firmware builds and a bridge smoke test. |
| 2 Protocol + simulator | Done, tested (bad checksums, sequence gaps, wraparound, device reset). |
| 3 Processing | Done, tested against synthetic data with known answers (RR 15 ± 0.5, EtCO2 40 ± 0.5, VT, MV). |
| 4 Monitor UI | Done. 10-minute soak at 100 Hz/channel in simulator mode (see PR notes for numbers). |
| 5 Alarms | Done, tested (delay, hysteresis, latching, re-arm, CO2 ramp end to end, in the window too). |
| 6 Firmware + hardware link | Firmware builds for Uno/Mega/Nano and its line format matches the dashboard byte for byte. **Not yet run on a real Arduino**: the "< 0.1 % drops over 10 min" check needs hardware. |
| 7 Recording, docs | Done. Replay reproduces recorded numbers exactly (tested). |
| + Hardware | Hardware check in Diagnostics (link, ADC, per-sensor rail/noise), one-click and automatic USB reconnect, firmware I2C scan and bus timeout. Step-by-step [SETUP-GUIDE.md](SETUP-GUIDE.md) with a wiring diagram. |
| + Python / other DAQs | `python/` package `co2r` (Python 3.9+): `co2r-daq serve` feeds the monitor (desktop or browser, Settings > Connection > Python acquisition) from the Arduino, NI-DAQmx, LabJack or a simulated DAQ; template for any other board; session loader (`co2r.session`, wrapped by `analysis/load_session.py`). NI and LabJack drivers are not yet tested on hardware. See [docs/python-daq.md](docs/python-daq.md). |
| + Data | Each recording is one analysis-ready CSV (format v3, with each breath's noise-screen quality), written to disk every second (crash-safe). Data & recordings screen: import, review with charts, export whole/breaths/events/time range, replay, delete. Python loader in `analysis/`. |
| + Noise removal | Hampel spike filter on every sample and a per-breath screen (noisy / partial / impossible / isolated jump) before anything is interpreted; rejected breaths stay in the file, marked. README §5.7. |
| + Read's rebreathing method | Added after reading the mentor and team documents ([docs/project-background.md](docs/project-background.md)). A marker labelled "Rebreathing" splits baseline from rebreathing breaths. The response line is fitted to the rebreathing breaths only, and the plot reports slope, r, x-intercept and the baseline means, as CHOP does. Shown live on the monitor (desktop and browser) and computed offline by the session loader (`co2r.session.co2_response`, also on the command line). Gaps the documents raise are listed there. |

**Changes from the plan made during the build**

- "Setup mode" is the **Settings** screen. Calibration there supports manual entry (linear, polynomial, lookup table, zero offset, pneumotach k1/k2) as well as zero & span, multi-point fit and 3 L syringe capture, with a live preview before saving.
- The firmware sketch lives in `firmware/CO2Response/` (not `src/` + `include/`) so the same folder builds in PlatformIO and opens in the Arduino IDE.
- ADC rail checks are per channel. CO2 checks only the high rail, because a capnograph reads 0 V at 0 % CO2; a pull-up on CH0 makes an unplugged capnograph read high.
- Added an **apnea** alarm (no breath for 20 s, high priority), since the per-breath EtCO2 alarm cannot fire when there are no breaths.
- The waveforms use sweep mode only; the optional scroll mode was not built.

---

## 1. What we are building

A research monitor that reads three analog sensors (end-tidal CO2, flow rate, pneumotachograph) through an Arduino and an external 16-bit ADC. It shows each signal as a live waveform and a large number, trends ETCO2 against time, and raises visual and audible alarms when CO2 crosses a limit the clinician sets.

**In scope (v1)**

- Arduino firmware that samples 3 channels from the ADC and streams volts over USB serial
- A desktop app (Python; originally a static web app) that talks to the Arduino directly over USB serial
- Live waveforms, numerics, derived values (ETCO2, RR, VT, MV)
- ETCO2 trend, minute-ventilation vs. ETCO2 response plot, configurable alarms, CSV recording and replay
- Simulator and CSV-replay sources, so the whole UI works with no hardware
- README, wiring and calibration docs, unit tests, CI

**Out of scope (v1):** cloud storage, user accounts, patient identifiers of any kind, clinical certification. This is a research prototype (see [Safety](#safety-limitations-and-open-questions)).

## 2. System architecture

```
 Sensors ──analog──► ADS1115 ──I2C──► Arduino ──USB serial──► Monitor app (Python, any OS)
 CH0 CO2                               samples at 100 Hz       │
 CH1 Flow                              sends volts only        ├─ line parser + checksum
 CH2 Pneumotach ΔP                     D,seq,t,v0,v1,v2*CK     ├─ processing (calibrate, filter,
                                                               │   breaths, ETCO2, RR, VT, MV)
                                                               ├─ alarm engine  ◄── runs in the data path
                                                               ├─ recorder (CSV)
                                                               └─ UI (sweep waveforms, numerics, trends)
```

- The Arduino is a dumb, reliable sampler. Every unit conversion, every derived value and every alarm runs in the app, so recalibrating never means reflashing.
- The app needs no server. Data never leaves the laptop.
- An optional Python bridge (serial to WebSocket) lets a second computer watch the same Arduino.

## 3. Hardware, channels and calibration

Sensor and ADC part numbers are not chosen yet, so every conversion lives in one editable file (`co2response/config/channels.json`). Nothing is hard-coded.

| Channel | Sensor | Raw signal (assumed) | Shown as |
| --- | --- | --- | --- |
| CH0 | Capnograph analog out | 0–5 V or 0–3.3 V, linear in CO2 | mmHg (default) or % |
| CH1 | Flow sensor | Linear or polynomial in flow | L/min |
| CH2 | Pneumotachograph ΔP transducer | Differential pressure voltage | L/min (via K or fit) |
| CH3 | Spare | — | Reserved |

**ADC:** ADS1115 by default (16-bit, I2C, 860 SPS shared), so 100 Hz per channel on 3 channels. The board sits behind one driver interface in firmware so an ADS1256 or MCP3208 swap touches one file.

**Calibration model per channel:** `linear` (gain × V + offset), `polynomial` (coefficients) or `table` (interpolated lookup), each with a timestamp.

- CO2: zero on room air (≈0.04 %), span on a known gas (e.g. 5 % CO2).
- Pneumotach: flow = K × ΔP (Fleisch-type) or quadratic fit. K comes from a 3 L syringe: push the volume, integrate, solve K so the integral equals 3.000 L.
- Calibrations save on the computer and export as JSON.

**Wiring notes (README and `docs/wiring.md`):** common ground across sensors, ADC and Arduino; keep every sensor output inside the ADC input range (divider if it exceeds VDD); twisted or shielded leads on the pneumotach transducer.

## 4. Firmware and serial protocol

`firmware/` is a PlatformIO project that also opens in the Arduino IDE.

1. Sample CH0–CH2 at a fixed rate (default 100 Hz) with a `micros()` scheduler, never `delay()`.
2. Convert counts to volts using the PGA gain; send 4 decimals.
3. 115200 baud (configurable in `include/config.h`).
4. Commands: `RATE <hz>`, `START`, `STOP`, `INFO` (firmware version, ADC type, channel count, rate).
5. If the ADC is missing at boot, send `E,ADC_NOT_FOUND` and do not stream zeros.
6. Adafruit ADS1X15 library for the default board.

**Line format** (ASCII, newline-terminated), specified once in `docs/protocol.md` and shared by firmware, parser, simulator and bridge:

```
D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>
```

- `seq`: 16-bit rolling counter, so the app can count dropped samples
- `t_ms`: Arduino `millis()`
- `checksum`: XOR of all bytes between `D` and `*`, 2 hex digits; bad lines are dropped and counted
- Other line types: `I,…` info, `E,…` error, `A,…` command acknowledgement

## 5. Signal processing

Pure Python in `co2response/processing/` (originally TypeScript), no UI imports, each function unit-tested on synthetic data with a known answer.

1. **Calibrate** volts to units from `channels.json`.
2. **Filter:** optional 2nd-order low-pass, 10 Hz default, per channel.
3. **CO2 units:** P_CO2 [mmHg] = F_CO2 [%] / 100 × (P_B − P_H2O). P_B defaults to 760 mmHg (Pittsburgh is closer to 735, so the setting matters). Water-vapor correction is off by default.
4. **Pneumotach flow:** K × ΔP or polynomial, with a zero-flow offset from a **Zero flow** button.
5. **Breath detection:** a breath starts when pneumotach flow crosses zero into inspiration, with a ±0.05 L/s deadband and a 1 s minimum breath. Falls back to CO2 rise/fall when there is no flow signal.
6. **ETCO2:** peak CO2 in the expiratory phase before the next inspiration. Show the latest breath and the median of the last 3.
7. **RR:** 60 / mean period of the last 4 breaths.
8. **VT:** integrate flow over inspiration; reset every breath to stop drift.
9. **MV:** sum of VT over the trailing 60 s, in L/min.
10. **Data quality:** sequence gaps, checksum failures, channel stuck at a rail.

Acceptance test example: a 15 breaths/min square-ish capnogram peaking at 40 mmHg returns RR = 15 ± 0.5 and ETCO2 = 40 ± 0.5.

---

## Clinical UI design

The goal is a screen a nurse or respiratory therapist could read from across the room at 3 a.m. and trust without being trained on it. Every rule below borrows from how bedside monitors and ventilators already look, because clinicians are already fluent in that language.

### Principles

1. **Numbers first, waveforms second, everything else hidden.** The monitor screen shows only what a clinician acts on. Calibration, raw volts, filter settings and channel config move to Setup.
2. **One color per parameter, everywhere.** A parameter's waveform, its number, its label and its trend all share one color, so the eye links them without reading.
3. **The screen never rearranges itself.** Panels have fixed positions. The alarm banner has a reserved slot, so an alarm does not push the waveforms down.
4. **Alarm state is shown three ways:** color, text and motion (flash). Never color alone.
5. **Dark by default.** Black background for overnight and low-light rooms; a "Day" theme is in Settings.
6. **Large targets, no hidden gestures.** Every control is a labeled button at least 44 px tall. No right-click menus, no hover-only controls.
7. **Nothing blocks the waveforms during an alarm.** No modal dialogs over the monitor area; Settings opens as a side drawer.

### Screen layout (1366×768 laptop is the minimum target, scales to 1920×1080)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ RESEARCH USE ONLY │ Study STUDY-01 │ ● Simulator · 100 Hz · 0 drops │ ● REC 00:12:31 │ 02:14:07 │ HEADER 44px
├──────────────────────────────────────────────────────────────────────────────────────┤
│ ▲ HIGH  EtCO2 HIGH  54 mmHg  > 50  since 02:13:51                    [Acknowledge]   │ ALARM SLOT 40px (reserved)
├────────────────────────────────────────────────────────┬─────────────────────────────┤
│ CO2 mmHg   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ 50 ─ ─ ─ ─ ─ ─ │ EtCO2  mmHg         50 / --  │
│   ┌──┐   ┌──┐   ┌──┐   ┌──┐   ┌─                     │          38                  │
│ ──┘  └───┘  └───┘  └───┘  └───┘ ▌                    │ RR  /min      FiCO2 mmHg     │
├────────────────────────────────────────────────────────┤   15            0           │
│ FLOW L/min      ⌒    ⌒    ⌒    ⌒   ▌                  ├─────────────────────────────┤
│ ───────────────────────────────── ▌                  │ FLOW L/min    PEAK L/min     │
│                 ⌣    ⌣    ⌣    ⌣                      │   12            31           │
├────────────────────────────────────────────────────────┼─────────────────────────────┤
│ PNEUMOTACH L/min ⌒   ⌒    ⌒    ⌒   ▌                  │ VT  mL        MV  L/min      │
│ ───────────────────────────────── ▌                  │  512           7.7           │
├────────────────────────────────────────────────────────┴─────────────────────────────┤
│ EtCO2 TREND   30 min ▾      · · · · · · · · ·:· · · · · · ·  ─ ─ 50 ─ ─              │ TREND 120–160px
│               ▲ CO2 step started                                                     │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ [Silence 2:00] [Arm audio] [Mark event] [● Record] [Freeze] [Alarm limits] [Setup]   │ SOFT KEYS 56px
└──────────────────────────────────────────────────────────────────────────────────────┘
       waveforms ≈ 68 % width                                  numerics ≈ 32 % width
```

On a narrow window (< 900 px) each waveform stacks above its numbers. The page never scrolls sideways.

### Color system

| Token | Color | Used for |
| --- | --- | --- |
| `--bg` | `#000000` | Screen background (true black is standard on monitors) |
| `--panel` | `#0B0F14` | Numeric tiles, header, soft-key bar |
| `--grid` | `#1A222C` | Waveform baselines and gridlines |
| `--text` | `#E6EBF0` | Labels, clock, header text |
| `--muted` | `#7A8694` | Units, limits, secondary text |
| `--co2` | `#FFD60A` yellow | CO2 waveform, EtCO2, RR, FiCO2, trend points |
| `--flow` | `#38D9F5` cyan | Flow-sensor waveform and numbers |
| `--pneumo` | `#5BE37D` green | Pneumotach waveform, VT, MV |
| `--alarm-high` | `#FF3B30` red | High-priority alarms |
| `--alarm-med` | `#FF9F0A` amber | Medium-priority and technical alarms |
| `--advisory` | `#8AB4F8` blue | Advisories (audio not armed, dropped samples) |

Yellow for CO2 is the most common capnography convention on bedside monitors. Alarm colors are kept distinct from all three parameter colors so an alarm never looks like a waveform.

### Typography

- Numerics: a condensed sans with tabular figures (Barlow Semi Condensed), bundled locally with `@fontsource` so the app works offline in the lab.
- EtCO2 is the largest number on the screen: at least 96 px at 1366 px wide. Other primary numbers at least 64 px; secondary numbers about 36 px.
- Labels are uppercase, 13–14 px, letter-spaced, in the parameter color. Units sit next to the label, never next to the number, so the number's width stays stable.
- Every number uses `font-variant-numeric: tabular-nums` so digits do not jitter as values change.

### Numeric tile anatomy

```
┌──────────────────────────────────┐
│ EtCO2  mmHg              50  ◄── │  high limit
│                          --  ◄── │  low limit (off)
│              38                  │  value, 4 Hz refresh
│ median 3 br: 38                  │  qualifier, muted
└──────────────────────────────────┘
```

- Alarm limits always show beside the number they guard, the way monitors do. A bell-with-slash icon replaces them when that alarm is off.
- **Stale channel** (no valid data for 2 s): the number becomes `---` in `--muted`, and a medium technical alarm starts at 3 s.
- **In alarm:** the tile fills red and the number flashes at about 2 Hz (high) or 0.5 Hz (medium). `prefers-reduced-motion` swaps flashing for a steady fill with a thick border.
- Raw volts are hidden in Monitor mode. Setup mode shows them under every number.

### Waveform rendering

- **Sweep, not scroll.** Each trace draws left to right and wraps, with a small erase bar ahead of the pen, like a monitor. Old data does not slide, so the eye can rest on a fixed spot. Scroll mode is an option in Settings.
- Sweep speed is set in seconds per screen (default 12 s, options 6 / 12 / 30 / 60 s), shared by all three traces.
- Fixed scales by default (CO2 0–60 mmHg; flow ±60 L/min) with an **Auto-scale** option. A fixed scale is how clinicians judge "big" vs. "small" at a glance.
- The high CO2 limit is drawn as a dashed line on the capnogram.
- **Implementation:** a small custom Canvas 2D sweep renderer (≈150 lines) for the three live traces, fed from typed-array ring buffers, drawn on `requestAnimationFrame` at ≤ 30 fps. uPlot is still used for the trend and response charts, where axes, zoom and cursors matter. This replaces the original plan's "uPlot for everything", because uPlot has no sweep mode.

### Trend strip (bottom, full width)

- One point per breath: EtCO2 against wall-clock time, in `--co2`, with a faint continuous CO2 trace behind it.
- Window: 15 min / 30 min / 2 h / whole session.
- Dashed limit line, a faint red band above the high limit, red triangles where alarms fired.
- Event markers from **Mark event** (hotkey `M`) with an editable label, e.g. "CO2 step started".
- Second tab: **Ventilatory response**. MV vs. EtCO2 scatter with a linear fit and the slope in L/min/mmHg, for rebreathing protocols.

### Soft-key bar

Seven keys, fixed order, always visible. Each shows its state in its own label.

| Key | Behavior |
| --- | --- |
| **Silence 2:00** | Silences audio for 120 s; the label counts down. Visual alarm stays on. |
| **Arm audio** | Unlocks Web Audio (browsers require a click). Turns green "Audio armed" when done. Until then a blue advisory says "Audible alarm NOT armed". |
| **Mark event** | Drops a labeled marker on the trend and in the CSV. |
| **Record** | Starts/stops CSV recording; header shows `● REC hh:mm:ss`. |
| **Freeze** | Freezes the waveforms for inspection. Numbers and alarms keep running underneath. |
| **Alarm limits** | Opens the limits drawer (high/low EtCO2, delay, hysteresis, source). |
| **Setup** | Switches to Setup mode. Asks for confirmation if recording. |

### Header

`RESEARCH USE ONLY` tag (permanent, not dismissible) · study code (free text, no patient identifiers) · source and connection dot (green connected, amber reconnecting, red lost) · sample rate · dropped-sample count · record status · alarm-system status pill (`ARMED`, `ALARM`, `SILENCED 1:42`, `AUDIO OFF`) · 24-hour clock with seconds.

### Setup mode

A separate screen, entered from the soft-key bar. Same color system, denser layout, light or dark.

- **Connect:** source picker (Arduino over USB serial, Simulator, Replay CSV, Bridge), baud rate, `INFO` handshake result.
- **Channels:** live raw volts per channel, calibration type, coefficients, stuck-at-rail indicator.
- **Calibration wizard:** step-by-step CO2 zero/span and the 3 L syringe procedure, with a pass/fail result and a timestamp.
- **Display:** sweep speed, scales, units (mmHg/%), barometric pressure, filter on/off, theme.
- **Diagnostics:** checksum failures, sequence gaps, effective sample rate, session log.

### Alarm presentation (styled after IEC 60601-1-8 conventions, not certified to it)

| Priority | Examples | Visual | Tone |
| --- | --- | --- | --- |
| **High** | EtCO2 above high limit | Red banner, red tile, 2 Hz flash, tab title flashes, system notification if tab hidden | 10-pulse burst, repeats every ~5 s |
| **Medium** | EtCO2 below low limit; sensor disconnect; no data 3 s; ADC error; channel at rail | Amber banner, amber tile, 0.5 Hz flash | 3-pulse burst, repeats every ~15 s |
| **Advisory** | Audio not armed; dropped samples > 0.1 %; checksum errors | Blue text in the alarm slot, no flash | None |

- Alarms **latch.** After the condition clears the banner stays, labeled "resolved", until someone presses **Acknowledge**.
- Several active alarms: the slot shows the highest priority, plus a "+2" chip that lists the rest.
- **Test alarm** (in Alarm limits) plays each tone and shows each banner, so the operator can confirm sound before a session.

## 6. CO2 alarm logic

Runs on every processed sample or breath, inside the data path, never in the render loop, so it fires even when the tab is in the background.

| Setting | Default | Notes |
| --- | --- | --- |
| Alarm source | EtCO2 per breath | Or live CO2 |
| High limit | 50 mmHg | Required; dashed on charts |
| Low limit | Off | Optional; apnea or disconnect |
| Trigger delay | 2 breaths or 5 s | Rejects single spikes |
| Hysteresis | 2 mmHg | Clears below limit − hysteresis |
| Silence | 120 s | Auto re-arms; maximum 120 s |
| Volume | 80 % | Cannot be 0 while armed |

Every alarm start, acknowledge, silence and clear goes into the session log and the CSV export. A Screen Wake Lock is held while connected so the laptop display does not sleep during overnight runs.

## 7. Tech stack and repo layout

| Layer | Choice |
| --- | --- |
| Firmware | C++ / PlatformIO, Adafruit ADS1X15 |
| App | Python 3.10+, PySide6 (Qt) desktop window |
| Live waveforms | Custom QPainter sweep widget (paths built with pyqtgraph's `arrayToQPath`) |
| Trend and review charts | pyqtgraph |
| Fonts | Barlow Semi Condensed (OFL), bundled in `co2response/fonts/` |
| Device link | pyserial (Windows, macOS, Linux) |
| Alarm tones | numpy-synthesized IEC-style pulses through sounddevice (PortAudio); system beep as a fallback |
| Bridge (optional) | Python, pyserial + websockets |
| Tests | pytest (protocol, simulator, processing, alarms, session files, engine with a pseudo-terminal "Arduino") and pytest-qt for the window, headless |
| CI | GitHub Actions on Linux, Windows and macOS |

```
CO2-Response/
├── README.md               # setup, usage, safety, assumptions
├── PLAN.md                 # this document
├── SETUP-GUIDE.md          # wiring to a running session
├── pyproject.toml, requirements*.txt
├── co2response/            # the monitor app (python -m co2response)
│   ├── protocol.py         # line format, checksum, parser, sequence tracking
│   ├── channels.py, settings.py, config/channels.json
│   ├── sim/                # simulated physiology and firmware emulation
│   ├── processing/         # calibration, filter, breaths, EtCO2, RR, VT, MV, rail checks
│   ├── alarms/             # alarm state machine and tones
│   ├── sources/            # serial (Arduino), simulator, replay, bridge
│   ├── storage/            # session file format, session folder, recorder
│   ├── engine/             # data path (source → processing → alarms → recorder), hardware check
│   └── ui/                 # PySide6 window: monitor, settings, review
├── tests/                  # pytest
├── scripts/make_sample_session.py
├── firmware/               # PlatformIO project; sketch in CO2Response/, host-side line-format test
├── bridge/                 # optional serial → WebSocket relay
├── analysis/               # pandas loader for session files
├── docs/                   # protocol, wiring, calibration, safety, data format, Phase 0 mockup
└── sample-data/synthetic_session.csv
```

## 8. Build phases

Each phase ends with tests run, a commit, a push, and a pause for review. The app is fully usable in simulator mode by Phase 4, before any hardware is wired.

| # | Phase | What gets built | Done when |
| --- | --- | --- | --- |
| 0 | **UI sign-off** | Static mockup of the Monitor screen (`docs/design/monitor-mockup.html`) | You and your PI/advisor agree on layout, colors and alarm look. Changes go into this plan. |
| 1 | **Scaffold** | Folder layout, `.gitignore` (Node, PlatformIO, Python), LICENSE, Vite + React + TS app shell, ESLint/Prettier, Vitest with one placeholder test, `ci.yml` | CI is green on a PR. |
| 2 | **Protocol + simulator** | `docs/protocol.md`, line parser, simulator with adjustable RR / ETCO2 / VT and scenarios: normal, CO2 ramp, apnea, disconnect, noisy | Parser tests pass, including bad checksums and sequence gaps. |
| 3 | **Processing** | Calibration, filter, breath detection, ETCO2, RR, VT, MV, data-quality flags | Vitest passes on synthetic data with known answers. |
| 4 | **Monitor UI** | Theme tokens and fonts, header, reserved alarm slot, sweep renderer, three wave panels, numeric tiles, trend strip, soft keys, Settings drawer, all driven by the simulator | Simulator runs 10 min at 100 Hz/channel with no visible lag and flat memory; Playwright screenshot matches the signed-off mockup at 1366×768 and 1920×1080. |
| 5 | **Alarms** | State machine (delay, hysteresis, latching, silence/re-arm), priorities, banner and tile states, Web Audio tones, Arm audio, Test alarm, technical alarms, logging, wake lock | Unit tests cover delay, hysteresis, latching and auto re-arm; the CO2 ramp scenario alarms at the set limit. |
| 6 | **Firmware + Setup mode** | PlatformIO sketch, Web Serial connect, `INFO` handshake, Setup screens, calibration wizard | A real Arduino streams at the set rate with < 0.1 % drops over 10 min. |
| 7 | **Record, deploy, docs** | CSV record/export with events and alarms, CSV replay, Pages deploy, optional bridge, final README and docs | The Pages URL loads, connects in Chrome, and a recorded session replays identically. |

### GitHub setup for this repo

- The repo `shrlak/co2-response` already exists, so there is no `gh repo create` step.
- Work happens on feature branches with a PR per phase into `main`.
- Things you turn on yourself in GitHub Settings (the build cannot do these from this environment):
  - **Branch protection:** Settings → Branches → require the CI check to pass before merging into `main`.

## Safety, limitations and open questions

This is a research prototype, not a medical device. It must never be the only CO2 monitor on a person or animal, and the README and UI must say so.

- The header shows `RESEARCH USE ONLY` permanently. A per-session banner adds "Not for clinical decision-making. Keep a certified monitor as the primary alarm."
- The alarm only works while the app is running, the laptop is awake, and a sound output is armed. The README says this in its first screen.
- No patient identifiers. Session files are named by date/time and a free-text study code.
- Electrical isolation: if anything connects to a person, the Arduino/ADC must be isolated from mains (battery laptop or USB isolator). Go through the lab's safety process before any human or animal use.
- The alarm look and tones are styled after IEC 60601-1-8 conventions so they feel familiar. They are not certified to that standard.

**Open questions** (each uses the stated default, with a `TODO` in code and a line under README "Assumptions"). The full, current list is in [docs/open-questions.md](docs/open-questions.md); discuss and answer them in [issue #4](https://github.com/shrlak/CO2-Response/issues/4).

- [ ] CO2 sensor/capnograph model, its analog output range and scaling
- [ ] Flow sensor and pneumotach + pressure transducer models
- [ ] ADC board (default ADS1115) and Arduino (Uno, Mega, Nano 33)
- [ ] Sample rate if more than 100 Hz/channel is needed
- [ ] Default alarm limits, and whether the alarm keys on ETCO2 or live CO2
- [ ] Is the flow sensor on the patient airway (same as the pneumotach) or a gas-delivery line? This decides whether it gets its own wave panel or moves to a smaller "Delivery flow" tile.
- [ ] Public vs. private repo, per lab policy
- [ ] Clinician review of the Phase 0 mockup: do the target users prefer mmHg or %, and which sweep speed?
