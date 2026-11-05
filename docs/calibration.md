# Calibration

Every sensor conversion lives in the monitor app, so recalibrating never means reflashing. Open **Settings > Calibration**. Each channel has a card with:

- **Live readout:** raw volts, the calibrated value using the settings you are editing (marked "Preview (unsaved)"), and a third value (CO2 in mmHg, pneumotach flow, or the flow sensor's saved value).
- **Methods:** Enter values, Zero & span / Two-point, Multi-point fit, and (pneumotach) 3 L syringe.
- **Save calibration / Discard changes / Load defaults.** Nothing on the monitor changes until you save. Saved calibrations get a timestamp, stay on this computer, and are written into the header of every recording.

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

## Where calibrations are stored

| Place | What |
| --- | --- |
| `co2response/config/channels.json` | Defaults for a fresh install. Edit it with the sensors' real scaling; no rebuild needed. |
| `channels.json` in the app's settings folder | The calibration you saved on this computer (Windows `%APPDATA%\CO2-Response`, macOS `~/Library/Application Support/CO2-Response`, Linux `~/.local/share/co2response`). |
| Exported JSON | Your backup. |
| Every session CSV header | The calibration in use when recording started, so replays reproduce the numbers. |
