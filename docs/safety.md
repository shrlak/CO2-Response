# Safety, limitations and data handling

CO2-Response is a **research prototype, not a medical device**. It is not certified to IEC 60601 or any other standard, and its alarms are styled after IEC 60601-1-8 conventions only so they look familiar.

## Rules for any live study

- **Never the only monitor.** Keep a certified capnograph/monitor as the primary alarm on any person or animal.
- **The alarm only works while:** the app is running, the computer is awake (the app asks Windows and macOS to stay awake while a device is connected, but power settings and updates can still sleep it), the device is connected, and a **sound output is armed**. Audio arms by itself when the app finds a speaker; if it cannot, a persistent blue "Audible alarm NOT armed" notice shows and the app falls back to the system beep.
- **The board has its own buzzer for when the app stops.** With firmware 1.1 or later and a piezo buzzer on pin 8 ([wiring.md](wiring.md#buzzer-host-watchdog)), the Arduino sounds the buzzer if the app's heartbeat stops for 3 s: the app closed or crashed, the computer slept, or the browser tab was put in the background. It is a reminder that nothing is watching the CO2, not a CO2 alarm: the board does not know the CO2 level. Without the buzzer wired, a stopped app is silent.
- **Test before every session:** **Alarm limits > Test alarm** plays the high and medium tones and shows the banner.
- **Two CO2 limits.** The **warning limit** (default 60 mmHg, medium priority, 3-pulse tone) says the end of the test is near. The **stop limit** (default 65 mmHg, high priority, 10-pulse tone, "STOP TEST") says to turn the valve back to room air now. The stop limit is checked on every sample (CO2 at or above it for half a second) and on every breath, so it does not wait for a second breath. These defaults follow the team's design and CHOP's 60 to 65 mmHg stop; confirm them with the clinicians ([open-questions.md](open-questions.md)). **By default the software does not turn the valve: the operator does.**
- **If the app switches the valve** (a valve driver on the board and **Switch the valve from the app** on), it returns the valve to room air at End test, at the stop alarm, on apnea, on low SpO2, at an **Emergency stop** and on disconnect, and the board returns it by itself if the app stops. Treat this as a backstop. It has not been tested on hardware; a valve can stick and a driver can fail, so use a valve that rests on room air without power, check it before every session, and stay ready to turn it or take the mouthpiece away by hand.
- **SpO2** (optional) is a second safety signal from a pulse oximeter's analog output: a high-priority alarm below 90 % for 10 s by default. It is only as good as the scaling entered for it; compare the tile with the oximeter's own display before each session. The oximeter's own alarm stays the primary one.
- **Silence is limited** to 120 s and re-arms by itself. A new high-priority alarm cancels a silence, so silencing the warning never mutes the stop alarm that follows. Volume cannot be set to zero.
- **Rebreathing time limit.** A medium alarm when 5 minutes (adjustable) have passed since the Rebreathing marker and the test has not been ended.
- **Technical alarms** fire for: no data for 3 s, device disconnected, ADC not found, a channel stuck at an ADC rail, a run of breaths too noisy to trust, and apnea (no breath for 20 s by default). Blue notices (no sound) cover an unarmed speaker, dropped samples, a volume mismatch (leak or drifted zero), an old calibration and settings that could not be saved.
- **A clipped flow signal is not trusted.** If a hard breath takes the pneumotach past its range, that breath's volume under-reads, so the breath is left out of the numbers and the fit, and a run of them raises the SIGNAL TOO NOISY alarm. The stop limit still works, because it reads the CO2 signal directly.
- **Software can crash, and computers update and sleep.** Alarm checks run in the data path rather than the drawing code, so they continue while the window is minimised or covered (a high alarm also flashes the taskbar entry), but a closed or crashed app stops everything.

## Electrical safety

If anything connects to a person or animal, the Arduino and ADC must be isolated from mains-powered equipment: run the laptop on battery or use a medical-grade USB isolator. Go through the Cook Lab and CMU safety process before any human or animal use.

## Data handling

- **No patient identifiers.** Sessions are named by study code and date/time only. Do not put names, MRNs or dates of birth in the study code or event labels.
- Data never leaves the computer: the app talks to the Arduino directly and has no network features apart from the optional bridge, which listens on localhost unless you choose otherwise.
- Recorded CSVs, calibrations and settings stay on the computer that made them.

## Supported computers

The monitor app runs on Windows 10/11, macOS and Linux with Python 3.10 or newer, or as a standalone app with no Python installed (built by `.github/workflows/release.yml`). CI runs the full test suite, including the window, on all three.

## What has not been validated

- Nothing here has been run on the real sensors yet. The firmware builds for Uno, Mega and Nano and its line format is tested, but the watchdog, the buzzer, the valve output, the SpO2 channel and the sampling have not been tried on a board.
- The LabChart import was written from LabChart's documented text-export layout and tested on exports built from the sample session. It has not read a real CHOP export.
- The Capnostream export merge (`--merge-capno`) was written without a real export and tested on exports built from the sample session. It lines the monitor's CO2 up with the flow by comparing the two, which regular breathing can leave ambiguous by a whole breath: it says so, and a 10 s breath-hold near the start of a study or the `--capno-start-offset` hint settles it.
- The noise screen, the baseline-steadiness indicator, the 30 s left out at the start of rebreathing and the volume-mismatch limit were tuned on simulated breathing. Check them against real recordings.
- The traceability table ([requirements-traceability.md](requirements-traceability.md)) lists which design input each automated test covers, and which still need a bench test.
