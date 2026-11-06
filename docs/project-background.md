# Project background: Ventilatory Response to CO2

What the clinical test is, what the mentors and the capstone team have asked for, and how this repository measures up. It summarizes the team's working documents from September to November 2026 (listed at the end). Where a document and the code disagree, the gap is listed in [§6](#6-gaps-what-the-documents-ask-for-that-the-repo-does-not-do-yet), and decisions still open are in [open-questions.md](open-questions.md).

CMU 42-401 BME Capstone, Group 6: Alexis, Kavan, Sofya, Spencer, Kammiee and Kaitlyn. Mentors: Dr. Daniel Weiner and Dr. G. Kurland (pediatric pulmonology). Course: Dr. Katie Bieryla.

## 1. The problem

**Congenital central hypoventilation syndrome (CCHS)** is a rare PHOX2B mutation (about 1 in 200,000 births). The brain does not respond normally to rising CO2, especially during sleep, so breathing stays slow or shallow while CO2 builds up. It is lifelong; many patients need a tracheostomy and a ventilator.

Clinicians measure how strongly a patient's breathing responds to CO2: expose them to rising CO2 (with supplemental O2) and record **minute ventilation** (MV = respiratory rate × tidal volume) against **end-tidal CO2** (EtCO2). The slope of MV against EtCO2, in L/min per mmHg, is the readout. In CCHS the slope is flat or near flat.

There is no established way to run this test at UPMC. The closest working setup is at the CHOP Sleep Lab.

## 2. How CHOP runs the test (Read's rebreathing method)

From the CHOP contact's description, forwarded by Dr. Weiner on Sep 3, 2026:

- **Subjects:** awake and seated, in a sleep research room.
- **Circuit (Read, 1967):** mouthpiece → in-line pneumotach → in-line EtCO2 sensor → manual two-way valve → rebreathing bag. No bag-in-box. Not a circular circuit.
- **Baseline:** the subject breathes room air back and forth with the valve open to the room until breathing is stable. Baseline MV and EtCO2 are collected.
- **Rebreathing:** the valve is turned to the bag (pre-filled with an O2-rich mix; the mix and starting volume are still to be confirmed with CHOP). Three deep breaths are coached. CO2 then climbs breath by breath.
- **Stop:** the test ends when EtCO2 reaches **60–65 mmHg**.
- **CO2 sensor:** an old Novametrix capnograph with analog output, set to **no averaging** so it gives the instantaneous CO2 concentration.
- **Acquisition:** PowerLab (LabChart) records calibrated flow (mL/s) and CO2 (mmHg). Their recording also has SpO2 (SAT) and transcutaneous CO2 channels.
- **Analysis takes three programs:**
  1. PowerLab records the signals.
  2. A BASIC program calculates MV and EtCO2 breath by breath. **The user discards erratic breaths by hand.**
  3. SigmaPlot plots MV against EtCO2 and fits a line.

  The recordings are irregular, so choosing well-formed breaths is necessary.

**The CHOP report.** Their example ("Patient X") shows:

- baseline breaths as hollow points, reported as an average: EtCO2 43.64 mmHg, MV 6.63 L/min;
- rebreathing breaths (about 47 to 65 mmHg) as filled points, with the fitted line: slope 1.244 L/min/mmHg, r = .848.

The baseline is not part of the fit.

From the first mentor meeting (Sep 2) and its summary (Sep 11):

- The main technical job is **measuring flow**, then volume. CO2 comes second (the lab may lend a CO2 sensor; specs to follow).
- The goal is software that samples flow, volume and CO2 together and plots MV against CO2.
- **Patients:** start with a proof of concept on cooperative adult volunteers, then adapt it for infants and children.
- **Interface:** use a mouthpiece with nose clips at first, as in lung-function testing. Ventilator or trach patients would use the ventilator's own measurements.
- **Physiology:**
  - no positive pressure and no PEEP; the lungs see only atmospheric pressure;
  - CO2 rises and O2 falls in the circuit, with pre-oxygenated gas in the reservoir bag;
  - mask leaks are a concern.
