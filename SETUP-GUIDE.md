# CO2-Response: setup and operating guide

This guide takes you from a box of parts to a running session: wiring the sensors to the ADC board and the Arduino, loading the firmware, connecting to the computer, checking and calibrating the setup, and running and saving a session.

> **Research use only.** CO2-Response is not a medical device. On a person or animal, a certified CO2 monitor must always be the primary alarm. Read [docs/safety.md](docs/safety.md) before any live use, and go through the Cook Lab / CMU safety process first.

**Contents**

1. [What you need](#1-what-you-need)
2. [Wire the ADC board and sensors](#2-wire-the-adc-board-and-sensors)
3. [Load the firmware onto the Arduino](#3-load-the-firmware-onto-the-arduino)
4. [Open the dashboard](#4-open-the-dashboard)
5. [Connect the Arduino to the computer](#5-connect-the-arduino-to-the-computer)
6. [Check the setup](#6-check-the-setup)
7. [Calibrate](#7-calibrate)
8. [Run a session](#8-run-a-session)
9. [After the session](#9-after-the-session)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What you need

| Item | Notes |
| --- | --- |
| Arduino Uno R3 | A Nano or Mega 2560 also works (the firmware builds for all three). |
| USB cable for the Arduino | Must be a **data** cable. Charge-only cables power the board but no port appears. |
| ADS1115 16-bit ADC breakout | For example Adafruit 1085. It has 4 inputs and talks to the Arduino over I2C. |
| CO2 monitor / capnograph with an analog output | Plus its analog-output cable. The output must stay between 0 and 5 V. |
| Flow sensor with a 0–5 V analog output | |
| Pneumotachograph and a differential pressure transducer with a 0–5 V output | Example transducer: NXP MPXV7002DP (±2 kPa, 5 V supply, 0.5–4.5 V output). Plus two lengths of tubing to connect it. |
| 1 MΩ resistor | Pull-up on the CO2 input, so an unplugged capnograph raises an alarm instead of reading 0. |
| Breadboard or perfboard, jumper wires | Use twisted pair or shielded cable for the pressure transducer's signal. |
| Computer | Windows, macOS, Linux or ChromeOS, with **Chrome or Edge** (desktop). |
| For calibration | Room air, a certified CO2 calibration gas (for example 5 %), a 3 L calibration syringe. Optional: a reference flowmeter and a manometer. |
| For any live study | A medical-grade USB isolator, or a laptop running on battery. See [safety](docs/safety.md). |

The exact sensor models are still open questions in [PLAN.md](PLAN.md). Everything below works with any sensors whose outputs stay within 0–5 V; only the calibration numbers change.

---

## 2. Wire the ADC board and sensors

![Wiring diagram: computer to Arduino Uno over USB; Arduino 5V, GND, A4/SDA and A5/SCL to the ADS1115; capnograph, flow sensor and pressure transducer to ADS1115 inputs A0, A1 and A2](docs/images/wiring.svg)

Do all wiring with the USB cable **unplugged**.

### 2.1 ADS1115 to Arduino

| ADS1115 pin | Arduino Uno / Nano | Arduino Mega 2560 | Wire colour in the diagram |
| --- | --- | --- | --- |
| VDD | 5V | 5V | red |
| GND | GND | GND | black |
| SDA | A4 (also labelled SDA on the Uno R3 header) | 20 (SDA) | blue |
| SCL | A5 (also labelled SCL on the Uno R3 header) | 21 (SCL) | purple |
| ADDR | GND | GND | black |
| ALRT | leave unconnected | leave unconnected | |

Wiring ADDR to GND gives the board I2C address 0x48, which is what the firmware expects. If ADDR is left floating or wired elsewhere, the board answers at 0x49–0x4B; the dashboard's hardware check will tell you.

### 2.2 Sensors to ADS1115

| Sensor | Connect | To |
| --- | --- | --- |
| **Capnograph (CO2)** | analog output | ADS1115 **A0** |
| | analog-output ground | **GND** |
| | 1 MΩ resistor | between **A0** and **VDD** (the pull-up) |
| | power | its own supply. Do not power a capnograph from the Arduino. |
| **Flow sensor** | output | ADS1115 **A1** |
| | supply | **5V** (or its own supply, joined to GND) |
| | ground | **GND** |
| **Pressure transducer (pneumotach)** | output | ADS1115 **A2** |
| | supply | **5V** |
| | ground | **GND** |
| | high-pressure port (P1) | tubing to the pneumotach tap on the **upstream / circuit** side |
| | low-pressure port (P2) | tubing to the tap on the **patient** side |
| Spare | | ADS1115 **A3** is free (or the transducer's second output, below) |

With P1 on the circuit side, inspiration (flow toward the patient) gives a positive pressure difference and shows as **positive flow**, which is what breath detection expects. You will check the sign in [step 6](#6-check-the-setup).

### 2.3 Rules that prevent most problems

- **One common ground.** The Arduino, the ADS1115 and every sensor's signal ground must be connected together. A missing ground is the most common cause of noisy or drifting readings.
- **Keep every input between 0 and 5 V.** The ADS1115 can be damaged by any input below −0.3 V or above VDD + 0.3 V. If a sensor can output more than 5 V, put a resistor divider in front of it and include the divider ratio in its calibration.
- **Power budget.** Power from the Arduino's 5V pin comes from USB. Keep what you draw from it small (the pressure transducer and the ADS1115 together use a few tens of mA). Give anything hungrier, such as a heated flow sensor, its own supply, and join its ground to the Arduino's ground.
- **Twist the transducer's signal wire with its ground** (or use shielded cable), and route it away from pumps, heaters and mains wiring.
- **Short I2C wires.** Keep SDA and SCL under about 30 cm.

### 2.4 Variations

| If… | Do this |
| --- | --- |
| Your sensors output 0–3.3 V | In `firmware/CO2Response/config.h` set `ADS_GAIN` to `GAIN_ONE` (±4.096 V, finer resolution). In `web/public/config/channels.json` set `railHighV` to `4.096`. |
| Your transducer has two outputs (differential) | Connect them to A2 and A3, and set `PNEUMO_DIFFERENTIAL 1` in `config.h`. |
| You use an Arduino Mega | SDA is pin 20 and SCL is pin 21. Everything else is the same. |

### 2.5 Before you plug in

With a multimeter, check for continuity between 5V and GND. There must be none: a short there will cut out the USB port. If you can, power each sensor on its own first and measure its output to confirm it stays inside 0–5 V.

---

## 3. Load the firmware onto the Arduino

You only need to do this once per Arduino, and again after changing `config.h`.

### Option A: Arduino IDE (simplest)

1. Install the [Arduino IDE](https://www.arduino.cc/en/software) (version 2).
2. Get the code: on the GitHub repository page, **Code → Download ZIP**, and unzip it (or `git clone` it).
3. In the Arduino IDE, **File → Open** `firmware/CO2Response/CO2Response.ino`.
4. **Tools → Manage Libraries…**, search for **Adafruit ADS1X15**, and install it. Accept its dependencies (Adafruit BusIO).
5. **Tools → Board →** Arduino Uno (or your board). **Tools → Port →** the Arduino's port.
6. Press **Upload** (the right-arrow button). Wait for "Done uploading".
7. Check it: **Tools → Serial Monitor**, set the baud rate to **115200**. You should see:
   - `I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100,…`, then a stream of `D,…` lines. Good.
   - `E,ADC_NOT_FOUND` and `I,I2C_SCAN,…` instead: the ADS1115 is not answering. See [troubleshooting](#10-troubleshooting).
8. **Close the Serial Monitor.** Only one program can use the port at a time, and the dashboard needs it.

### Option B: PlatformIO (command line)

```bash
pip install platformio
```

```bash
cd firmware && pio run -e uno -t upload
```

Use `-e mega` or `-e nano` for those boards. `pio device monitor -b 115200` shows the output.

### Settings in `config.h`

| Setting | Default | Change it when |
| --- | --- | --- |
| `BAUD_RATE` | 115200 | You raise the sample rate above 150 Hz (also change it in the dashboard). |
| `DEFAULT_RATE_HZ` | 100 | You need faster sampling (up to 200). The dashboard can also change the rate live. |
| `ADS_I2C_ADDRESS` | 0x48 | ADDR is not wired to GND. |
| `ADS_GAIN` | `GAIN_TWOTHIRDS` (±6.144 V) | Your sensors are 0–3.3 V (use `GAIN_ONE`). |
| `PNEUMO_DIFFERENTIAL` | 0 | Your transducer has two outputs on A2/A3. |

---

## 4. Open the dashboard

The dashboard runs in **Chrome or Edge on a desktop or laptop**. Safari and Firefox cannot talk to USB devices; for those, use the Python bridge (below).

**Option A, hosted (once GitHub Pages is turned on):** open `https://shrlak.github.io/CO2-Response/`. Loading the page needs internet; once it is open, the data never leaves the computer.

**Option B, on your own computer.** Install [Node.js 22](https://nodejs.org/), then:

```bash
cd web
```

```bash
npm install
```

```bash
npm run dev
```

Open the address it prints, usually `http://localhost:5173`.

**Option C, through Python (Safari, Firefox, a second screen, or a DAQ other than the Arduino):** on the computer the hardware is plugged into:

```bash
pip install -e python
```

```bash
co2r-daq serve --driver arduino --port COM3
```

Use `co2r-daq list` to see port names. Then in the dashboard go to **Settings → Connection → Python acquisition → Connect to Python** (`ws://localhost:8765`).

### Using a different data-acquisition system

The same Python program runs other DAQs instead of the Arduino and ADS1115: NI-DAQmx boards (`--driver nidaq`), LabJack (`--driver labjack`), or any board with a Python library via a small driver file. The sensors connect to the DAQ's first three analog inputs (CO2, flow, pneumotach, in that order), and the monitor, alarms, calibration and recording work unchanged. See **[docs/python-daq.md](docs/python-daq.md)**.

The first time, a banner explains that this is research software; press **I understand**. To try the dashboard without hardware, press **Start simulator**.

---

## 5. Connect the Arduino to the computer

1. Plug the Arduino's USB cable **directly into the computer**, not through a hub.
2. Close the Arduino IDE Serial Monitor, and any other program that might be using the port.
3. In the dashboard, press **Connect Arduino (USB)**. The browser lists serial ports. Pick the Arduino:
   - Windows: "Arduino Uno (COM3)" or similar; clones may show "USB-SERIAL CH340 (COM4)"
   - macOS: "Arduino Uno" or "cu.usbmodem…"; clones may show "cu.wchusbserial…"
   - Linux: "ttyACM0" or "ttyUSB0"
4. Within a second the header shows **Arduino (USB 2341:0043) · 100 Hz · 0 dropped**, and the waveforms start sweeping.

Next time, the start screen offers **Reconnect Arduino (USB …)**, which reopens it with one click and no port list.

**If the cable comes out during a session**, a medium-priority **DEVICE DISCONNECTED** alarm sounds. Plug the same Arduino back in and the dashboard reconnects on its own, and any recording continues in the same file. You can turn this off in **Settings → Connection**.

**No port appears?** See [troubleshooting](#10-troubleshooting). The usual causes are a charge-only cable, a missing CH340 driver for clone boards (Windows/macOS), or, on Linux, not being in the `dialout` group.

---

## 6. Check the setup

Do this after wiring, after moving the setup, and at the start of each study day.

1. **Run the hardware check.** Go to **Settings → Diagnostics → Hardware check**, choose **10 s**, and press **Run hardware check**. Breathe through the sensors (or leave them running) while it runs. Every row should read **PASS**:

   | Check | What PASS means |
   | --- | --- |
   | Firmware handshake | The board runs CO2-Response firmware with an ADS1115 at 0x48 |
   | Sample rate | About 100 Hz per channel |
   | Dropped samples | Under 0.1 % |
   | Corrupt lines | None |
   | CO2 sensor (A0), Flow sensor (A1), Pneumotach transducer (A2) | Not stuck at 0 or 5 V, not clipping, and noise below the limit (shown in mmHg or L/min) |

   Anything marked **FAIL** or **CHECK** comes with what to do. **Save report** keeps a copy for your notes.

2. **For a new setup, run the 10-minute check once.** It is the acceptance test: under 0.1 % dropped samples over 10 minutes.

3. **Check the flow direction.** In **Settings → Display & units**, turn on **Show raw volts**. Go back to the monitor and breathe in through the circuit. Both flow traces should go **up** (positive) on inspiration. If one goes down, swap that sensor's tubing (P1 and P2), or flip the sign of its gain in **Settings → Calibration**.

4. **Check that each sensor responds.** Breathe out through the capnograph's sample line and watch the CO2 trace rise; breathe through the flow sensor and pneumotach and watch both flow traces move.

---

## 7. Calibrate

The dashboard converts volts to real units using the numbers in **Settings → Calibration**. Until you calibrate, it uses placeholder values that are almost certainly wrong for your sensors. Calibrate in this order (full details in [docs/calibration.md](docs/calibration.md)):

1. **Barometric pressure.** In **Settings → Display & units**, set the lab's barometric pressure. Pittsburgh is usually near 735 mmHg, not 760; the difference changes every CO2 reading in mmHg by about 3 %.
2. **CO2: zero and span.** Calibration card **CO2 → Zero & span**:
   1. Flow room air through the sample line and press **Capture** on point A (0.04 %).
   2. Flow the calibration gas, enter its certified value (for example 5.00 %), and press **Capture** on point B.
   3. Press **Compute line**, check the preview, then **Save calibration**.
3. **Flow sensor.** With no flow, press **Enter values → Zero now (no flow)**. If you have a reference flowmeter, use **Two-point** or **Multi-point fit** as well. Then **Save calibration**.
4. **Pneumotach.**
   1. **Transducer:** enter the transducer's datasheet scaling under **Enter values**. For example, for an MPXV7002DP (2.5 V at 0 Pa, 1 V per kPa): gain **1000** Pa/V, offset **−2500** Pa.
   2. With no flow, press **Zero now (no flow)**, then **Save calibration**.
   3. **3 L syringe:** on the **3 L syringe** tab, press **Start stroke**, push the syringe fully in with one smooth stroke, press **Stop**, then **Apply** the new k1 and **Save calibration**.
   4. Push the syringe again and confirm VT on the monitor reads about 3000 mL.
5. **Export a copy.** Press **Export calibration (JSON)** and keep it with your lab notes. Every recording also stores the calibration that was in use.

You can type any calibration in by hand (gain/offset, polynomial, or a lookup table) under **Enter values**.

---

## 8. Run a session

### Before you start

- [ ] A certified monitor is connected as the primary alarm (for any live study).
- [ ] The laptop runs on battery, or the Arduino is behind a USB isolator (for any live study).
- [ ] The computer will not sleep: set its power settings to never sleep while plugged in or on battery, and postpone OS updates. The dashboard asks the screen to stay on while connected, but that does not stop the computer from sleeping.
- [ ] The header shows **100 Hz · 0 dropped**, and the hardware check passed.
- [ ] Each calibration card shows today's date (or a recent one you trust).
- [ ] The study code is set in **Settings → Data & recordings**. **Never use a name, MRN or date of birth.**
- [ ] Alarm limits are set: **Alarm limits** soft key (default: EtCO2 high at 50 mmHg, 2-breath delay).
- [ ] **Arm audio** is pressed, then **Alarm limits → Test alarm**: you see the red banner and hear the high-priority tone, then the medium tone.

### Recording

Press **● Record**. The header shows **● REC** and a timer. The session is saved to the browser every second, so a crash loses at most a second.

To start recording automatically whenever the Arduino connects, turn on **Settings → Data & recordings → Start recording when the Arduino connects**. This suits overnight runs.

### Reading the screen

| Area | Shows |
| --- | --- |
| Header | Study code, connection and sample rate, dropped samples, recording timer, alarm status (`ARMED`, `ALARM`, `SILENCED 1:42`, `AUDIO OFF`), clock |
| Alarm strip | The most important alarm: red flashing = high priority, amber = medium (technical), blue = information. "+N more" lists the rest. |
| CO2 row | Capnogram (yellow, with the high limit dashed), **EtCO2** (large, with its limits beside it), RR, FiCO2 |
| Flow row | Flow-sensor waveform (cyan), live flow, peak inspiratory flow |
| Pneumotach row | Pneumotach flow (green), VT, MV |
| Bottom chart | EtCO2 per breath over 15 min to the whole session, with alarm and event markers. The **Ventilatory response** tab plots MV against EtCO2 with a fitted slope. |

A number shows `---` when its sensor has sent no valid data for 2 s.

### During the session

| To | Do |
| --- | --- |
| Mark an event (e.g. "CO2 step started") | Press **M** (or **Mark event**) and type a label |
| Silence the alarm sound for 2 minutes | **Silence**, or **Acknowledge** on the alarm strip. The visual alarm stays on. |
| Clear an alarm that has resolved | **Clear** on the alarm strip |
| Look closely at the waveforms | **Freeze**. Numbers and alarms keep running underneath. |
| Change alarm limits | **Alarm limits** |

### Alarms and what to do

| Alarm | Priority | Meaning | What to do |
| --- | --- | --- | --- |
| EtCO2 HIGH | high (red) | EtCO2 above the high limit for the trigger delay | Check the subject first, then the primary monitor |
| APNEA | high | No breath detected for 20 s | Check the subject and the airway connection |
| EtCO2 LOW (if set) | medium | EtCO2 below the low limit | Check for a leak, a disconnected sample line, or apnea |
| DEVICE DISCONNECTED | medium (amber) | USB link lost | Reconnect the cable; the dashboard reconnects on its own |
| NO DATA FROM DEVICE | medium | Connected, but nothing received for 3 s | Check the Arduino; unplug and replug it |
| ADC NOT FOUND | medium | The Arduino cannot reach the ADS1115 | Check the ADC wiring (VDD, GND, SDA, SCL, ADDR) |
| … SIGNAL AT ADC RAIL | medium | A sensor reads 0 V or 5 V for 2 s | Sensor unplugged, unpowered or out of range |
| Audible alarm NOT armed | info (blue) | The browser has not been allowed to play sound | Press **Arm audio** |

Alarms **latch**: after the condition clears, the alarm stays as "resolved" until someone presses **Clear**, so a short event is never missed.

**Keep the tab open and the laptop awake.** The alarms work in a background tab, but not if the tab is closed, the browser crashes, or the computer sleeps.

---

## 9. After the session

1. Press **■ Stop rec**. The session is saved as **one CSV file**, `co2r_<study code>_<date>-<time>.csv`, in your Downloads folder, and a copy stays in **Settings → Data & recordings**.
2. Copy the file to the lab's storage. Browser copies are deleted if the browser's site data is cleared.
3. To look at it again, go to **Settings → Data & recordings → Open**. You get the summary, zoomable charts and the event list. Export the whole session, the breath table or the events, for everything or a zoomed time range, or **Replay** it on the monitor.
4. To analyse it, open the file in Excel, MATLAB or R, or use `analysis/load_session.py` with Python. The columns and examples are in [docs/data-format.md](docs/data-format.md).
5. In **Settings → Connection** press **Disconnect**, then unplug the Arduino.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "This browser has no Web Serial" | Not Chrome or Edge on a desktop, or the page is not on `https://` or `localhost` | Use desktop Chrome or Edge on the Pages URL or `localhost`, or use the Python bridge |
| No port in the browser's list | Charge-only USB cable; clone board without its CH340 driver; on Linux, not in `dialout` | Try a data cable. Install the CH340 driver (Windows/macOS). On Linux run `sudo usermod -a -G dialout $USER` and log out and back in. |
| "Failed to open serial port" | Another program has the port | Close the Arduino IDE Serial Monitor, other browser tabs using the board, and any other serial program |
| Connected but nothing arrives; NO DATA alarm | Wrong firmware, wrong baud rate, or the firmware is waiting for the ADC | Check **Settings → Diagnostics → Last raw lines**. If it shows `E,ADC_NOT_FOUND`, see the next rows. Otherwise reflash the firmware and match the baud rate. |
| ADC NOT FOUND, I2C scan "none" | The ADS1115 has no power, or SDA/SCL are not connected | Check VDD to 5V, GND to GND, SDA to A4 and SCL to A5 (20/21 on a Mega) |
| ADC NOT FOUND, I2C scan "0x49" (or 0x4A, 0x4B) | ADDR is not wired to GND | Wire ADDR to GND, or set `ADS_I2C_ADDRESS` in `config.h` to the scanned address and reflash |
| A number shows `---` and "SIGNAL AT ADC RAIL" | The sensor is unplugged, unpowered, or outputting more than 5 V | Check that sensor's power, ground and signal wire. For CO2, an unplugged capnograph reads 5 V because of the pull-up. |
| Readings jump around or drift | Missing common ground, long untwisted wires, nearby pumps or heaters | Join all grounds at one point, twist or shield the signal wires, re-route them, and keep **Low-pass filter** on |
| Flow goes negative when breathing in | Tubing or sensor reversed | Swap P1 and P2, or flip the sign of the gain in **Calibration** |
| EtCO2 lower than the reference monitor | Barometric pressure left at 760, calibration out of date, or a leak in the sample line | Set the real barometric pressure, redo zero and span, and check the sample line |
| VT is wrong | Pneumotach not zeroed, or k1 not calibrated | Zero with no flow, then redo the 3 L syringe calibration |
| Dropped samples above 0.1 % | USB hub, poor cable, overloaded computer, or the tab throttled | Plug in directly, use a short good-quality cable, close heavy programs, keep the tab in front |
| No alarm sound | Audio not armed, system volume muted, or sound going to another output | Press **Arm audio**, check the computer's volume and output device, then **Test alarm** |
| The recording file is not in Downloads | The browser asked where to save, or blocked the download | Export it from **Settings → Data & recordings → Export → Whole session** |
| The computer went to sleep | OS power settings | Set the computer to never sleep, and keep it on mains through an isolator or on battery as your safety plan allows |

Still stuck? **Settings → Diagnostics** shows the last raw lines from the Arduino and the session log. **Download session log** in **Data & recordings** saves it to share.
