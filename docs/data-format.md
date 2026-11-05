# Session files: saving, importing, exporting and analysis

Every recording is saved as **one CSV file** that holds everything about the session: every sample of every channel (raw volts and calibrated values), every breath, every alarm, every marker, and the calibration and settings that were in use. Excel, MATLAB, Python and R all open it directly.

## Saving

1. Press **Record** on the monitor (or **Start recording** in **Settings > Data & recordings**).
2. While it records, the session is written to the browser's storage every second. If the tab crashes or the laptop restarts, at most the last second is lost; the next time the app opens it says it recovered the recording.
3. Press **Stop rec**. The session is saved as `co2r_<study code>_<YYYYMMDD-HHMMSS>.csv` to your Downloads folder. It is also kept in **Data & recordings**, so you can save it again later.

Sessions kept in the browser are deleted if the browser's site data is cleared. Export anything you need to keep, and press **Keep when disk is low** once so the browser does not evict them on its own.

## Importing

**Settings > Data & recordings > Import session files…** (or drag files onto the Import card). Each file is checked as it is read; a CSV that is not a session file is rejected and nothing is kept. Imported sessions can be opened, replayed on the monitor and exported like recorded ones. Files made by the first version of the app (format v1) import too and are converted to v2 when exported.

## Reviewing

**Open** a session to see:

- a summary: length, breaths, mean/min/max EtCO2, mean RR, VT and MV, alarms, markers, sample rate and dropped samples
- EtCO2 and RR per breath, VT and MV per breath, and the CO2/flow/pneumotach waveforms; drag across any chart to zoom all three
- the alarms and events list; click a time to zoom to 30 s either side
- **Replay on the monitor**, which plays the raw volts back through the live processing and alarms

## Exporting

| Export | What you get |
| --- | --- |
| Whole session (one .csv) | The complete session file, exactly as recorded |
| Breaths (.csv) | One row per breath: `breath, time_s, wall_time_iso, etco2_mmHg, fico2_mmHg, rr_bpm, vt_ml, mv_lpm, peak_insp_lpm` |
| Alarms & events (.csv) | `time_s, wall_time_iso, type, event` |
| Zoomed range | The same three exports, limited to the time range you zoomed to |

## File layout (format v2)

```
# CO2-Response session,v2
# study_code,STUDY-01
# started,2026-11-05T05:00:00.000Z
# app_version,0.2.0
# source,Arduino (USB 2341:0043)
# firmware,1.0.0
# rate_hz,100
# time_base,...
# units,...
# channels,{...calibration JSON...}
# processing,{...}
# alarm_settings,{...}
time_s,wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,co2_pct,flow_lpm,pneumo_pa,pneumo_lpm,breath,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm,event
0.010,2026-11-05T05:00:00.010Z,10,0,2.6316,2.5000,2.5000,38.00,5.000,0.00,0.0,0.00,,,,,,,,
...
4.050,2026-11-05T05:00:04.050Z,4060,405,0.7642,2.6190,2.6270,18.82,2.476,3.05,15.2,3.04,1,38.0,0.0,15.0,498,7.49,35.4,
```

- Lines starting with `#` are metadata. Free text never contains `#`, so `comment='#'` readers are safe.
- **Every row has the same 20 columns.** One row per sample.
- **Breath columns** (`breath` to `peak_insp_lpm`) are filled only on the sample where that breath ended; they are empty on other rows.
- **`event`** holds alarms (`ALARM_START`, `ALARM_ACKNOWLEDGED`, `ALARM_SILENCED`, `ALARM_RESOLVED`, `ALARM_CLEARED`, `ALARM_REARMED`), markers (`MARK <label>`), connections, device errors and calibration changes on the row where they happened. Several on one row are joined with ` | `. An event while no data was flowing gets a row with only `time_s`, `wall_time_iso` and `event`.
- **`time_s`** is seconds since the recording started, from the Arduino's clock, so samples are exactly 1/rate apart. `wall_time_iso` is the same instant on the computer's clock. `t_ms` and `seq` are the raw device clock and 16-bit sequence number (gaps in `seq` are dropped samples).
- `v0`–`v2` are the raw ADC volts (CO2, flow, pneumotach), so the session can be re-processed later with a different calibration.

| Column | Unit |
| --- | --- |
| `v0`, `v1`, `v2` | V |
| `co2_mmHg`, `etco2_mmHg`, `fico2_mmHg` | mmHg |
| `co2_pct` | % |
| `flow_lpm`, `pneumo_lpm`, `peak_insp_lpm`, `mv_lpm` | L/min |
| `pneumo_pa` | Pa |
| `vt_ml` | mL |
| `rr_bpm` | breaths/min |

## Analysis examples

### Python (pandas)

`analysis/load_session.py` splits a file into samples, breaths and events:

```python
import sys; sys.path.append("analysis")
from load_session import load_session

s = load_session("co2r_STUDY-01_20261105-010203.csv")
s.breaths.plot(x="time_s", y=["etco2_mmHg", "mv_lpm"], secondary_y="mv_lpm")
alarms = s.events[s.events.type == "ALARM_START"]
```

Or by hand:

```python
import pandas as pd
df = pd.read_csv(path, comment="#")
samples = df[df.t_ms.notna()]
breaths = df[df.breath.notna()]
events  = df[df.event.notna()]
```

From the command line, `python analysis/load_session.py file.csv --breaths breaths.csv` prints a summary and writes the breath table.

### MATLAB

```matlab
T = readtable('co2r_STUDY-01_20261105-010203.csv', 'CommentStyle', '#', 'TextType', 'string');
breaths = T(~isnan(T.breath), :);
plot(breaths.time_s, breaths.etco2_mmHg)
```

(Not tested here; MATLAB was not available to check it.)

### Excel

Open the file directly. The `#` lines appear as text rows at the top. Filter the `breath` column to non-blank rows to get one row per breath, or export **Breaths (.csv)** from the app for a smaller sheet. Excel stops at 1,048,576 rows, which is about 2.9 hours at 100 Hz, so for longer sessions use the breath export or Python/MATLAB for the raw samples.

### Ventilatory response

The slope of MV against EtCO2 (L/min per mmHg) for a rebreathing step:

```python
import numpy as np
step = s.breaths[(s.breaths.time_s > t_start) & (s.breaths.time_s < t_end)]
slope, intercept = np.polyfit(step.etco2_mmHg, step.mv_lpm, 1)
```
