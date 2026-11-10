# Session files: saving, importing, exporting and analysis

Every recording is saved as **one CSV file** that holds everything about the session: every sample of every channel (raw volts and calibrated values), every breath, every alarm, every marker, and the calibration and settings that were in use. Excel, MATLAB, Python and R all open it directly.

## Saving

1. Press **Record** on the monitor (or **Start recording** in **Settings > Data & recordings**).
2. While it records, the file `co2r_<study code>_<YYYYMMDD-HHMMSS>.csv` is written to the sessions folder every second (**Documents/CO2-Response/sessions** unless you pick another one in **Data & recordings**). If the app crashes or the laptop restarts, at most the last second is lost; the next time the app opens it lists the recording as **Recovered**.
3. Press **Stop rec**. The file is finished and listed in **Data & recordings**, where you can open, export, replay or delete it.

The sessions folder holds the CSV files themselves plus a small `index.json` with each session's status and summary. Copy the CSVs to the lab's storage like any other data file.

## Importing

**Settings > Data & recordings > Import session files…** (or drag files onto the Import card). Each file is checked as it is read; a CSV that is not a session file is rejected and nothing is kept. Imported sessions can be opened, replayed on the monitor and exported like recorded ones. Files made by earlier versions of the app import too (formats v1 to v3) and are written as v4 when exported. A v1 to v3 file has no breath-length column, so each breath's length is taken from the time between breath rows. Breaths in a v1/v2 file have no quality yet, so they are screened as they are read (for isolated EtCO2 jumps and impossible values; noise and partial breaths can only be judged live, from the samples).

## Reviewing

**Open** a session to see:

