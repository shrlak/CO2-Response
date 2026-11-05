# Serial protocol

One protocol is shared by the firmware (`firmware/src/main.cpp`), the dashboard parser
(`web/src/protocol/protocol.ts`), the simulator (`web/src/sim/`) and the optional bridge
(`bridge/serial_ws_bridge.py`). Change it here first.

## Transport

- USB serial, 8N1, **115200 baud** by default (`BAUD_RATE` in `firmware/include/config.h`).
- ASCII text, one message per line, terminated by `\n` (a preceding `\r` is ignored).
- The device only sends **volts**. Every conversion to engineering units happens in the dashboard,
  so recalibrating never requires reflashing.

## Device → host

| Prefix | Meaning | Example |
| --- | --- | --- |
| `D` | Data: one sample of every channel | `D,1042,10420,2.6316,2.5000,2.5000*5C` |
| `I` | Info, sent at boot and in reply to `INFO` | `I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100` |
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
| `v0…vN` | decimal volts | 4 decimals. CH0 = CO2, CH1 = flow sensor, CH2 = pneumotach transducer. Extra channels are allowed and ignored by v1 of the dashboard. |
| `checksum` | 2 hex digits | XOR of every byte **after** the leading `D` and **before** `*` (so it starts with the first comma). Upper or lower case is accepted. |

The host discards a data line, and counts it as a checksum failure, if the checksum is missing or
wrong, or if any field fails to parse.

### Checksum example

For `D,1,10,1.0000,2.0000,3.0000*XX` the checksum covers `,1,10,1.0000,2.0000,3.0000`.

### Info line

`I,` followed by key/value pairs: `FW` firmware version, `ADC` converter type, `CH` channel count,
`RATE` samples per second per channel, and optionally `BAUD`.

### Errors

| Code | Meaning |
| --- | --- |
| `ADC_NOT_FOUND` | The ADC did not answer on I2C at boot. The device sends this once a second and does not stream. |
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

The dashboard sends `INFO` then `START` after opening the port.

## Rates and bandwidth

At 100 Hz a data line is about 45 bytes, so 4.5 kB/s against roughly 11.5 kB/s available at
115200 baud. 200 Hz uses about 80 % of the link; above that, raise the baud rate.
The ADS1115 tops out at 860 conversions per second shared across channels, which limits three
channels to about 250 Hz in practice.
