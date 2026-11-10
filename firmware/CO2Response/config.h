// CO2-Response firmware configuration. Everything you might need to change is here.
#pragma once

#define FW_VERSION "1.2.0"

// ---- serial link (must match the dashboard's baud setting) ----
#define BAUD_RATE 115200

// ---- sampling ----
#define DEFAULT_RATE_HZ 100   // samples per second per channel
// A pulse oximeter's analog output on A3, sent as a fourth value on every data line
// (the app's "SpO2 channel connected" setting reads it). It cannot be combined with
// PNEUMO_DIFFERENTIAL, which also uses A3.
#define SPO2_CHANNEL 0
#if SPO2_CHANNEL
#define NUM_CHANNELS 4        // CH0 CO2, CH1 flow sensor, CH2 pneumotach, CH3 SpO2
#define MAX_RATE_HZ 150       // ADS1115: 860 conversions/s shared by 4 channels
#else
#define NUM_CHANNELS 3        // CH0 CO2, CH1 flow sensor, CH2 pneumotach
#define MAX_RATE_HZ 200       // ADS1115: 860 conversions/s shared by 3 channels
#endif
#define AUTOSTART 1           // stream on power-up (the dashboard also sends START)

// ---- host watchdog ----
// The monitor app sends "HB" once a second. If the heartbeats stop for this long (the
// app closed or crashed, the computer went to sleep, the tab was put in the background),
// the board sounds its own buzzer and blinks its LED until they come back, because by
// then nothing on the computer is checking the CO2 alarms. The watchdog arms itself on
// the first heartbeat, so nothing sounds with software that does not send them.
// 0 turns the watchdog off.
#define WATCHDOG_TIMEOUT_MS 3000
#define BUZZER_PIN 8              // piezo buzzer between this pin and GND; -1 = no buzzer
#define BUZZER_HZ 2700
#define ALARM_LED_PIN LED_BUILTIN // blinks with the buzzer

// ---- valve output (automatic stop) ----
// A digital output for a valve driver (a relay or MOSFET module switching a solenoid or
// motorised two-way valve). The pin is HIGH only while the subject should breathe from
// the bag: the monitor app sends "VALVE 1" when rebreathing starts and "VALVE 0" when the
// test ends, at the stop alarm or at an emergency stop. Wire the valve so that it is on
// ROOM AIR when the pin is LOW or the board has no power. The board itself drops the pin
// LOW at power-up, at reset and when the host's heartbeat is lost, so a stopped app or
// an unplugged cable can never leave the subject on the bag.
// -1 = no valve output (the operator turns a manual valve; the app only alarms).
#define VALVE_PIN 7

// ---- ADC (default: ADS1115 over I2C) ----
// TODO: confirm the ADC board. To use another ADC, add a driver like
// ads1115_driver.h and change the two lines in CO2Response.ino marked "ADC driver".
#define ADS_I2C_ADDRESS 0x48  // ADDR pin to GND
#define I2C_CLOCK_HZ 400000
// PGA gain sets the full-scale range. GAIN_TWOTHIRDS = +/-6.144 V, needed for 0-5 V sensors
// on a 5 V board (inputs must still never exceed VDD + 0.3 V).
// Use GAIN_ONE (+/-4.096 V) for 0-3.3 V sensors to gain resolution.
#define ADS_GAIN GAIN_TWOTHIRDS

// Read the pneumotach transducer differentially on AIN2-AIN3 (for a two-output transducer)
// instead of single-ended on AIN2.
#define PNEUMO_DIFFERENTIAL 0
#if SPO2_CHANNEL && PNEUMO_DIFFERENTIAL
#error "SPO2_CHANNEL and PNEUMO_DIFFERENTIAL both need input A3: use one of them"
#endif
