# Safety, limitations and data handling

CO2-Response is a **research prototype, not a medical device**. It is not certified to IEC 60601 or any other standard, and its alarms are styled after IEC 60601-1-8 conventions only so they look familiar.

## Rules for any live study

- **Never the only monitor.** Keep a certified capnograph/monitor as the primary alarm on any person or animal.
- **The alarm only works while:** the browser tab is open, the computer is awake (the app requests a screen wake lock but the OS can still sleep), the device is connected, and **audio is armed** (press **Arm audio** once per session; browsers block sound until you click). A persistent blue "Audible alarm NOT armed" notice shows until it is.
- **Test before every session:** **Alarm limits > Test alarm** plays the high and medium tones and shows the banner.
- **Silence is limited** to 120 s and re-arms by itself. Volume cannot be set to zero.
- **Technical alarms** fire for: no data for 3 s, device disconnected, ADC not found, a channel stuck at an ADC rail, and apnea (no breath for 20 s by default).
- **Browsers can crash, update, or throttle background tabs.** Alarm checks run in the data path rather than the drawing loop, so they continue in a background tab, but a closed or crashed tab stops everything.

## Electrical safety

If anything connects to a person or animal, the Arduino and ADC must be isolated from mains-powered equipment: run the laptop on battery or use a medical-grade USB isolator. Go through the Cook Lab and CMU safety process before any human or animal use.

## Data handling

- **No patient identifiers.** Sessions are named by study code and date/time only. Do not put names, MRNs or dates of birth in the study code or event labels.
- Data never leaves the computer: the page is static and talks to the Arduino directly. The optional bridge listens on localhost unless you choose otherwise.
- Recorded CSVs, calibrations and settings stay on the computer that made them.

## Browser support

Web Serial needs desktop Chrome or Edge on `https://` or `localhost`. Use the Python bridge for Safari or Firefox.