- **Normalization:** normal blood CO2 does not depend on age, height or weight, but the slope may need normalizing for body size (open).
- **Equipment:** a pneumotach, a 3-way valve and an ADC board may need to be bought.
- **Overlap:** another capstone project overlaps with similar hardware.
- **Plan:** build a digital prototype first, physical later in the semester.

Mentor reading list: Read (1967); Yamada et al. (2021, Read's method in normal infants, *Pediatric Pulmonology*, called the most relevant for future needs); Berkenbosch et al. (1988); Mannée et al. (2017).

## 3. User needs and design inputs

From the brainstorming summary (Oct 8) and the mid-semester presentation:

| User need | Design input (mid-semester deck) | In this repo |
| --- | --- | --- |
| Patient safety | Automatic CO2 stop within ≤ 1 s; emergency stop | Alarms only, no actuator: the operator turns the valve. See §6 |
| Patient safety | Warn near 60 mmHg, stop alert at 65 mmHg | One high-EtCO2 alarm, default 50 mmHg (open question 9) |
| Reliable measurements | Minute-ventilation accuracy ±10 % | 3 L syringe calibration exists; no validation procedure written yet (§7) |
| Reliable measurements | Continuous EtCO2 recording | Yes: every sample and every breath, saved each second |
| Clinically useful results | Display ventilation against EtCO2 | Yes: the Ventilatory response tab |
| Clinically useful results | Calculate the CO2 response slope | Yes: slope, r, x-intercept, with the baseline set apart |

The brainstorm also asks for:

- **Displays:** EtCO2, MV and RR against time, plus MV against EtCO2.
- **SpO2** as a safety variable.
- **One system** instead of moving data through several programs.
- **Abnormal conditions** detected, with the operator warned, the test paused and gas delivery stopped.
- **Hardware:** a mask that fits and seals across pediatric face sizes, and a compact setup without a lot of tubing.

## 4. The team's software designs and where the repo stands

The mid-semester deck proposes two software solutions. **This repo already implements Solution B** and covers most of Solution A's analysis steps for its own recordings.

**Solution A: Python program run after the test (PowerLab kept).** It replaces the BASIC program and SigmaPlot.

| Step | Repo |
| --- | --- |
| 1. Load the PowerLab export (flow, CO2, markers) | **Not yet.** It reads its own session CSVs, not LabChart files (§6) |
| 2. Calibrate volts to L/s and mmHg; shift CO2 to cancel the sensor delay | Calibration: yes. CO2 delay shift: **not yet** (§6) |
| 3. Detect breaths | Yes (flow zero-crossings, or the CO2 edges if there is no flow) |
| 4. VT, RR, MV, EtCO2 per breath | Yes |
| 5. Flag coughs, sighs and leaks automatically | Partly: the noise screen rejects noisy, partial, impossible and isolated-jump breaths. Not specifically coughs, sighs or leaks |
| 6. Label baseline vs rebreathing breaths | **Yes (new):** a marker labelled "Rebreathing" splits them |
| 7. Regression of MV against PETCO2: slope, r, x-intercept | **Yes (new):** on the rebreathing breaths only |
| 8. Report: plot, per-breath accepted/rejected table, PDF | Plot and breath CSV (with `breath_quality`): yes. PDF: **not yet** |

**Solution B: runs during the test, with a new DAQ (NI USB or Arduino + ADC).** This is what the repo is.

| Step | Repo |
| --- | --- |
| 1. Stream and save in the background | Yes: crash-safe CSV written every second |
| 2. Calibrate; shift CO2 for the sensor delay | Calibration: yes. Delay shift: not yet |
| 3. Detect each breath as it ends | Yes |
| 4. Metrics and QC on every breath | Yes |
| 5. Safety: check every breath; warn near 60, stop alert at 65 | Per-breath EtCO2 alarm with delay and hysteresis, one limit (default 50). Two tiers: not yet |
| 6. Live dashboard: flow and CO2 waves, live MV-vs-EtCO2 plot, start/end buttons | Waves, numbers, live response plot: yes. Start/end: Record/Stop plus the Rebreathing marker |
| 7. Final analysis when the operator ends the test: slope, r, plot, PDF | Slope, r, x-intercept on screen and in `co2r.session.co2_response`. PDF: not yet |
| Hardware | Arduino + ADS1115 firmware; NI-DAQmx and LabJack drivers (not yet tried on hardware) |

## 5. How to run a test with the software (Read's method)

1. Calibrate. CO2: zero, then span gas. Pneumotach: 3 L syringe.
2. Enter a study code (no identifiers) and press **Record**.
3. **Baseline:** the subject breathes room air through the mouthpiece (nose clip on) until breathing is stable.
4. Turn the valve to the bag. Press **M** and label the marker **Rebreathing**. Coach three deep breaths.
5. Watch EtCO2. End the test at the stop limit (CHOP: 60–65 mmHg) by turning the valve back to room air. Mark it if you like.
6. Read the **Ventilatory response** tab:
   - baseline mean EtCO2 and MV (hollow rings);
   - slope, r, x-intercept and n of the rebreathing breaths.

   The same numbers come from `python analysis/load_session.py file.csv`, or from `co2_response(load_session(...))` in Python.

The labelled marker is what splits baseline from rebreathing. The match is case-insensitive and any label containing "rebreath" works, including one renamed after it was placed. It counts from when it was placed.

## 6. Gaps: what the documents ask for that the repo does not do yet

| Gap | Asked for in | Notes |
| --- | --- | --- |
| Two-tier EtCO2 limit: warning near 60, stop alert at 65 | Mid-semester deck; CHOP stop at 60–65 | Today there is one high limit (default 50, which every rebreathing test crosses). Needs a decision (open question 9) |
| Automatic CO2 stop (≤ 1 s) and emergency stop | Deck design inputs; brainstorm | Needs a controllable valve, plus firmware and app support. With Read's manual valve, the alarm tells the operator to turn the valve |
| Manual breath exclusion by the clinician | CHOP practice; Potential Solutions doc | Only the automatic noise screen exists |
| CO2 signal delay alignment | Deck step 2 | Matters most for a sidestream analyzer (e.g. Masimo NomoLine ISA, 50 mL/min). Less for a mainstream analyzer like the Novametrix |
| PowerLab / LabChart file import | Deck Solution A | Needs a sample export from CHOP (text export with flow in mL/s and EtCO2 in mmHg) |
| PDF summary report | Deck step 8 | Plot, per-breath table with accepted/rejected, baseline and response numbers |
| MV and RR against time | Brainstorm displays | Only EtCO2 against time is trended now |
| SpO2 channel | Brainstorm safety; CHOP records SAT | CH3 is spare on the ADS1115 |
| Slope normalization for body size | First meeting | Open clinically; not needed for adults |
| Gas delivery / blending | Brainstorm | Read's method needs only the bag and valve. A blender is out of scope unless the design changes |

## 7. Validation (asked at the mid-semester review: "how do we assess accuracy?")

Proposed, to agree with the mentors:

- **Volume / MV (±10 %):** a 3 L calibration syringe at several stroke speeds and rates. Compare integrated VT and MV with the true value. Add a known-rate ventilator or breathing simulator for MV.
- **CO2:** certified gases (e.g. 0 % and 5 %, plus one near 8 % ≈ 60 mmHg). Report the error at the 60–65 mmHg stop limit.
- **Timing:** step the CO2 at the sample port (switch between gases) and measure the delay against the flow signal. This sets the delay shift above.
- **End to end:** replay recordings with known answers (the simulator and `sample-data/`), and compare with the CHOP analysis on the same data once a PowerLab export is available.
- **Alarm latency:** time from a breath above the limit to the alarm (target ≤ 1 s once a stop actuator exists).

## Sources

Team documents (not in the repo):

- Prep for First Meeting (Sep 2, 2026)
- First Mentors Meeting Notes (Sep 2)
- Initial information provided by mentors (Sep 3; the CHOP description, report plot and PowerLab screenshot)
- Zoom meeting summary (Sep 11)
- Brainstorming Summary (Oct 8)
- Current Solutions and its draft: RespirAct, Capnostream 35, Vyntus CPX, Radiometer TCM5 FLEX, Masimo NomoLine ISA
- Potential Solutions (mouthpiece vs pediatric face mask; post-test vs live software)
- Mid-Semester presentation (Group 6)

The CHOP images are not copied here because the PowerLab screenshot shows a name.
