// CO2-Response firmware for the Uno R4 board with digital sensors. Everything you
// might need to change is here. Wiring: docs/wiring.md ("Uno R4 board").
#pragma once

#define FW_VERSION "2.0.0"

// ---- serial link to the computer (USB) ----
#define BAUD_RATE 115200
#define DEFAULT_RATE_HZ 100   // data lines per second
#define MAX_RATE_HZ 200
#define AUTOSTART 1           // stream on power-up (the app also sends START)

// ---- Sensirion SFM3304-D flow sensor (I2C, 3.3 V, through the BSS138 level shifter) ----
#define SFM_I2C_ADDRESS 0x2E
#define I2C_CLOCK_HZ 100000   // 100 kHz is safe through a BSS138 shifter and the eval cable
// Start continuous measurement, calibrated for air. (O2: 0x3603, air/O2 mix: 0x3632.)
#define SFM_CMD_START_AIR 0x3608
#define SFM_CMD_STOP 0x3FF9
#define SFM_CMD_READ_SCALE 0x3661  // scale factor, offset and unit for a measurement command
// Used only if the sensor does not answer the scale-factor command.
// TODO: confirm both against the SFM3304-D datasheet for your part.
#define SFM_DEFAULT_SCALE 2500.0f
#define SFM_DEFAULT_OFFSET -6000.0f
// +1 if inspiration (towards the subject) reads positive with the sensor as mounted, -1 if not
#define SFM_FLOW_SIGN 1

// ---- GSS SprintIR-6S-20 CO2 sensor (UART 9600 8N1 on Serial1 = D0/D1, 3.3 V) ----
#define CO2_BAUD 9600
// The sensor sends "Z <filtered> z <raw>" 20 times a second; the value times the
// multiplier it reports (10 for the 0-20 % part) is ppm. The raw value follows a
// breath fastest; set CO2_USE_FILTERED 1 for the sensor's own smoothing.
#define CO2_USE_FILTERED 0
#define CO2_DEFAULT_MULTIPLIER 10
#define CO2_TIMEOUT_MS 1000   // no reading for this long = the sensor is not answering

// ---- what a sensor that is not answering sends (the app shows --- and alarms) ----
#define NOT_ANSWERING 9999.0f

// ---- controls and indicators (pins from the wiring diagram) ----
#define BUTTON_PIN 2          // stop / marker button to GND (INPUT_PULLUP)
#define REED_PIN 3            // valve reed switch to GND (INPUT_PULLUP); -1 = none
#define REED_CLOSED_IS_BAG 1  // the magnet on the handle closes the switch at the bag position
#define ALARM_LED_PIN 7       // alarm LED through 330 ohm to GND
#define BUZZER_PIN 8          // ACTIVE piezo buzzer to GND (it makes its own tone)
#define HEATER_PIN 9          // gate of Q1 (IRLZ44N): the SFM3304-D heater from 5V_EXT
#define PUMP_PIN 10           // gate of Q2 (IRLZ44N): the sample pump from 5V_EXT
#define SERVO_PIN -1          // D6 for a valve servo (powered from 5V_EXT); -1 = manual valve
#define SERVO_AIR_DEG 0
#define SERVO_BAG_DEG 90

// heater and pump PWM duty, 0-255. The pump runs while the board streams.
#define HEATER_DUTY 255
#define PUMP_DUTY 255

// ---- host watchdog ----
// The monitor app sends "HB" once a second. If the heartbeats stop for this long, the
// board sounds its buzzer and flashes its LED, and sends the valve (if a servo drives
// it) back to room air, because nothing on the computer is checking the CO2 any more.
#define WATCHDOG_TIMEOUT_MS 3000
