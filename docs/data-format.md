# Session files: saving, importing, exporting and analysis

Every recording is saved as **one CSV file** that holds everything about the session: every sample of every channel (raw volts and calibrated values), every breath, every alarm, every marker, and the calibration and settings that were in use. Excel, MATLAB, Python and R all open it directly.

## Saving

1. Press **Record** on the monitor (or **Start recording** in **Settings > Data & recordings**).
2. While it records, the file `co2r_<study code>_<YYYYMMDD-HHMMSS>.csv` is written to the sessions folder every second (**Documents/CO2-Response/sessions** unless you pick another one in **Data & recordings**). If the app crashes or the laptop restarts, at most the last second is lost; the next time the app opens it lists the recording as **Recovered**.
3. Press **Stop rec**. The file is finished and listed in **Data & recordings**, where you can open, export, replay or delete it.

The sessions folder holds the CSV files themselves plus a small `index.json` with each session's status and summary. Copy the CSVs to the lab's storage like any other data file.

## Importing

**Settings > Data & recordings > Import session files…** (or drag files onto the Import card). Each file is checked as it is read; a CSV that is not a session file is rejected and nothing is kept. Imported sessions can be opened, replayed on the monitor and exported like recorded ones. Files made by earlier versions of the app import too (format v1 and v2) and are written as v3 when exported. Breaths in a v1/v2 file have no quality yet, so they are screened as they are read (for isolated EtCO2 jumps and impossible values; noise and partial breaths can only be judged live, from the samples).

## Reviewing

**Open** a session to see:

