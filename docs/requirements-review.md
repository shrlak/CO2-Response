# Requirements review and plan to December 11

CO2-Response · Group 6 (CO2) · reviewed Nov 6, 2026

This compares the design and development file (the team's Google Drive folder `06_CO2`) with what the system actually does, lists the requirements that were missed, and turns them into a plan for the five weeks left. It is written so someone outside the team can follow it, as the TA asked.

**Read:** DTM rev02 (`42401_DTM_CO2_rev02`) and Krystal's rev01 feedback; the mid-semester feedback (all peer and TA comments); TA notes from 9/29, 10/6, 10/20, 10/27 and Krystal's 11/3 email; the mentors' first-meeting notes, the CHOP description of their test (9/3), the budget feedback (10/6); the safety and risk notes (10/6) and design-consideration notes (10/7); problem statement, needs statements, current and potential solutions, brainstorming summary, mid-semester slides, proposed budget and the 11/4 purchase list; stakeholder documents; the mentors' reference papers (Read 1967, Yamada 2021, Rebuck 1976, Mannée 2017).

**Status words used below:** *Met* (the system does it and a test shows it), *Met in software* (done, not yet checked with real hardware), *Partial*, *Not met*, *Revise* (the requirement itself needs changing).

---

## 1. The findings that matter most

1. **The CO2 limits contradicted the test.** CHOP stops the Read test at an EtCO2 of 60–65 mmHg (mentor notes, 9/3); Mannée 2017 stopped adults at an end-tidal PCO2 above 8.5 kPa (about 64 mmHg). DTM input 3.1 (alarm when inspired CO2 > 7 % for 10 s, about 50 mmHg) and the app's default high limit (50 mmHg) would both alarm in *every* normal test: the Read bag *starts* at 7 % CO2 (Read 1967, Rebuck 1976), and inspired CO2 rises with the subject's own. **Software fixed** (end-test limit while rebreathing, warning before it). **DTM 3.1 needs rewriting** and the numbers need Dr. Weiner's sign-off. Infants are different: Yamada 2021 stopped once EtCO2 had risen 2 % (about 15 mmHg) from the start, at a median maximum of 51 mmHg, so the end-test limit must depend on the population.
2. **"Automatically stop CO2 within 1 s" (DTM 3.2) cannot be met by the current design.** The two-way valve is turned by hand and nothing in the system can move it. The TA asked exactly this on 10/27 ("automatically terminate or manual intervention?"). The team needs to decide (§4.4): reword 3.2 for prototype 1.0 (software alarm, operator turns the valve), or add a normally-open solenoid valve with a hardware watchdog.
3. **Nothing measures oxygen**, yet five DTM inputs depend on it (3.4, 3.5, 6.3, 6.4, 7.8). Simplest answer for December: a certified pulse oximeter on the subject covers 3.4/3.5 (and is required for safety anyway); FiO2 is fixed by the bag gas, so decide whether 6.4/7.8 stay (the ADC's fourth input, A3, is free for an O2 sensor).
4. **The "2-way valve" in the budget is an IV stopcock.** Luer stopcocks have a bore of a few millimetres; breathing through one is like breathing through a coffee stirrer. The rebreathing valve needs 22 mm breathing-circuit ports. The same goes for the aquarium tube connectors, and Gorilla Glue and duct tape must not be in the gas path (leaks, and fumes the subject would breathe; see ISO 18562 in §4.2). **Do not order these for the gas path.**
5. **Two budget lines need checking before money is spent.** The "EtCO2 sensor" listing is titled "…straight sample line w/ humidifier, box of 10": that reads like consumable sampling lines for a Respironics capnograph, not an analyser. And there is no differential pressure transducer for the pneumotach, which is what actually produces the flow signal. The mentors said the lab has CO2 sensors; ask before buying.
6. **The response slope was not computed the way CHOP computes it.** The app paired each breath's EtCO2 with a 60-second average MV and fitted every breath in a 30-minute window: baseline, rebreathing and recovery mixed. CHOP computes MV and EtCO2 breath by breath over the rebreathing period, and the three deep breaths the subject is coached to take at the switch (Rebuck: "to facilitate mixing") are voluntary, so they should not count toward the slope. **Software fixed**, and it now reports r (DTM 9.5 asks for R), the x-intercept and the slope's 95 % confidence interval.
7. **A sidestream capnograph's delay would have put each breath's EtCO2 into the next breath.** The slides promised to "shift CO2 to cancel sensor delay"; the code did not. **Software fixed**, with a button that measures the delay from normal breathing.
8. **At the end of a test the flow can exceed the pneumotach channel's range.** In the simulator, an adult's expiratory flow passes the default ±100 L/min near 59 mmHg. The app treated the clipped samples as a lost signal: a **false apnea alarm**, and the EtCO2 alarm went blind exactly when CO2 was highest. **Software fixed**; the hardware requirement is in §4.3.
9. **Pediatric sizing is missing from the DTM.** Dead space, flow range, breath-detection settings and bag volume all scale with the child. The mentors' plan is adults first, then children; the DTM should say which sizes prototype 1.0 serves.
10. **Testing on people needs approval.** Breathing on the device, even by teammates, is human-subjects research. For December 11, verify on the bench (§6); talk to Dr. Bieryla about the IRB route before any volunteer test.

---

## 2. What changed in the software (this update)

| Change | Requirement it serves | Checked by |
| --- | --- | --- |
| **Start/End rebreathing** soft key; `TEST_REBREATHING_START` / `_END` events in the recording with baseline EtCO2/MV, duration and result | DTM 6.2, 7.9; risk notes "clear termination procedure" | `tests/test_rebreathing.py`, `tests/test_ui.py` |
| Baseline-stable cue on that key (last 6 clean breaths within 3 mmHg) | DTM 6.2 | same |
| End-test EtCO2 limit (65 mmHg) replaces the high limit while rebreathing; message "END TEST … turn the valve to room air"; stays on in recovery until EtCO2 is back under the normal limit | DTM 3.1 (revised), mentors (60–65 mmHg) | same |
| Warning at 60 mmHg (medium) | potential-solutions doc ("warn near 60, stop at 65") | same |
| Rebreathing timer and 6-minute limit alarm | DTM 8.1 | same |
| Response fit: breath-by-breath MV, rebreathing period only, first 3 clean breaths left out; reports slope ± 95 % CI, r, x-intercept, n | DTM 9.1–9.5; CHOP method | same |
| CO2 delay alignment (`co2_delay_ms`) and **Measure from 20 s of breathing** | slides 11–12 ("shift CO2 to cancel delay"); feedback "what delay can you deal with?" | same |
| Pneumotach clipping: breaths still found, EtCO2 still alarmed, VT marked unknown, **PNEUMOTACH OUT OF RANGE** alarm instead of a false apnea | risk notes (flow sensor failure, incorrect MV) | same |

All 139 automated tests pass (2 browser tests skip without Playwright). The defaults are placeholders until the mentors answer the questions in §8.

---

## 3. DTM rev02, input by input

| # | Input (short) | Status | Note |
| --- | --- | --- | --- |
| 1.1 | MV accuracy ±10 % | Met in software | Needs the bench volume test (V1). See BTPS and gas-composition effects in §4.3: each can be ~10 % on its own |
| 1.2 | RR "+1" breaths/min | Met in software | Write it as ±1. Synthetic data gives ±0.5 |
| 1.3 | EtCO2 ±5 % or 2 mmHg | Met in software | Bench test with calibration gas (V3); depends on the delay alignment |
| 1.4 | Mask leak ≤ 5 L/min | Not met | No method yet. Add the pressure at which leak is measured, and a nose clip for mouthpiece tests (§4.2) |
| 2.1 | ISO 10993-1 | Revise | 10993-1 covers skin/mucosa contact (mouthpiece, mask cushion). The *gas path* falls under ISO 18562 |
| 2.2 | Mask ≤ 50 g | Revise | The mentors said not to design a mask ("the PFT lab would have masks… do not need to work on that"). Make the input "use commercial, sized, single-use interfaces" |
| 2.3 | Sizes newborn to adult | Revise | Say what prototype 1.0 covers (adults first per the mentors) |
| 3.1 | Alarm at inspired CO2 > 7 % for 10 s | **Revise** | Contradicts the protocol (§1.1). Proposed: warn at EtCO2 60, end test at 65 mmHg, confirmed by Dr. Weiner |
| 3.2 | Automatic stop within 1 s | **Not met** | Manual valve. Decision in §4.4 |
| 3.3 | Emergency stop | Partial | With a manual valve, the valve is the stop. Specify it: one hand, one movement, back to room air in ≤ 1 s, reachable from the operator's position |
| 3.4 | Monitor patient O2 | Not met | Certified pulse oximeter alongside |
| 3.5 | Alarm if O2 < 90 % for 15 s | Not met | The oximeter's own alarm; write it into the test procedure |
| 4.1 | Cost ≤ $5,000 | Met | Budget $570 |
| 5.1 | Apply interface ≤ 60 s | Not checked | Time it during the dry runs |
| 5.2 | ≤ 2 controls for the stimulus | Met by design | One valve; nothing to adjust in a Read test |
| 5.3 | Display CO2 and ventilation | Met | |
| 6.1 | Bag 5 % CO2, ≥ 90 % O2 | Revise | 5 % CO2 / 95 % O2 is what Yamada used in infants; Read and Rebuck used 7 % CO2 / 93 % O2 in adults. State the mix per population and add the bag volume (§4.1). Michelle (CHOP) knows their mix |
| 6.2 | Switch once baseline is stable | Met in software | The app's cue: 6 clean breaths within 3 mmHg. Rebuck's rule is stricter: end-tidal PCO2 steady within ±1 mmHg over at least 5 min of room air. Pick one with the mentors and put it in the DTM |
| 6.3 | FiO2 ≥ 60 % | Met by design | Follows from the bag gas (Mannée kept FiO2 > 60 % by adding O2); not measured |
| 6.4 | Measure FiO2 ±3 % | Not met | Keep only if an O2 sensor is added (question 33) |
| 7.1–7.3 | Record EtCO2, MV, RR | Met | Per breath, in the session CSV |
| 7.4 | Record CO2 delivered | Met | FiCO2 per breath is the inspired CO2 |
| 7.5–7.6 | Store, timestamp | Met | |
| 7.7 | Share via EHR / hospital network | Not met | Out of scope for a research prototype; replace with the data-handling input in §4.6 |
| 7.8 | Record inspired O2 | Not met | As 6.4 |
| 7.9 | Record start/end of CO2 exposure | Met in software | `TEST_REBREATHING_START/END` |
| 8.1 | Done in ≤ 6 min | Met in software | Timer and alarm. Read and Rebuck rebreathed for 4 min; Yamada's infant tests took 150 ± 38 s |
| 9.1–9.5 | MV vs EtCO2, slope, display, R | Met in software | Breath by breath, rebreathing only, r and 95 % CI |

Krystal's rev01 points that are still open: thresholds without sources (3.1, 3.5), comfort not turned into numbers (2.x), and "think beyond high CO2: low O2, excessive pressure, incorrect gas mixture, sensor failure" (covered by §4.2–4.4 below).

---

## 4. Requirements that were missed (proposed DTM rev03 inputs)

Numbered by the user need they belong to. "Verify" says how the input would be shown to be met.

### 4.1 The test protocol

| New | Input | Source / why | Verify |
| --- | --- | --- | --- |
| 6.5 | Bag gas: certified premix with a certificate of analysis (adults 7 % CO2 / 93 % O2; infants 5 % CO2 / 95 % O2); starting volume stated (Read: 4–6 L; Rebuck: the subject's vital capacity + 1 L in a 6–10 L bag; infants: not stated by Yamada, ask) | Read 1967, Rebuck 1976, Yamada 2021; DTM 6.1 | Certificate; fill procedure in the SOP |
| 6.6 | The first breaths after switching are excluded from the slope: CHOP and Rebuck coach 3 large breaths "to facilitate mixing"; Read used data from 30 s on, Rebuck's first point is about 45 s in | CHOP, Read, Rebuck | Software test (done, by breaths) |
| 6.7 | Equilibration check: after the first 20 s of rebreathing, inspired and end-tidal CO2 differ by < 2 mmHg; if not, the bag mix was wrong and the test is repeated | Rebuck 1976 | Software (to do): FiCO2 vs EtCO2 is already measured per breath |
| 8.2 | Stopping rules, any one ends rebreathing: EtCO2 ≥ end-test limit; time ≥ limit; SpO2 < 90 %; subject asks to stop or is distressed (infants: wakes); loss of the CO2 or flow signal; operator judgement | Mannée 2017 (PETCO2 > 8.5 kPa, SpO2 < 90 %, subject request); Yamada 2021 (arousal); risk notes | Written in the SOP; alarm tests (V5) |
| 8.3 | A written standard operating procedure: setup, pre-test checks, baseline, switch "at the end of a normal expiration" (Rebuck), stop, recovery, cleaning; no tea or coffee for 2 h before (Read, Rebuck); headache and nausea listed as possible side effects (one of Mannée's 20 subjects withdrew with them) | risk notes; Read, Rebuck, Mannée | Dry run against the SOP |
| 9.6 | Report baseline EtCO2 and MV with the slope; for infants, also the slope per kg (Yamada: mL/min/mmHg/kg, normal term infants 34.6, IQR 29.3–42.8). Adults are reported unnormalised: Read found "no useful correlation with body size" | Yamada 2021, Read 1967 | Software (to do) |

### 4.2 Breathing circuit and gas path

| New | Input | Source / why | Verify |
| --- | --- | --- | --- |
| 10.1 | Two-way rebreathing valve with 22 mm breathing-circuit ports, switched with one hand, position visible | §1.4; mentors: "obtain or build a 2 way valve system" | Inspection; time to switch |
| 10.2 | Connectors to ISO 5356-1 (15/22 mm cones); no glue or tape in the gas path | leak and contamination hazards | Inspection; leak test |
| 10.3 | Gas-path materials evaluated per ISO 18562 (breathing gas pathways); skin-contact parts per ISO 10993-1 | Krystal: 10993 is about biocompatibility, not comfort | Use commercial medical parts and keep their datasheets |
| 10.4 | Apparatus dead space (patient connector + filter + pneumotach + CO2 adapter, up to the valve) stated and small relative to the smallest subject's tidal volume | Dead space adds to rebreathing and blunts the response; an infant's VT is only about 6–8 mL/kg | Fill with water and measure the volume |
| 10.5 | Added resistance ≤ 1.0 cmH2O/L/s at 4 L/s (adults) | Rebuck 1976; a loaded subject under-breathes and the slope reads low | Pressure drop at set flows |
| 10.6 | Nose clip for every mouthpiece test | Air through the nose bypasses the pneumotach and the bag | Checklist |
| 10.7 | Single-use bacterial/viral filter and mouthpiece/mask per subject; cleaning instructions for the reusable valve and pneumotach | peer feedback (cleaning, sanitization); mentors (lab has filters) | SOP; parts list |
| 10.8 | If the capnograph is sidestream, its sample flow is returned to the bag | A sidestream analyser removes 50–200 mL/min from a closed bag circuit; Rebuck returned it "to eliminate changes in the volume of the system" | Plumbing |
| 10.9 | Circuit leak ≤ 5 L/min at a stated pressure (for example 5 cmH2O) | DTM 1.4 has no test pressure | Hold the pressure with a flowmeter feeding the occluded circuit |

### 4.3 Measurement

| New | Input | Source / why | Verify |
| --- | --- | --- | --- |
| 1.5 | Pneumotach and transducer range covers the peak flows at the end of the test for the largest subject, with resolution for the smallest | §1.8: the simulator's adult passes ±100 L/min near 60 mmHg, and its slope (about 0.5 L/min/mmHg) is weak; at Read's normal slopes (1.2–6.2 L/min/mmHg) ventilation at 65 mmHg is far higher. Yamada's infants roughly tripled their tidal volume | Syringe at the highest flows; the app's clipping alarm |
| 1.6 | A differential pressure transducer for the pneumotach, analog output within 0–5 V | missing from the budget | Datasheet; hardware check |
| 1.7 | Capnograph gives the instantaneous CO2 waveform on its analog output (averaging off), with rise time short compared with expiration | CHOP: "set to no-averaging so that it gives just instantaneous CO2" | Step test (V4) |
| 1.8 | CO2 delay measured and set before each session | §1.7 | Measure button; V4 |
| 1.9 | Volumes reported at BTPS | Read corrected all volumes to BTPS. Room-air (ATPS) volumes read about 9 % low against BTPS at lab temperature, and the gas at the pneumotach warms and humidifies during rebreathing | Software (to do); state the convention in the report |
| 1.10 | Pneumotach calibrated with the test gas, or corrected for it | O2-rich gas is roughly 10 % more viscous than air, and a pneumotach reads flow in proportion to viscosity | Syringe calibration with air and with bag gas (V1) |
| 1.11 | Pneumotach protected from condensation (heated, or a filter in front) | rebreathed gas is warm and saturated; water on the screen shifts the calibration | Zero and calibration check before and after a test |
| 1.12 | Calibration before each test day: CO2 zero and span with certified gas, pneumotach with a 3 L syringe (a smaller syringe for pediatric sizes) | feedback: "how would you calibrate this? where do you get an accurate CO2 reading?" | Calibration record in each session file (already written) |

### 4.4 Safety and alarms

| New | Input | Source / why | Verify |
| --- | --- | --- | --- |
| 3.1 (rev) | Medium alarm when EtCO2 ≥ 60 mmHg and a high "end test" alarm when EtCO2 > 65 mmHg during rebreathing, numbers confirmed by the clinicians | mentors; §1.1 | V5 (software done) |
| 3.2 (rev, option A) | Prototype 1.0: the system alerts within one breath of the end-test limit; the operator returns the valve to room air | the valve is manual | V5 |
| 3.2 (rev, option B) | Prototype 2: a normally-open solenoid valve returns to room air when de-energised; the Arduino energises it only while it receives a heartbeat from the app (watchdog ≤ 1 s); a hard-wired stop button cuts its power | risk notes: fail-safe valve, power failure, system freeze, communication failure. Precedent: Mannée 2017 switched their circuit's valves with servos driven by an Arduino Mega | Pull USB / power / kill the app and time the return to room air |
| 3.6 | A certified monitor (pulse oximeter, ideally with capnography) on the subject throughout; this system is never the only alarm | `docs/safety.md`; peer feedback "how do you ensure nothing malfunctions?" | SOP checklist |
| 3.7 | Electrical isolation of anything patient-connected (battery laptop or medical-grade USB isolator) | IEC 60601-1; `docs/safety.md` | Inspection |
| 3.8 | Software developed with traceable requirements and tests (IEC 62304-style); every recording carries the app version and settings | feedback on software bugs and backups | Test suite; session header (both exist) |

### 4.5 Patient interface (answering the feedback on masks)

The mentors supply commercial masks, mouthpieces and filters, so the interface is chosen, not designed. The DTM should say: CHOP's setup (mouthpiece and nose clip) for cooperative older children and adults; a sized commercial face mask with the weight of the tubing supported (the support arm from potential solution 2) for younger children, as in the infant study; single-use parts; fit checked by the operator.

### 4.6 Data, privacy and approvals

| New | Input | Source / why |
| --- | --- | --- |
| 7.10 | No identifiers in files (study code only; exists); recordings stored on an access-controlled CMU or hospital drive, not personal laptops; who can open them and how long they are kept written down | peer feedback on data privacy; DTM 7.7 replacement |
| 7.11 | Subject data needed to interpret the slope recorded separately from the study code: age band, height, weight, sex | pediatric normalisation |
| 7.12 | IRB approval (or a written determination) before anyone, including teammates, breathes on the system | human-subjects rules |

---

## 5. Purchasing: fix before ordering

| Budget line | Problem | Do instead |
| --- | --- | --- |
| 2-way valve (MEETOOT stopcocks, $9.99) | IV stopcock, Luer bore of a few mm | Ask the PFT lab for a breathing-circuit two-way (directional) valve with 22 mm ports, or buy one (the mentors pointed you to Hans Rudolph for the pneumotach; they also make these valves) |
| Tube connectors (aquarium, $15.98) | Not medical, wrong sizes | 22 mm / 15 mm breathing-circuit adapters (cheap, single-use) |
| Gorilla Glue, duct tape | In the gas path they leak and give off fumes | Push-fit medical connectors; tape only for strain relief outside the gas path |
| EtCO2 sensor ($167) | Listing reads like a box of 10 sample lines | Ask the mentors which capnograph the lab has; buy only the matching consumables |
| Pneumotachograph (10/box, $296) | Unclear what it pairs with; no transducer in the budget | Confirm model and flow range against §4.3; add a differential pressure transducer if the lab does not supply one |
| (missing) | — | Nose clips, single-use filters, a certified CO2 calibration gas (or borrow the lab's), a 3 L syringe (lab), a USB isolator |
| Analog-to-digital board ($11.99) | Fine if it is an ADS1115 | The firmware supports the ADS1115 at address 0x48 |

Krystal's 11/3 email asks for one person to own purchasing and close the mask/shipping loop. Sofya is listed as buyer.

---

## 6. Verification and validation plan

TA (10/27): "specify verification process… need a number to compare to". Each test below has a pass number.

| ID | What | How | Pass |
| --- | --- | --- | --- |
| V1 | Volume and MV | 3 L syringe through the pneumotach, 10 strokes at slow, medium and fast speeds; repeat with bag gas | Each VT within ±3 %; MV within ±10 % (DTM 1.1) |
| V2 | Respiratory rate | Syringe strokes timed at 10, 20, 40 and 60 per minute | ±1 breath/min (DTM 1.2) |
| V3 | CO2 accuracy | Sample room air, then certified gas (for example 5 % and 7 %) | ±2 mmHg or ±5 % (DTM 1.3) |
| V4 | CO2 timing | Switch the sample point from room air to calibration gas with the valve; read the transport delay and the 10–90 % rise time from the recording | Delay matches the measured setting; rise time stated |
| V5 | Alarms | Simulator and replay: end-test limit, warning, time limit, apnea, signal loss; with hardware, unplug each sensor and the USB cable | Each alarm within one breath (EtCO2) or within its stated time; tone heard |
| V6 | Analysis matches CHOP | Run CHOP recordings (PowerLab export, with their BASIC/SigmaPlot slopes) through the app | Slope within ±5 % of theirs on every file |
| V7 | Leak | Occluded circuit held at the stated pressure | ≤ 5 L/min (DTM 1.4) |
| V8 | Setup time | Team member applies interface and starts baseline, timed | ≤ 60 s (DTM 5.1) |
| Validation | Real use | After IRB: adult volunteers; each tested twice on different days | Slopes inside Read's normal range (1.16–6.18 L/min/mmHg; x-intercept 34.6–48.6 mmHg); repeat slopes within about 20 % (Read's within-subject SD); reproducibility comparable to Mannée (ICC 0.89) |

V6 needs sample data: Krystal suggested asking the mentors for it, and it is the strongest check of the software available before December. The app needs a LabChart text-export importer for it (backlog, §9).

---

## 7. Where the mid-semester feedback is answered

| Feedback | Answer |
| --- | --- |
| What are the CO2 limits? | Warn 60, end test 65 mmHg during rebreathing, 6 min maximum (§4.4), to be confirmed by Dr. Weiner |
| Automatic stop or manual? Signs the system is failing? | Manual valve with alarms for prototype 1.0; fail-safe solenoid as option B (§4.4). Technical alarms for signal loss, rail, clipping, noise and no data already exist |
| Software bugs / safe backup? | Certified monitor alongside; manual valve; 139 automated tests; alarms on lost data |
| Calibration? | Certified gas and 3 L syringe before each test day (§4.3) |
| Delay and margin of error? | Measured CO2 delay, compensated; accuracy targets V1–V4 |
| Masks: sizes, seal, comfort, weight, restless kids, skin | Commercial sized interfaces from the PFT lab, tubing support, test ≤ 6 min (§4.5) |
| Cleaning between patients | Single-use filters and mouthpieces; cleaning procedure for the valve and pneumotach (10.7) |
| Data privacy | §4.6 |
| Treatment if CO2 gets too high | Turning the valve to room air is the treatment: CO2 falls within minutes |
| Hospital workflow, who runs it | One operator at the valve plus one watching the subject; SOP (8.3) |
| Machine learning? | No: clinicians use a transparent linear regression, which is what CHOP uses |
| Combine both software solutions | Done: live monitoring (B) with the post-test analysis (A) in one app; the next step is the per-test report |
| Presentation: fewer words, references at the end, more graphs like "normal vs CCHS" | For Dec 11: one simulator demo of a full test, the response plot, and the verification numbers |

---

## 8. Questions for the mentors and for Dr. Burman (Nov 19)

These are also in [open-questions.md](open-questions.md) (28–35).

1. End-test limit and warning: is 65 mmHg (warning at 60) right for children? Any SpO2 or time rules?
2. Bag gas and volume: what mix and starting volume does CHOP use (Michelle)? For children?
3. Which capnograph does the lab have: sidestream or mainstream, analog output, can averaging be turned off?
4. Which pneumotach and pressure transducer, and their flow range? Is it heated?
5. Can the lab lend a breathing-circuit two-way valve, nose clips, filters, a 3 L syringe and calibration gas?
6. Can we have two or three CHOP recordings (PowerLab export) with the slopes CHOP calculated, to check our analysis against?
7. How many breaths (or seconds) after the switch should be excluded: CHOP's 3 coached breaths, or Read's first 30 s? Does CHOP drop other breaths by hand, and why?
8. Should children's slopes be normalised per kg, as Yamada did for infants? Which normal values do you compare against?
9. Automatic stop: is a manual valve acceptable for the first prototype?
10. For Dr. Burman: how is the test tolerated by children with CCHS; what makes a test fail; what would make it easier for the family and the staff?

---

## 9. Schedule to December 11

Krystal (11/3) asked for a weekly plan with owners, purchases and a tangible outcome each week, and for work to continue in parallel while hardware is waiting. Owners in brackets are suggestions from the roles already in the Drive; confirm them as a team.

| Week | Hardware and purchasing | Software and analysis | Documents | Tangible outcome |
| --- | --- | --- | --- | --- |
| **Nov 9–13** | Email the mentors questions 3–6 and get shipping dates; order transducer, connectors, nose clips, filters (Sofya) | Run the firmware on a real Arduino and ADS1115 for the first time; 10-minute drop test | DTM rev03 with §4 inputs (Kaitlyn); Gantt chart to Krystal; mid-semester feedback video (11/9) | Arduino streaming real volts; DTM rev03 draft |
| **Nov 16–20** | Assemble the circuit with whatever has arrived; first syringe calibration | Measure the CO2 delay on the real capnograph; start the LabChart importer if CHOP data arrives | Dr. Burman interview (Thu 11/19); stakeholder write-up (due Mon 11/23) | Calibrated pneumotach; recorded bench session |
| **Nov 23–27** (Thanksgiving) | — | Per-test report (§10) | Proposal draft (due Mon 11/30): sections as assigned in the proposal outline | Proposal draft |
| **Nov 30–Dec 4** | Leak test, alarm tests with hardware | V1–V6 runs; fix what they find | Verification results table | Numbers for every DTM input that can be tested |
| **Dec 7–11** | Prototype ready to show | Demo script: full simulated test, then the bench rig | Final presentation, dry run twice; final proposal (Dec 11) | Presentation and prototype (Fri Dec 11) |

---

## 10. Software backlog (after this update)

In priority order.

1. **LabChart/PowerLab text import** so CHOP recordings can be replayed and compared (V6).
2. **Per-test report** (one page: response plot, slope ± CI, r, x-intercept, baseline, duration, alarms, accepted/rejected breaths, calibration), as promised on slides 11–12.
3. **BTPS volume correction** with gas temperature and humidity settings (1.9), and a gas-composition factor for the pneumotach (1.10).
4. **Pre-test checklist screen**: calibration done today, CO2 delay measured, alarm tested, barometric pressure set, certified monitor on, nose clip on.
5. **Equilibration check** (6.7): flag a test whose FiCO2 and EtCO2 still differ by ≥ 2 mmHg after 20 s of rebreathing.
6. **Interval averaging option**: Read and Rebuck fitted ventilation over half-minute intervals and Mannée used a 5-breath moving average; CHOP and Yamada work breath by breath (the current default). Offer both, so results can be compared with any of them.
7. **Realistic simulator response** (about 2 L/min/mmHg) so the demo and the flow-range checks resemble a real adult.
8. **Patient-size presets** (infant, child, adult) for the breath-detection deadband, minimum breath length and display scales. The current defaults (3 L/min deadband, 1 s minimum breath) suit adults; an infant breathing at 60/min has 1 s breaths and low flows.
9. **Slope per kg** when the subject's weight is entered (9.6).
10. **O2 channel** on ADC input A3, if question 33 says O2 should be measured.
11. **Valve control and watchdog** in the firmware, if option B is chosen for DTM 3.2.
