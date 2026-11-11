# A Capnostream 35 as the CO2 source

The lab's EtCO2 monitor is a [Medtronic Capnostream™ 35](https://www.medtronic.com/en-us/healthcare-professionals/products/patient-monitoring/capnography-monitoring/portable-respiratory-monitors/capnostream-35-portable-respiratory-monitor.html). CO2-Response can take its CO2 from it instead of from the board's own CO2 sensor (the SprintIR-6S):

- The **Capnostream** samples gas from the airway through its Microstream FilterLine (50 mL/min) and measures CO2 20 times a second. It sends what it measures to the computer over its data port.
- The **Arduino** still measures the flow (SFM3304-D) 100 times a second.
- The app puts the two on one timeline. Breaths, EtCO2, VT, MV, the MV-vs-EtCO2 graph, the alarms and the recording then work exactly as with the board's own sensor. The monitor's own numbers (EtCO2, RR, SpO2, pulse) are shown beside the app's, as a cross-check.

> **Research use only.** The Capnostream stays the certified, primary monitor and its own alarms stay on. CO2-Response only reads its data output.

## What is known, and what is not

Medtronic lists the Capnostream 35's data interfaces as:

- wired serial RS-232: Capnostream, SPDout and ASCII protocols
- RS-422: SPDout and ASCII
- wired Ethernet (LAN), and Wi-Fi: Capnostream, SPDout and ASCII

Source: the [Capnostream 35 software page](https://www.medtronic.com/en-us/healthcare-professionals/products/patient-monitoring/capnography-monitoring/portable-respiratory-monitors/capnostream-35-portable-respiratory-monitor/software.html).

**The byte layout of these protocols is not published.** Medtronic's "RS-232 Capnostream Data Transfer Protocols" document comes on the CD with the monitor and is available from Medtronic support (800-633-8766). So CO2-Response reads the **ASCII** protocol with a decoder you can set up without changing any code. By default it finds `KEY=value` pairs and bare numbers on each line by itself; otherwise you give the fields in order. The **capture tool** below records what the monitor sends, so you can see the layout. Send that file, or the protocol document, to whoever maintains this software, and the layout can be built in.

**Not yet tested with a real monitor.** The whole path is tested with a simulated Capnostream (`tests/test_capnostream.py`).

## Hardware

- **Data cable.** Use the monitor's RS-232 cable (Medtronic accessory PM35ACRS232; check the part number with Medtronic) and a USB-to-RS-232 adapter on the computer (FTDI or Prolific chip). Or put the monitor on the network and use its LAN address.
- **Sampling.** The Capnostream has its own sampling pump. Connect its FilterLine to the gas port of the filter, where the SprintIR's sample line went. The board's sample pump (Q2) and the SprintIR are then not needed: leave the SprintIR unplugged, and the board sends its CO2 as "not answering", which the app ignores while the Capnostream is the source.
- **Isolation.** The monitor is mains-powered medical equipment and the laptop is connected to a person through the Arduino. Keep the USB isolator in the Arduino's line, and use an isolated RS-232 adapter (or the network link) for the monitor. See [safety.md](safety.md).

## Setting it up

1. On the Capnostream, turn on its data output in the **ASCII** protocol on the port you use (RS-232 or LAN). The menu is described in its operator's manual. Note the baud rate if it shows one.

2. **Capture what it sends** (about a minute, while it is monitoring someone or a test lung):

   ```bash
   co2response --capture COM5
   ```

   (On macOS the port is `/dev/cu.usbserial-…`, on Linux `/dev/ttyUSB0`, and over the network `socket://192.168.1.50:4001`.) It tries the common baud rates, keeps the one that gives readable text, and saves the lines to `capnostream-capture-<date>.txt`. It then reports how many CO2 waveform samples a second it found (it should be about 20) and which numbers it found.

3. **If the waveform was not found**, open the capture file and work out the layout of a waveform line. Then try your layout on the monitor:

   ```bash
   co2response --capture COM5 --capno-fields "time,co2,etco2,rr,spo2,pr"
   ```

   Field names: `co2` (one waveform sample), `co2*` (every remaining field on the line is a waveform sample), `etco2`, `fico2`, `rr`, `spo2`, `pr`, `ipi`, and `skip` (ignore this field). Fields are split on commas, semicolons, tabs or spaces. `--capno-units` sets the CO2 unit (`mmHg`, `%` or `kPa`).

4. **Use it:**

   ```bash
   co2response --capnostream COM5 --capno-baud 9600
   ```

   This is saved, so from then on every connection to the Arduino takes the CO2 from the Capnostream. `--capnostream off` turns it off. You can also set it in the desktop app (**Settings > Connection > Capnostream 35**) or in the terminal monitor (key `k`).

5. **Check the sample-line delay.** The gas takes about 2.7 s to reach the Capnostream's sensor, and the app delays the flow by the same amount, so each CO2 value is paired with the flow that carried that gas. **Settings > Diagnostics** shows the CO2 lag: how long after expiration starts the CO2 rises. With the delay right it is about 0.2–0.5 s. If it is larger, raise the delay; if it is negative or zero, lower it. Change it with `--capno-delay 2900`, or in the Capnostream card. A longer FilterLine means a longer delay (Medtronic quotes a 2.95 s typical response time, about 5 s with long tubing).

## Trying it without a monitor

```bash
co2response-tui --sim --capnostream sim
```

The simulator then sends its CO2 through a simulated Capnostream: `CO2=…` lines 20 times a second, 2.7 s late, and `ETCO2=… RR=… SPO2=… PR=…` once a second. They go through the same decoder and merge as a real monitor's lines. In the desktop app, choose **Simulated** in **Settings > Connection > Capnostream 35** and start the simulator.

## What you see

- The CO2 waveform, EtCO2 and the alarms come from the Capnostream's CO2.
- Beside the app's EtCO2 and RR: "Capnostream 38", the monitor's own value. Small differences are normal: the app takes EtCO2 per breath, and the monitor applies its own algorithm.
- The device line in Diagnostics, and every recording, say `CO2,CAPNOSTREAM35`.
- If the monitor's data stops for a second: CO2 reads `---` and the medium alarm **CAPNOSTREAM NOT ANSWERING** sounds. The app waits up to 45 s after connecting before this alarm, because the monitor warms up for about 40 s.

## How it works (`co2response/sources/capnostream.py`)

- `CapnostreamSource` reads the monitor's port on a background thread. Lines may end in CR, LF or both.
- `CapnoDecoder` turns each line into CO2 samples (mmHg) and numbers.
- `CapnoMerge` stamps each CO2 sample with the time it arrived, spaced at 50 ms. It maps the Arduino's clock onto the computer's, using the smallest transfer delay seen. It holds each Arduino data line until the CO2 of that moment has arrived (at most 0.6 s), then writes the interpolated CO2 into the line's CO2 value (as %, the unit the Uno R4 board's lines carry). From there the line goes through the usual parser, processing and recorder. The processing's CO2 delay is set to the Capnostream's sample-line delay while it is the source.
- Only the Uno R4 board, whose lines carry engineering units, is supported. With the ADC board the app says so in the log and leaves its lines alone.