- a summary: length, breaths (and how many were left out as noisy), mean/min/max EtCO2, mean RR, VT and MV, alarms, markers, sample rate and dropped samples
- EtCO2 and RR per breath, VT and MV per breath (clean breaths only, see [Noise removal](#noise-removal)), and the CO2/flow/pneumotach waveforms; drag across any chart to zoom all three
- the alarms and events list; click a time to zoom to 30 s either side
- **Replay on the monitor**, which plays the raw volts back through the live processing and alarms

## Exporting

| Export | What you get |
| --- | --- |
| Whole session (one .csv) | The complete session file, exactly as recorded |
| Breaths (.csv) | One row per breath: `breath, time_s, wall_time_iso, etco2_mmHg, fico2_mmHg, rr_bpm, vt_ml, mv_lpm, peak_insp_lpm, quality`. Every breath is listed; filter `quality == ok` for the clean ones |
| Alarms & events (.csv) | `time_s, wall_time_iso, type, event` |
| Zoomed range | The same three exports, limited to the time range you zoomed to |

## File layout (format v3)

```
# CO2-Response session,v3
# research use only,not for clinical decision-making
# study_code,STUDY-01
# started,2026-11-05T05:00:00.000Z
# app_version,0.3.0
# source,Arduino (USB 2341:0043)
# firmware,1.0.0
# rate_hz,100
# time_base,...
# units,...
# channels,{...calibration JSON...}
# processing,{...}
# alarm_settings,{...}
time_s,wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,co2_pct,flow_lpm,pneumo_pa,pneumo_lpm,breath,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm,breath_quality,event
0.010,2026-11-05T05:00:00.010Z,10,0,2.6316,2.5000,2.5000,38.00,5.000,0.00,0.0,0.00,,,,,,,,,
...
4.050,2026-11-05T05:00:04.050Z,4060,405,0.7642,2.6190,2.6270,18.82,2.476,3.05,15.2,3.04,1,38.0,0.0,15.0,498,7.49,35.4,ok,
...
12.110,2026-11-05T05:00:12.110Z,12060,1205,0.7618,2.6258,2.6251,19.15,2.519,2.98,15.4,3.07,3,25.7,0.0,14.9,173,7.42,12.5,artifact,
```

- Lines starting with `#` are metadata. Free text never contains `#`, so `comment='#'` readers are safe.
- **Every row has the same 21 columns.** One row per sample.
- **Breath columns** (`breath` to `breath_quality`) are filled only on the sample where that breath ended; they are empty on other rows.
- **`breath_quality`** is the result of the breath screen: `ok`, `noisy`, `artifact` or `outlier` (see [Noise removal](#noise-removal)). Only `ok` breaths count in the numbers, the trend, the response plot and the EtCO2 alarms. The others stay in the file so nothing is lost.
- **`event`** holds alarms (`ALARM_START`, `ALARM_ACKNOWLEDGED`, `ALARM_SILENCED`, `ALARM_RESOLVED`, `ALARM_CLEARED`, `ALARM_REARMED`), markers (`MARK <label>`), breaths found to be isolated jumps one breath later (`BREATH_REJECTED <n> outlier: <reason>`, which overrides that breath's `breath_quality`), connections, device errors and calibration changes on the row where they happened. Several on one row are joined with ` | `. An event while no data was flowing gets a row with only `time_s`, `wall_time_iso` and `event`.
- **`time_s`** is seconds since the recording started, from the Arduino's clock, so samples are exactly 1/rate apart. `wall_time_iso` is the same instant on the computer's clock. `t_ms` and `seq` are the raw device clock and 16-bit sequence number (gaps in `seq` are dropped samples).
- `v0`–`v2` are the raw ADC volts (CO2, flow, pneumotach), untouched by any noise removal, so the session can be re-processed later with a different calibration or different cleaning.
- The calibrated columns (`co2_mmHg` … `pneumo_lpm`) are the cleaned signal: spikes removed and low-pass filtered, as shown on the monitor. Because the spike filter looks 3 samples ahead, they lag the raw volts by 30 ms at 100 Hz; `time_s` is the time of the raw sample.

| Column | Unit |
| --- | --- |
| `v0`, `v1`, `v2` | V |
| `co2_mmHg`, `etco2_mmHg`, `fico2_mmHg` | mmHg |
| `co2_pct` | % |
| `flow_lpm`, `pneumo_lpm`, `peak_insp_lpm`, `mv_lpm` | L/min |
| `pneumo_pa` | Pa |
| `vt_ml` | mL |
| `rr_bpm` | breaths/min |

## Noise removal

Noise is removed before anything is interpreted, both live and when a recording is analysed. The steps run in this order:

1. **Spike removal** (Hampel filter, **Remove spikes** in Settings > Display & units). Each sample is compared with the median of the 7 samples around it. If it is more than 3.5 robust standard deviations away, it is replaced with that median. Single glitchy samples (ADC hiccups, electrical pickup, a bumped cable) disappear. Real fast changes such as the capnogram upstroke are kept, because the median follows a monotonic edge. Adds 30 ms of delay. No samples are dropped.
2. **Low-pass filter**: a 2nd-order Butterworth at 10 Hz (adjustable) on every channel.
3. **Breath detection** on the cleaned flow (or CO2) signal.
4. **Breath screen** (**Leave noisy breaths out**). Each finished breath is checked and given a `breath_quality`:

| Quality | Meaning |
| --- | --- |
| `ok` | Used everywhere |
| `noisy` | Signal-to-noise below 8: CO2 noise (measured sample to sample during that breath, before filtering) above 1/8 of the breath's CO2 amplitude and above 1 mmHg, or flow noise above 1/8 of peak inspiratory flow and above 1.5 L/min |
| `artifact` | A partial breath with no alveolar plateau (less than 10 % of expiration within 90 % of EtCO2: a shallow breath, sample-line kink or cough), an impossible EtCO2 (outside −2 to 120 mmHg), or a tidal volume under 1 mL |
| `outlier` | An isolated EtCO2 jump: more than 5 mmHg and 3× the usual breath-to-breath change away from both the breath before and the breath after. A real step (the start of rebreathing) is kept, because the next breath stays at the new level. Known one breath later, so it is written as a `BREATH_REJECTED` event |

Breaths that are not `ok` are left out of the EtCO2/RR/VT/MV numbers, the EtCO2 trend, the MV-versus-EtCO2 response plot, the review summary and charts, the EtCO2 alarms and `s.breaths` in `analysis/load_session.py`. They stay in the recording, marked. If 3 of the last 5 breaths are rejected as noisy or partial, the **SIGNAL TOO NOISY** technical alarm (medium priority) sounds instead of false CO2 alarms; it clears when no more than 1 of the last 5 is rejected. A flat CO2 trace (sample line off) is not called noisy, so the low-CO2 and apnea alarms still see it.

Turn both switches off to see the signal exactly as it arrives, for example while calibrating.

## Analysis examples

### Python (pandas)

`analysis/load_session.py` splits a file into samples, breaths and events:

```python
import sys; sys.path.append("analysis")
from load_session import load_session

s = load_session("co2r_STUDY-01_20261105-010203.csv")
s.breaths.plot(x="time_s", y=["etco2_mmHg", "mv_lpm"], secondary_y="mv_lpm")   # clean breaths only
alarms = s.events[s.events.type == "ALARM_START"]
s.breaths_all.breath_quality.value_counts()                                     # every breath, with its quality
```

`load_session(path, clean=False)` keeps every breath in `s.breaths`.

Or by hand:

```python
import pandas as pd
df = pd.read_csv(path, comment="#")
samples = df[df.t_ms.notna()]
breaths = df[df.breath.notna() & (df.breath_quality == "ok")]   # clean breaths (see BREATH_REJECTED events too)
events  = df[df.event.notna()]
```

From the command line, `python analysis/load_session.py file.csv --breaths breaths.csv` prints a summary and writes the clean breath table (`--all-breaths` keeps the rejected ones, marked).

### MATLAB

```matlab
T = readtable('co2r_STUDY-01_20261105-010203.csv', 'CommentStyle', '#', 'TextType', 'string');
breaths = T(~isnan(T.breath) & T.breath_quality == "ok", :);
plot(breaths.time_s, breaths.etco2_mmHg)
```

(Not tested here; MATLAB was not available to check it.)

### Excel

Open the file directly. The `#` lines appear as text rows at the top. Filter the `breath` column to non-blank rows and `breath_quality` to `ok` to get one row per clean breath, or export **Breaths (.csv)** from the app for a smaller sheet. Excel stops at 1,048,576 rows, which is about 2.9 hours at 100 Hz, so for longer sessions use the breath export or Python/MATLAB for the raw samples.

### Ventilatory response

The slope of MV against EtCO2 (L/min per mmHg) for a rebreathing step:

```python
import numpy as np
step = s.breaths[(s.breaths.time_s > t_start) & (s.breaths.time_s < t_end)]   # clean breaths only
slope, intercept = np.polyfit(step.etco2_mmHg, step.mv_lpm, 1)
```
