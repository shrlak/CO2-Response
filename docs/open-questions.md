# Open questions

Decisions CO2-Response still needs. Until each is answered, the system uses the assumption shown. What the mentors and team documents have already settled is marked **Partly answered**, with its source in [project-background.md](project-background.md). **Discuss and answer in [issue #4](https://github.com/shrlak/CO2-Response/issues/4)** (reply by number); this page records where each answer gets entered.

| # | Question | Assumed until answered | Where the answer goes |
| --- | --- | --- | --- |
| **Sensors** | | | |
| 1 | Which capnograph / CO2 monitor, and what does its analog output read? **Partly answered:** the lab may lend one (Dr. Weiner to send specs). CHOP uses a Novametrix with analog output, set to no averaging | 0–5 V linear, 0–10 % CO2 | `co2response/config/channels.json` → `co2.calibration` (or Settings → Calibration) |
| 2 | Which flow sensor, and what is its output scaling? | 2.5 V = 0 L/min, 40 L/min per V | `channels.json` → `flow.calibration` |
| 3 | Which pneumotachograph and pressure transducer? | 2.5 V = 0 Pa, 200 Pa per V; k1 0.2 L/min/Pa | `channels.json` → `pneumo.calibration`, `pneumo.flowModel` |
| 4 | Does the flow sensor measure the patient's airflow, or a gas-delivery line? **Answered:** patient airflow, through an in-line pneumotach at the mouthpiece (Read's circuit) | patient airflow | `channels.json` → `flow.label`; PLAN.md UI |
| 5 | Are all sensor outputs within 0–5 V? | yes | `firmware/CO2Response/config.h` → `ADS_GAIN`; `channels.json` → `adc.railHighV`; a divider if over 5 V |
| **Acquisition** | | | |
| 6 | Which DAQ for the second version (NI model, LabJack, MCC, other)? | not decided | `co2r-daq --driver`, `python/examples/*.json`; new driver in `python/co2r/drivers/` if not NI/LabJack |
| 7 | Which Arduino board? | Uno | `firmware/platformio.ini` env |
| 8 | Is 100 samples/s per channel enough? | yes | `config.h` → `DEFAULT_RATE_HZ`; `co2r-daq --rate` |
| **Alarms** | | | |
| 9 | High EtCO2 limit. **Partly answered:** CHOP ends the test at 60–65 mmHg; the team's design is a warning near 60 and a stop alert at 65. Confirm with the mentors, then add a second tier | 50 mmHg | `co2response/settings.py` → `AlarmSettings.high_mm_hg` |
| 10 | Low EtCO2 limit | off | `AlarmSettings.low_mm_hg` |
| 11 | Alarm on EtCO2 per breath or live CO2? (The team's design checks each breath) | EtCO2 per breath | `AlarmSettings.source` |
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
| 20 | People, animals, or bench only? **Partly answered:** cooperative adult volunteers first (mouthpiece and nose clip), then infants and children. Needs approval before any person is tested | bench until approved | `docs/safety.md`; lab safety review |
| 21 | Certified monitor alongside as the primary alarm? | required for live use | `docs/safety.md`, SETUP-GUIDE checklist |
| 22 | Auto-start recording on connect for overnight runs? | off | `Settings.auto_record` |
| 23 | Study-code format (no identifiers) | `STUDY-01` | `Settings.study_code`; SETUP-GUIDE |
| 24 | Where recordings are stored after a session | `Documents/CO2-Response/sessions` (desktop app); browser storage + export (Pages site) | SETUP-GUIDE §9 |
| **Repository** | | | |
| 25 | Private or public repo? | private | GitHub settings (Pages from a private repo needs GitHub Pro) |
| 26 | License | all rights reserved | `LICENSE` |
| 27 | Clinician review of the screen layout? | not yet | PLAN.md Phase 0 |
| **Test protocol (Read's rebreathing method)** | | | |
| 28 | Rebreathing-bag gas mix (O2 %) and starting volume (ask CHOP) | not set | `docs/project-background.md` §2 |
| 29 | Should the response slope be normalized for body size (weight, height, BSA) for children? | not normalized | `co2response/processing/metrics.py` → `co2_response` |
| 30 | Which breaths count as baseline: all before the marker, or only the last N / a stable stretch? | all clean breaths before the marker labelled "Rebreathing" in the trend window | `co2_response` (desktop and browser); `python/co2r/session.py` → `co2_response` |
| 31 | Should the clinician be able to exclude breaths by hand, as CHOP does? | automatic noise screen only | review screen and session format (new feature) |
| 32 | Can CHOP share a PowerLab (LabChart) export, so it can be imported and the analysis compared? | no import | new importer in `co2response/storage/` |
| 33 | Will the valve stay manual, or should the software switch it (automatic stop ≤ 1 s, emergency stop)? | manual valve; alarms only | firmware, `docs/safety.md` |
| 34 | Record SpO2 as a safety channel? | no | spare CH3, `channels.json` |

When a question is settled, tick it in issue #4 and update the file in the last column (or ask Claude Code to).
