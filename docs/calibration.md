# Calibration

Every sensor conversion lives in the monitor app, so recalibrating never means reflashing. Open **Settings > Calibration**. Each channel has a card with:

- **Live readout:** raw volts, the calibrated value using the settings you are editing (marked "Preview (unsaved)"), and a third value (CO2 in mmHg, pneumotach flow, or the flow sensor's saved value).
- **Methods:** Enter values, Zero & span / Two-point, Multi-point fit, and (pneumotach) 3 L syringe.
- **Save calibration / Discard changes / Load defaults.** Nothing on the monitor changes until you save. Saved calibrations get a timestamp, stay on this computer, and are written into the header of every recording.

**Calibration age.** While a real device is connected, a blue notice says which channels were calibrated more than 24 hours ago, or never (still on the default values). The limit is **Calibration reminder after** in **Settings > Alarms**; 0 turns it off. A stale calibration at the start of a recording is also written into the file (`CALIBRATION_STALE`), and the test report lists when each channel was calibrated. If a calibration cannot be saved to disk, a **SETTINGS NOT SAVED** notice says so: the calibration is in use, but only until the app closes.

Use **Export calibration (JSON)** to keep a copy with your lab notes, and **Import calibration…** to load it on another computer. **Reset all to defaults** returns to `co2response/config/channels.json`.

## Calibration models

| Type | Formula | When to use |
| --- | --- | --- |
| Linear | value = gain × V + offset | Most sensors with a linear analog output |
| Polynomial | value = c0 + c1·V + c2·V² + … | Mildly non-linear sensors (fit from several points) |
| Lookup table | straight lines between (V, value) points | Datasheet tables, or strongly non-linear sensors |

A **zero offset** is subtracted after the calibration. The pneumotach adds a **flow model**: flow (L/min) = k1·ΔP + k2·ΔP·|ΔP|, with ΔP in Pa.

## Entering values by hand

Pick **Enter values**, choose the type, and type the numbers (for example gain and offset from the sensor datasheet). Values commit when you press Enter or leave the field. For a polynomial, type the coefficients lowest order first, separated by commas. For a table, edit the rows, use **Capture V** to fill a row's voltage from the live signal, and **Add point** for more rows.

## CO2: zero and span

1. **Zero & span** tab. Point A defaults to room air (0.04 %), point B to a 5 % calibration gas. Change B to your gas's certified value.
2. Flow room air through the sample line, wait for the reading to settle, press **Capture** on A (it averages 2 s).
3. Flow the calibration gas, let it settle, press **Capture** on B.
4. Press **Compute line**, check the preview, then **Save calibration**.

You can type the volts by hand instead of capturing. To re-zero quickly between spans, use **Enter values > Zero now on room air**.

The monitor shows CO2 in mmHg using P_CO2 = F_CO2/100 × (P_B − P_H2O). Set the lab's barometric pressure in **Display & units** (Pittsburgh is usually near 735 mmHg, not 760); turn on water-vapor correction for BTPS.

## Flow sensor: two-point or multi-point

- **Zero:** with no flow, **Enter values > Zero now (no flow)**.
- **Two-point:** capture no flow and one known flow from a reference flowmeter.
- **Multi-point fit:** capture three or more flows across the range, then **Fit line**, **Fit quadratic** or **Use as lookup table**. The result line reports the largest error at your reference points.

## Pneumotach

1. **Transducer (volts → Pa).** Use the transducer datasheet (Enter values) or a manometer with the two-point or multi-point methods.
2. **Zero:** with no flow, **Enter values > Zero now (no flow)**, then save.
3. **Flow constant with the 3 L syringe** (**3 L syringe** tab):
   1. Connect the syringe with the plunger fully out.
   2. Press **Start stroke**, push the plunger fully in with one smooth stroke (1–2 s), press **Stop**.
   3. The card shows ∫ΔP dt, the volume measured with the current k1 and its error, and the new k1 that makes the stroke read the syringe volume (k2 is held).
   4. Press **Apply**, **Save calibration**, and push again to confirm the volume reads correctly.
4. Check VT on the monitor: a 3 L stroke should read about 3000 mL.

## Gas conditions and timing (Settings > Display & units)

These change how the pneumotach's flow becomes the reported volumes. All three are off (or neutral) by default, are written into every recording's header, and never touch the raw volts.

| Setting | What it does | When to use it |
| --- | --- | --- |
| **Report volumes at body conditions (BTPS)** and **Room temperature** | Multiplies pneumotach flow, VT and MV by (310.15 / (273.15 + T)) × (P_B − P_H2O(T)) / (P_B − 47): the factor from room temperature, saturated, to 37 °C, saturated. About ×1.09 at 22 °C. | When the clinicians want ventilation at BTPS, the usual convention in lung-function testing. During rebreathing the inspired gas warms and humidifies, so the true factor falls below this; agree the convention before comparing results. |
| **Bag gas correction** | Multiplies pneumotach flow by this factor from the Rebreathing marker to the End-of-test marker only. | A linear (Fleisch or screen) pneumotach's pressure drop follows gas viscosity, so one calibrated in air over-reads in an oxygen-rich bag: true flow = reading × μ_air / μ_mix. About 0.90 for pure O2 and 1 for air; `co2response.processing.co2.gas_factor(o2_pct, co2_pct)` gives a first-order estimate. Leave it at 1 if you did the syringe calibration with the bag gas (then the baseline, in room air, under-reads instead). |
| **CO2 signal delay** | Holds the flow signals back by this many ms so each breath's CO2 lines up with its flow. | A sidestream analyser's sample line delays CO2 by a second or more. Without the correction the end of the plateau falls in the next inspiration, so inspired CO2 reads high and partial breaths are mis-flagged. A mainstream sensor needs 0. |

**Measuring the CO2 delay.** The field's hint shows what the monitor measures now: how long after expiration begins the CO2 rises (median of the last 10 breaths). Dead-space washout alone takes 0.1 to 0.3 s. If the hint says more, enter the excess as the delay; the hint then falls back to the washout time. For a direct bench measurement, switch the gas at the sample port (room air to calibration gas) and time the step against the flow signal.

**Checking the pneumotach zero while it runs.** The VT tile shows the expired volume beside the inspired one ("exp 497 mL"). They should match. If they differ by more than 20 % over 5 breaths (**Volume mismatch notice** in Settings > Alarms), a blue notice says so: look for a leak at the mouthpiece or mask, or re-zero the pneumotach.

## SpO2 (optional)

The oximeter channel has no calibration card: it is a safety signal, not a measurement the test reports. **Settings > Display & units > SpO2** takes the analog output's scaling (SpO2 % = scaling × volts + offset, from the oximeter's manual; often 1 V = 100 %). Check it against the oximeter's own display before each session.

## Where calibrations are stored

| Place | What |
| --- | --- |
| `co2response/config/channels.json` | Defaults for a fresh install. Edit it with the sensors' real scaling; no rebuild needed. |
| `channels.json` in the app's settings folder | The calibration you saved on this computer (Windows `%APPDATA%\CO2-Response`, macOS `~/Library/Application Support/CO2-Response`, Linux `~/.local/share/co2response`). |
| Exported JSON | Your backup. |
| Every session CSV header | The calibration in use when recording started, so replays reproduce the numbers. |
