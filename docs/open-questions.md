# Open questions

Decisions CO2-Response still needs. Until each is answered, the system uses the assumption shown. **Discuss and answer in [issue #4](https://github.com/shrlak/CO2-Response/issues/4)** (reply by number); this page records where each answer gets entered.

| # | Question | Assumed until answered | Where the answer goes |
| --- | --- | --- | --- |
| **Sensors** | | | |
| 1 | Which capnograph / CO2 monitor, and what does its analog output read? | 0–5 V linear, 0–10 % CO2 | `co2response/config/channels.json` → `co2.calibration` (or Settings → Calibration) |
| 2 | Which flow sensor, and what is its output scaling? | 2.5 V = 0 L/min, 40 L/min per V | `channels.json` → `flow.calibration` |
| 3 | Which pneumotachograph and pressure transducer? | 2.5 V = 0 Pa, 200 Pa per V; k1 0.2 L/min/Pa | `channels.json` → `pneumo.calibration`, `pneumo.flowModel` |
| 4 | Does the flow sensor measure the patient's airflow, or a gas-delivery line? | patient airflow | `channels.json` → `flow.label`; PLAN.md UI |
| 5 | Are all sensor outputs within 0–5 V? | yes | `firmware/CO2Response/config.h` → `ADS_GAIN`; `channels.json` → `adc.railHighV`; a divider if over 5 V |
| **Acquisition** | | | |
| 6 | Which DAQ for the second version (NI model, LabJack, MCC, other)? | not decided | `co2r-daq --driver`, `python/examples/*.json`; new driver in `python/co2r/drivers/` if not NI/LabJack |
| 7 | Which Arduino board? | Uno | `firmware/platformio.ini` env |
| 8 | Is 100 samples/s per channel enough? | yes | `config.h` → `DEFAULT_RATE_HZ`; `co2r-daq --rate` |
| **Alarms** | | | |
| 9 | High EtCO2 limit | 50 mmHg | `co2response/settings.py` → `AlarmSettings.high_mm_hg` |
| 10 | Low EtCO2 limit | off | `AlarmSettings.low_mm_hg` |
| 11 | Alarm on EtCO2 per breath or live CO2? | EtCO2 per breath | `AlarmSettings.source` |
| 12 | Seconds without a breath before the apnea alarm | 20 s | `AlarmSettings.apnea_sec` |
| 13 | Breaths past the limit before alarming | 2 | `AlarmSettings.delay_breaths` |
| **Display** | | | |
| 14 | CO2 in mmHg or %? | mmHg | `settings.py` → `Settings.co2_unit` |
| 15 | Sweep speed | 12 s per screen | `Settings.sweep_sec` |
| 16 | Lab barometric pressure | 760 mmHg (Pittsburgh is usually near 735) | `ProcessingOptions.barometric_mm_hg` |
| 17 | Water-vapor (BTPS) correction | off | `ProcessingOptions.water_vapor` |
| **Calibration** | | | |
| 18 | CO2 calibration gas and certified concentration | 5 % | `SPAN_DEFAULT` in `co2response/ui/settings/calibration.py` and `co2response/web/settings_view.py`; `docs/calibration.md` |
| 19 | 3 L syringe? Reference flowmeter or manometer? | syringe available | `docs/calibration.md` |
| **Use and safety** | | | |
| 20 | People, animals, or bench only? | bench until approved | `docs/safety.md`; lab safety review |
| 21 | Certified monitor alongside as the primary alarm? | required for live use | `docs/safety.md`, SETUP-GUIDE checklist |
| 22 | Auto-start recording on connect for overnight runs? | off | `Settings.auto_record` |
| 23 | Study-code format (no identifiers) | `STUDY-01` | `Settings.study_code`; SETUP-GUIDE |
| 24 | Where recordings are stored after a session | `Documents/CO2-Response/sessions` (desktop app); browser storage + export (Pages site) | SETUP-GUIDE §9 |
| **Repository** | | | |
| 25 | Private or public repo? | private | GitHub settings (Pages from a private repo needs GitHub Pro) |
| 26 | License | all rights reserved | `LICENSE` |
| 27 | Clinician review of the screen layout? | not yet | PLAN.md Phase 0 |
| **Rebreathing test** (from [requirements-review.md](requirements-review.md)) | | | |
| 28 | End-test EtCO2 limit, and the warning before it. DTM rev02 input 3.1 (inspired CO2 > 7 % for 10 s) contradicts the CHOP practice of stopping at 60–65 mmHg | end 65, warn 60 mmHg | `AlarmSettings.test_end_mm_hg`, `test_warn_mm_hg`; DTM 3.1 |
| 29 | Longest rebreathing period | 6 min (DTM 8.1) | `AlarmSettings.test_max_sec` |
| 30 | Bag gas mixture and starting volume (DTM 6.1 says 5 % CO2 / ≥ 90 % O2, as Yamada used in infants; Read and Rebuck used 7 % CO2 / 93 % O2 in adults, 4–6 L or vital capacity + 1 L). Michelle at CHOP knows what they use | not decided | DTM 6.1; SETUP-GUIDE |
| 31 | How many breaths (or seconds) at the start of rebreathing to leave out of the slope (CHOP and Rebuck coach 3 large breaths; Read used data from 30 s on) | 3 breaths | `ProcessingOptions.response_skip_breaths` |
| 32 | Automatic stop (DTM 3.2): keep the manual valve and reword 3.2 for prototype 1.0, or add a normally-open solenoid valve with a hardware watchdog? | manual valve; software alarms only | DTM 3.2, 3.3; firmware |
| 33 | Is oxygen measured (FiO2 sensor on spare ADC input A3), or does a certified pulse oximeter alongside cover DTM 3.4/3.5? | certified oximeter alongside, FiO2 not measured | DTM 3.4–3.5, 6.3–6.4, 7.8 |
| 34 | Capnograph: sidestream or mainstream, analog output with averaging off, and its delay | sidestream, delay measured in the app | `ProcessingOptions.co2_delay_ms` |
| 35 | Report the slope normalised to body weight for children (Yamada: mL/min/mmHg/kg)? | not normalised | requirements-review.md |

When a question is settled, tick it in issue #4 and update the file in the last column (or ask Claude Code to).
