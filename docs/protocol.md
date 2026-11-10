# Serial protocol

One protocol is shared by the firmware (`firmware/CO2Response/`), the monitor app's parser
(`co2response/protocol.py`), its simulator (`co2response/sim/`) and the Python acquisition
package (`python/co2r/protocol.py`), which also produces it for other DAQs (NI, LabJack) and is
what `bridge/serial_ws_bridge.py` runs. Change it here first.

`docs/protocol-fixtures.json` holds shared test vectors. The monitor app (`tests/test_protocol.py`),
the firmware's formatter (`firmware/test/test_line_format.cpp`) and the Python acquisition package
(`python/tests/test_protocol.py`) are all tested against them, so their lines stay byte-identical.
Numbers are formatted with 4 decimals, rounding halves away from zero (as the firmware's `lroundf`
does; Python's own formatting would round halves to even, so both Python packages use their own
`to_fixed`).

## Transport

- USB serial, 8N1, **115200 baud** by default (`BAUD_RATE` in `firmware/CO2Response/config.h`).
- ASCII text, one message per line, terminated by `\n` (a preceding `\r` is ignored).
- The device only sends **volts**. Every conversion to engineering units happens in the monitor app,
  so recalibrating never requires reflashing.

## Device → host

| Prefix | Meaning | Example |
| --- | --- | --- |
| `D` | Data: one sample of every channel | `D,1042,10420,2.6316,2.5000,2.5000*5C` |
| `I` | Info, sent at boot and in reply to `INFO` | `I,FW,1.2.0,ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144,WD,3000,VALVE,1` |
| `E` | Error | `E,ADC_NOT_FOUND` |
| `A` | Acknowledgement of a command | `A,RATE,100` |

### Data line

```
D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>
```

Firmware built with `SPO2_CHANNEL 1` sends a fourth voltage, `<v3>` (a pulse oximeter's analog
output on A3), and says `CH,4` in its info line. The host reads as many voltages as the line has.

| Field | Type | Notes |
| --- | --- | --- |
| `seq` | 0–65535 | Increments by 1 per **scheduled** sample slot and wraps to 0. A skipped slot still advances `seq`, so the host can count dropped samples from gaps. |
| `t_ms` | uint32 | Device `millis()` at the sample. Processing uses this clock for integration (`dt`). |
| `v0…vN` | decimal volts | 4 decimals. CH0 = CO2, CH1 = flow sensor, CH2 = pneumotach transducer. Extra channels are allowed and ignored by the app. |
| `checksum` | 2 hex digits | XOR of every byte **after** the leading `D` and **before** `*` (so it starts with the first comma). Upper or lower case is accepted. |

The host discards a data line, and counts it as a checksum failure, if the checksum is missing or
wrong, or if any field fails to parse.

### Checksum example

For `D,1,10,1.0000,2.0000,3.0000*XX` the checksum covers `,1,10,1.0000,2.0000,3.0000`.

### Info line

