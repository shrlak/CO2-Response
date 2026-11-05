# Serial protocol

One protocol is shared by the firmware (`firmware/CO2Response/`), the monitor app's parser
(`co2response/protocol.py`), the simulator (`co2response/sim/`) and the optional bridge
(`bridge/serial_ws_bridge.py`). Change it here first. `tests/test_protocol.py` and
`firmware/test/test_line_format.cpp` check the same example lines, so the app and the
firmware stay byte-for-byte compatible.

## Transport

- USB serial, 8N1, **115200 baud** by default (`BAUD_RATE` in `firmware/CO2Response/config.h`).
- ASCII text, one message per line, terminated by `\n` (a preceding `\r` is ignored).
- The device only sends **volts**. Every conversion to engineering units happens in the monitor app,
  so recalibrating never requires reflashing.

## Device → host

| Prefix | Meaning | Example |
| --- | --- | --- |
| `D` | Data: one sample of every channel | `D,1042,10420,2.6316,2.5000,2.5000*5C` |
| `I` | Info, sent at boot and in reply to `INFO` | `I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144` |
| `E` | Error | `E,ADC_NOT_FOUND` |
| `A` | Acknowledgement of a command | `A,RATE,100` |

### Data line

```
D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>
```

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
`RATE` samples per second per channel, and optionally `BAUD`, `ADDR` (the ADC's I2C address) and
`FSR` (the ADC's full-scale input range in volts, set by the PGA gain).

When the ADC does not answer, the firmware also sends an I2C scan, `I,I2C_SCAN,<addresses>`, with
the addresses that did answer separated by spaces (or `none`). The app shows it in
Diagnostics and in the hardware check. An ADS1115 answers at 0x48 (ADDR to GND), 0x49 (VDD),
0x4A (SDA) or 0x4B (SCL).

### Errors

| Code | Meaning |
| --- | --- |
| `ADC_NOT_FOUND` | The ADC did not answer on I2C. The device sends this once a second (with an `I2C_SCAN` every 5 s), keeps retrying, and does not stream until the ADC answers. |
| `BAD_RATE` | `RATE` was outside the supported range (1–200 Hz). |
| `UNKNOWN_CMD` | The command was not recognized. |
| `OVERRUN` | Sampling fell behind schedule; slots were skipped (also visible as `seq` gaps). |

## Host → device

Plain text commands, one per line, case-insensitive.

| Command | Reply | Effect |
| --- | --- | --- |
| `INFO` | `I,…` | Report firmware, ADC, channels and rate. |
| `START` | `A,START` | Start streaming data lines. |
| `STOP` | `A,STOP` | Stop streaming. |
| `RATE <hz>` | `A,RATE,<hz>` or `E,BAD_RATE` | Set samples per second per channel (1–200). |

The app sends `INFO` then `START` after opening the port. Opening the port resets an Uno, so if no `I,…` line has arrived 2.5 s later the app sends them once more.

## Rates and bandwidth

At 100 Hz a data line is about 45 bytes, so 4.5 kB/s against roughly 11.5 kB/s available at
115200 baud. 200 Hz uses about 80 % of the link; above that, raise the baud rate.
The ADS1115 tops out at 860 conversions per second shared across channels. With three channels
read in single-shot mode (about 1.5 ms each including I2C), a sample set takes about 4.5 ms, so
200 Hz is the practical ceiling and 100 Hz leaves comfortable margin. If the firmware falls behind
it skips slots, advances `seq` and sends `E,OVERRUN` (at most once a second).