- a summary: length, breaths (and how many were left out as noisy), mean/min/max EtCO2, mean RR, VT and MV, alarms, markers, sample rate and dropped samples
- EtCO2 and RR per breath, VT and MV per breath (clean breaths only, see [Noise removal](#noise-removal)), and the CO2/flow/pneumotach waveforms; drag across any chart to zoom all three
- the alarms and events list; click a time to zoom to 30 s either side
- the **CO2 response**: slope, r, x-intercept and the baseline averages, worked out the way the live monitor does ([Ventilatory response](#ventilatory-response))
- **Replay on the monitor**, which plays the raw volts back through the live processing and alarms. The recording's own markers are replayed too, so a rebreathing test is split and fitted as it was live

A long recording (over about 4 MB, a few minutes) is opened in small steps, so the monitor keeps polling the device and checking alarms while the file is read. An hour of recording is about 39 MB and takes about a second to read on a laptop.

## Exporting

| Export | What you get |
| --- | --- |
| Whole session (one .csv) | The complete session file, exactly as recorded |
| Breaths (.csv) | One row per breath: `breath, time_s, wall_time_iso, etco2_mmHg, fico2_mmHg, rr_bpm, vt_ml, mv_lpm, peak_insp_lpm, period_s, vte_ml, mv_breath_lpm, quality`. Every breath is listed; filter `quality == ok` for the clean ones |
| Alarms & events (.csv) | `time_s, wall_time_iso, type, event` |
| CO2 response report (.pdf) | The test report: results, the response plot, EtCO2 and MV against time, how it was measured, and a table of every breath saying how it was used ([The test report](#the-test-report)) |
| Zoomed range | The same three exports, limited to the time range you zoomed to |

## File layout (format v4)

```
# CO2-Response session,v4
# research use only,not for clinical decision-making
# study_code,STUDY-01
# started,2026-11-05T05:00:00.000Z
# app_version,0.4.0
# source,Arduino (USB 2341:0043)
# firmware,1.1.0
# rate_hz,100
# time_base,...
# units,...
# channels,{...calibration JSON...}
# processing,{...}
# alarm_settings,{...}
# test,{"responseSkipSec":30,"heightCm":null,"weightKg":null,"ageYears":null}
time_s,wall_time_iso,t_ms,seq,v0,v1,v2,co2_mmHg,co2_pct,flow_lpm,pneumo_pa,pneumo_lpm,breath,etco2_mmHg,fico2_mmHg,rr_bpm,vt_ml,mv_lpm,peak_insp_lpm,period_s,vte_ml,breath_quality,event
0.070,2026-11-05T05:00:00.070Z,20,1,1.6738,2.5315,2.5361,30.67,4.035,0.32,5.6,1.11,,,,,,,,,,,
...
4.120,2026-11-05T05:00:04.120Z,4070,406,0.6037,2.6499,2.6431,15.04,1.979,3.99,19.3,3.87,1,38.0,0.0,15.0,500,7.47,35.5,4.010,497,ok,
...
12.180,2026-11-05T05:00:12.180Z,12130,1212,...,3,25.7,0.0,15.0,173,7.42,12.5,3.930,172,artifact,
```

- Lines starting with `#` are metadata. Free text never contains `#`, so `comment='#'` readers are safe.
- **Every row has the same 23 columns.** One row per sample. `period_s` and `vte_ml` are new in v4.
- **Breath columns** (`breath` to `breath_quality`) are filled only on the sample where that breath ended; they are empty on other rows.
- **`breath_quality`** is the result of the breath screen: `ok`, `noisy`, `artifact` or `outlier` (see [Noise removal](#noise-removal)). Only `ok` breaths count in the numbers, the trend, the response plot and the EtCO2 alarms. The others stay in the file so nothing is lost. A breath the operator took out of the response fit by hand reads back as `excluded` (see `BREATH_EXCLUDED` below).
- **`period_s`** is the breath's own length (from the start of its inspiration to the start of the next). **`vte_ml`** is its expired volume, which should match `vt_ml` (the inspired volume); a difference means a leak or a drifted pneumotach zero.
- **Two minute ventilations.** `mv_lpm` is the 60 s average the monitor shows. The breath's own minute ventilation is `vt_ml / 1000 × 60 / period_s`; the response slope is fitted to that, because an average over the last minute lags the EtCO2 it is paired with. The loaders and the breath export give it as `mv_breath_lpm`.
- **`# test`** holds how the response is analysed (`responseSkipSec`: the seconds of rebreathing left out of the fit) and the subject's height, weight and age if they were entered (never a name, MRN or date of birth).
- **`event`** holds alarms (`ALARM_START`, `ALARM_ACKNOWLEDGED`, `ALARM_SILENCED`, `ALARM_RESOLVED`, `ALARM_CLEARED`, `ALARM_REARMED`), markers (`MARK <label>`, and `MARK_RENAMED <id> <label>` when a marker placed with the M key is labelled), breaths found to be isolated jumps one breath later (`BREATH_REJECTED <n> outlier: <reason>`, which overrides that breath's `breath_quality`), breaths taken out of the response fit by hand or put back (`BREATH_EXCLUDED <n> manual`, `BREATH_INCLUDED <n>`), a stale calibration at the start (`CALIBRATION_STALE …`), connections, device errors and calibration changes and simulation speed changes (`SIM_SPEED 5`) on the row where they happened. Several on one row are joined with ` | `. An event while no data was flowing gets a row with only `time_s`, `wall_time_iso` and `event`.
- **`time_s`** is seconds since the recording started, from the Arduino's clock, so samples are exactly 1/rate apart. `wall_time_iso` is the same instant on the computer's clock. `t_ms` and `seq` are the raw device clock and 16-bit sequence number (gaps in `seq` are dropped samples). A simulator recording made at a faster simulation speed (2×–10×) says so on its `source` line, e.g. `Simulator (5× speed)`; its `time_s` and `wall_time_iso` follow the simulated clock, so they run that much faster than the real time of day.
- `v0`–`v2` are the raw ADC volts (CO2, flow, pneumotach), untouched by any noise removal, so the session can be re-processed later with a different calibration or different cleaning.
- The calibrated columns (`co2_mmHg` … `pneumo_lpm`) are the cleaned signal: spikes removed and low-pass filtered, as shown on the monitor. Because the spike filter looks 3 samples ahead, they lag the raw volts by 30 ms at 100 Hz; `time_s` is the time of the raw sample.

| Column | Unit |
| --- | --- |
| `v0`, `v1`, `v2` | V |
| `co2_mmHg`, `etco2_mmHg`, `fico2_mmHg` | mmHg |
| `co2_pct` | % |
| `flow_lpm`, `pneumo_lpm`, `peak_insp_lpm`, `mv_lpm` | L/min |
| `pneumo_pa` | Pa |
| `vt_ml`, `vte_ml` | mL |
| `rr_bpm` | breaths/min |
| `period_s` | s |

`pneumo_lpm`, `vt_ml`, `vte_ml` and the minute ventilations include the gas-condition settings that were on (`volumeBtps` and `rebreathingGasFactor` in `# processing`); the raw volts never do. With a CO2 delay set (`co2DelayMs`), the flow columns on a row are the flow from that much earlier, lined up with the row's CO2.

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
| `artifact` | A partial breath with no alveolar plateau (less than 10 % of expiration within 90 % of EtCO2: a shallow breath, sample-line kink or cough), an impossible EtCO2 (outside −2 to 120 mmHg), a tidal volume under 1 mL, or inspiratory flow beyond the sensor's range for more than 20 ms (the volume under-reads) |
| `outlier` | An isolated EtCO2 jump: more than 5 mmHg and 3× the usual breath-to-breath change away from both the breath before and the breath after. A real step (the start of rebreathing) is kept, because the next breath stays at the new level. Known one breath later, so it is written as a `BREATH_REJECTED` event |

Breaths that are not `ok` are left out of the EtCO2/RR/VT/MV numbers, the EtCO2 trend (both the breath points and the continuous CO2 line behind them, which is broken over a rejected breath), the MV-versus-EtCO2 response plot, the review summary and charts, the EtCO2 alarms and `s.breaths` in `analysis/load_session.py`. The loader also blanks (NaN) the calibrated values of every sample inside a rejected breath in `s.samples`, so averages, plots and fits of the samples skip them too; the raw volts and the rows stay. They stay in the recording, marked. If 3 of the last 5 breaths are rejected as noisy or partial, the **SIGNAL TOO NOISY** technical alarm (medium priority) sounds instead of false CO2 alarms; it clears when no more than 1 of the last 5 is rejected. A flat CO2 trace (sample line off) is not called noisy, so the low-CO2 and apnea alarms still see it.

Turn both switches off to see the signal exactly as it arrives, for example while calibrating.

## Analysis examples

### Python (pandas)

`analysis/load_session.py` splits a file into samples, breaths and events:

```python
import sys; sys.path.append("analysis")
from load_session import load_session

s = load_session("co2r_STUDY-01_20261105-010203.csv")
s.breaths.plot(x="time_s", y=["etco2_mmHg", "mv_breath_lpm"], secondary_y="mv_breath_lpm")   # clean breaths only
alarms = s.events[s.events.type == "ALARM_START"]
s.breaths_all.breath_quality.value_counts()                                     # every breath, with its quality
```

Each row of `s.samples` has a `breath_quality` column: the quality of the breath it belongs to (empty before the first breath and after the last). `load_session(path, clean=False)` keeps every breath in `s.breaths` and every calibrated value in `s.samples`.

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

`co2_response(s)` reports a rebreathing test the way CHOP does (Read's method), with the same rules as the live monitor:

- **Baseline:** clean breaths from the marker labelled "Baseline" (or from the start, if there is none) to the start of rebreathing. They are averaged, not fitted.
- **Rebreathing starts** at the marker whose label contains "rebreath" (for example `MARK Rebreathing`, or `MARK Event 2` then `MARK_RENAMED 2 Rebreathing`). With several tests in one file, the last one is used.
- **Fitted:** each later breath's own minute ventilation (`mv_breath_lpm`) against its EtCO2, leaving out the first `responseSkipSec` seconds of rebreathing (from the file's header; the coached deep breaths and the time the bag, lungs and blood take to come to equilibrium), everything from the end marker on ("End of test", "Test ended", "Rebreathing stopped" …), breaths the noise screen rejected, and breaths excluded by hand.

```python
from co2r.session import co2_response
r = co2_response(s)
r["baseline_etco2_mmHg"], r["baseline_mv_lpm"], r["baseline_n"]
r["slope"], r["r"], r["x_intercept"], r["n"]   # L/min per mmHg; x-intercept in mmHg (MV = 0)
r["skipped_n"], r["skip_s"], r["end_s"]        # what was left out of the fit, and why
r["slope_per_kg"], r["slope_per_m2"]           # mL/min/mmHg/kg and L/min/mmHg/m2, if height and weight are in the header
co2_response(s, start=t, end=t2)                # choose the start and end of rebreathing yourself
co2_response(s, skip_s=20, exclude=[41, 42])    # another skip; leave breaths 41 and 42 out
```

From the command line: `python analysis/load_session.py file.csv --skip 20 --exclude 41,42`.

Or for any stretch of breaths:

```python
import numpy as np
step = s.breaths[(s.breaths.time_s > t_start) & (s.breaths.time_s < t_end)]   # clean breaths only
slope, intercept = np.polyfit(step.etco2_mmHg, step.mv_breath_lpm, 1)
```

## The test report

**CO2 response report (.pdf)** (in the review screen, and in the Export menu of the sessions list) makes a PDF from a recording. The desktop app, the browser version and the command line all make the same report:

```bash
python -m co2response --report co2r_STUDY-01_20261105-010203.csv
```

That writes `co2r_STUDY-01_20261105-010203.pdf` next to the file and prints the numbers. `--out report.pdf` chooses the name; `--skip 20` changes the seconds of rebreathing left out.

- **Page 1:** the study code, start time, length and subject size; the results (test phases, baseline means, slope, r, x-intercept, the EtCO2 range fitted, the slope per kg and per m² when height and weight were entered, how many breaths were rejected, not fitted or excluded by hand, alarms); the response plot (fitted breaths filled, baseline as rings, breaths outside the fit faint, excluded as crosses); EtCO2 and each breath's MV against time with the markers; and how it was measured (source, firmware, barometric pressure, CO2 delay, gas-condition settings, alarm limits, when each channel was calibrated).
- **Following pages:** every breath with its time, EtCO2, VT, RR and MV, and how it was used: `baseline`, `fitted`, `not fitted`, `excluded`, `rejected (noisy / artifact / outlier)` or `-` (before the baseline).

The report is made by a small PDF writer inside the app (`co2response/storage/pdf.py`), so it needs nothing installed and works offline.