`I,` followed by key/value pairs: `FW` firmware version, `ADC` converter type, `CH` channel count,
`RATE` samples per second per channel, and optionally `BAUD`, `ADDR` (the ADC's I2C address),
`FSR` (the ADC's full-scale input range in volts, set by the PGA gain), `WD` (the host
watchdog's timeout in ms; its presence tells the host to send heartbeats, see below) and
`VALVE,1` (the board has a valve output, see below).

When the ADC does not answer, the firmware also sends an I2C scan, `I,I2C_SCAN,<addresses>`, with
the addresses that did answer separated by spaces (or `none`). The app shows it in
Diagnostics and in the hardware check. An ADS1115 answers at 0x48 (ADDR to GND), 0x49 (VDD),
0x4A (SDA) or 0x4B (SCL).

### Errors

| Code | Meaning |
| --- | --- |
| `ADC_NOT_FOUND[,reason]` | The ADC did not answer on I2C (or, from the Python package, the DAQ could not be opened; the reason follows the code). The device sends this once a second (with an `I2C_SCAN` every 5 s), keeps retrying, and does not stream until the ADC answers. |
| `BAD_RATE` | `RATE` was outside the supported range (1–200 Hz). |
| `UNKNOWN_CMD` | The command was not recognized. |
| `OVERRUN` | Sampling fell behind schedule; slots were skipped (also visible as `seq` gaps). |
| `HOST_LOST` | The host's heartbeat stopped (see below). Sent once a second while the buzzer sounds. |

## Host → device

Plain text commands, one per line, case-insensitive.

| Command | Reply | Effect |
| --- | --- | --- |
| `INFO` | `I,…` | Report firmware, ADC, channels and rate. |
| `START` | `A,START` | Start streaming data lines. |
| `STOP` | `A,STOP` | Stop streaming. |
| `RATE <hz>` | `A,RATE,<hz>` or `E,BAD_RATE` | Set samples per second per channel (1–200). |
| `HB` | none | Heartbeat: the host is alive. Arms the watchdog on first use. |
| `HB OFF` | `A,HB,OFF` | The host is disconnecting on purpose: disarm the watchdog. |
| `VALVE 1` | `A,VALVE,1` (or `A,VALVE,0` if the heartbeat is lost) | Valve output to the bag (rebreathing). |
| `VALVE 0` | `A,VALVE,0` | Valve output to room air. |

The app sends `INFO` then `START` after opening the port. Opening the port resets an Uno, so if no `I,…` line has arrived 2.5 s later the app sends them once more.

## Host watchdog (heartbeat)

Firmware 1.1.0 and later. All CO2 alarms are checked in the monitor app, so if the app stops
(closed, crashed, the computer asleep, a browser tab in the background) nothing is watching.
The board therefore watches the app:

- The info line carries `WD,<ms>` (3000 by default; `WATCHDOG_TIMEOUT_MS` in `config.h`, 0 turns
  the watchdog off and removes the field).
- A host that sees `WD` sends `HB` once a second. There is no reply.
- The first `HB` arms the watchdog. If no `HB` arrives for `<ms>`, the board sounds its buzzer
  (`BUZZER_PIN`, 250 ms pulses), blinks its LED and sends `E,HOST_LOST` once a second, until the
  next `HB`.
- On a deliberate disconnect the host sends `HB OFF` (then `STOP`), so closing the app normally
  is silent.

A host that never sends `HB` (older software, a serial terminal) never arms the watchdog, and
older firmware (no `WD` field) is never sent `HB`, so mixed versions keep working. Opening the
serial port resets an Uno, which also disarms the watchdog until the next `HB`.

## Valve output (automatic stop)

Firmware 1.2.0 and later, when `VALVE_PIN` is set (pin 7 by default; -1 for none). The info
line then carries `VALVE,1`.

- The pin is HIGH only while the subject should breathe from the bag. The valve must be wired so
  that LOW, and no power, mean **room air**.
- `VALVE 1` is sent by the app at **Start rebreathing** and `VALVE 0` at **End test**, when the
  stop alarm, the apnea alarm or the low-SpO2 alarm starts, and at an **emergency stop**. The app does this only when
  **Switch the valve from the app** is on (Settings > Alarms); the emergency stop always sends
  `VALVE 0`.
- The board returns to room air by itself at power-up, at reset, and when the host's heartbeat
  is lost (it then also sends `A,VALVE,0`). `VALVE 1` is refused while the heartbeat is lost.
- Before a deliberate disconnect the app sends `VALVE 0`, then `HB OFF`, then `STOP`. It sends
  `VALVE 0` whatever it believes the valve is doing.
- The board answers each command with `A,VALVE,<0|1>`. The app matches answers to the commands
  it has sent: an answer that newer commands have already overtaken is not taken for the valve
  moving, while an `A,VALVE,0` nobody asked for (the watchdog) is believed and recorded.

A board without a valve output answers `E,NO_VALVE` and has no `VALVE` field, so the app never
sends it the command.

## Rates and bandwidth

At 100 Hz a data line is about 45 bytes, so 4.5 kB/s against roughly 11.5 kB/s available at
115200 baud. 200 Hz uses about 80 % of the link; above that, raise the baud rate.
The ADS1115 tops out at 860 conversions per second shared across channels. With three channels
read in single-shot mode (about 1.5 ms each including I2C), a sample set takes about 4.5 ms, so
200 Hz is the practical ceiling and 100 Hz leaves comfortable margin. If the firmware falls behind
it skips slots, advances `seq` and sends `E,OVERRUN` (at most once a second).
