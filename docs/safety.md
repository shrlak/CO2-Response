# Safety, limitations and data handling

CO2-Response is a **research prototype, not a medical device**. It is not certified to IEC 60601 or any other standard, and its alarms are styled after IEC 60601-1-8 conventions only so they look familiar.

## Rules for any live study

- **Never the only monitor.** Keep a certified capnograph/monitor as the primary alarm on any person or animal.
- **The alarm only works while:** the app is running, the computer is awake (the app asks Windows and macOS to stay awake while a device is connected, but power settings and updates can still sleep it), the device is connected, and a **sound output is armed**. Audio arms by itself when the app finds a speaker; if it cannot, a persistent blue "Audible alarm NOT armed" notice shows and the app falls back to the system beep.
- **Test before every session:** **Alarm limits > Test alarm** plays the high and medium tones and shows the banner.
- **Silence is limited** to 120 s and re-arms by itself. Volume cannot be set to zero.
- **Technical alarms** fire for: no data for 3 s, device disconnected, ADC not found, a channel stuck at an ADC rail, and apnea (no breath for 20 s by default).
- **Software can crash, and computers update and sleep.** Alarm checks run in the data path rather than the drawing code, so they continue while the window is minimised or covered (a high alarm also flashes the taskbar entry), but a closed or crashed app stops everything.

## The rebreathing test

- **The software cannot stop the CO2.** The two-way valve is turned by hand. The end-test, warning and time-limit alarms tell the operator to turn it back to room air; someone must be at the valve for the whole rebreathing period.
- While rebreathing, the high EtCO2 limit is replaced by the end-test limit (default 65 mmHg, to be confirmed by the clinicians), because the normal limit would sound in every test. It stays in force in recovery until EtCO2 is back under the normal limit, or for 5 minutes.
- A certified pulse oximeter (with its own low-SpO2 alarm) must be on the subject: this system does not measure oxygen.
- Press **End rebreathing** when the valve goes back to room air. Until then the app keeps the end-test limit and the timer running.

## Electrical safety

If anything connects to a person or animal, the Arduino and ADC must be isolated from mains-powered equipment: run the laptop on battery or use a medical-grade USB isolator. Go through the Cook Lab and CMU safety process before any human or animal use.

## Data handling

- **No patient identifiers.** Sessions are named by study code and date/time only. Do not put names, MRNs or dates of birth in the study code or event labels.
- Data never leaves the computer: the app talks to the Arduino directly and has no network features apart from the optional bridge, which listens on localhost unless you choose otherwise.
- Recorded CSVs, calibrations and settings stay on the computer that made them.

## Supported computers

The monitor app runs on Windows 10/11, macOS and Linux with Python 3.10 or newer. CI runs the full test suite, including the window, on all three.
