// CO2-Response firmware for the Uno R4 board with digital sensors. Everything you
// might need to change is here. Wiring: docs/wiring.md ("Uno R4 board") and the
// picture docs/images/wiring-r4.svg.
//
// Parts this is set up for (the team's order):
//   Arduino UNO R4 Minima (ABX00080)
//   Sensirion SFM3300-250-D flow sensor (I2C, 5 V, +/-250 slm)
//   GSS SprintIR-R 20 % CO2 sensor (UART, 3.3 V logic, 50 readings a second)
//   Adafruit 757 BSS138 4-channel level shifter (the SprintIR's 3.3 V UART)
//   Adafruit 2107 ADuM4160/ADuM5000 USB isolator (computer <-> board)
//   ZR370-02PM 4.5 V air pump (the CO2 sample pump) switched by an IRLZ44N
//   5 V / 2 A wall adapter + 2.1 mm barrel jack (the 5V_EXT rail: pump and SprintIR)
//   Adafruit 375 magnetic contact switch (the valve handle's position)
#pragma once

#define FW_VERSION "2.1.0"

// ---- serial link to the computer (USB, through the isolator) ----
#define BAUD_RATE 115200
#define DEFAULT_RATE_HZ 100   // data lines per second
#define MAX_RATE_HZ 200
#define AUTOSTART 1           // stream on power-up (the app also sends START)

// ---- Sensirion SFM3300-250-D flow sensor (I2C, 5 V: wired straight to the Uno, no shifter) ----
#define SFM_I2C_ADDRESS 0x40
#define I2C_CLOCK_HZ 100000   // the sensor allows 400 kHz; 100 kHz is kinder to a long cable
#define SFM_CMD_START 0x1000  // start continuous measurement
#define SFM_CMD_RESET 0x2000  // soft reset
#define SFM_CMD_SERIAL 0x31AE // read the 32-bit serial number
// flow [slm] = (raw - offset) / scale. Datasheet values for the SFM3300 in air, N2 or O2.
#define SFM_OFFSET 32768.0f
#define SFM_SCALE 120.0f
// +1 if inspiration (towards the subject) reads positive with the sensor as mounted, -1 if not.
// The arrow on the SFM3300 points in the positive direction.
#define SFM_FLOW_SIGN 1
// The sensor makes a new measurement every 0.5 ms. The firmware reads it every
// SFM_READ_US and sends the mean of the reads since the last data line: an
// anti-aliasing average that lowers the noise of the 100 Hz flow (1 = read once per line).
#define SFM_AVERAGE 1
#define SFM_READ_US 1000

// ---- GSS SprintIR-R-20 CO2 sensor (UART 9600 8N1 on Serial1 = D0/D1, 3.3 V logic via the BSS138) ----
#define CO2_BAUD 9600
// The SprintIR-R measures 50 times a second. At 9600 baud only one value per reading
// fits, so the sensor is asked for just one: the raw value 'z' (follows a breath fastest)
// or, with CO2_USE_FILTERED 1, its own smoothed value 'Z'.
#define CO2_USE_FILTERED 0
#define CO2_DEFAULT_MULTIPLIER 10  // the 0-20 % part reports in units of 10 ppm
#define CO2_TIMEOUT_MS 500    // no reading for this long = the sensor is not answering

// ---- what a sensor that is not answering sends (the app shows --- and alarms) ----
#define NOT_ANSWERING 9999.0f

// ---- controls and indicators ----
#define BUTTON_PIN 2          // optional stop / marker button to GND (INPUT_PULLUP); unwired = never pressed
#define REED_PIN 3            // Adafruit 375 magnetic contact switch to GND (INPUT_PULLUP); -1 = none
#define REED_CLOSED_IS_BAG 1  // the magnet on the valve handle closes the switch at the bag position
#define ALARM_LED_PIN LED_BUILTIN  // the board's own LED (D13); move to D7 for an external LED + 330 ohm
#define BUZZER_PIN 8          // optional ACTIVE piezo buzzer to GND (or through Q2 if it needs more current); -1 = none
#define HEATER_PIN -1         // the SFM3300 has no heater
#define PUMP_PIN 10           // gate of Q1 (IRLZ44N): the sample pump from 5V_EXT
#define SERVO_PIN -1          // D6 for a valve servo (powered from 5V_EXT); -1 = manual valve
#define SERVO_AIR_DEG 0
#define SERVO_BAG_DEG 90

// Sample pump PWM duty, 0-255. The ZR370-02PM moves about 2.5 L/min with nothing on its
// inlet, far more than a sidestream sample needs; ~150-250 mL/min through the filter and
// the SprintIR-R is the aim. Measure it (docs/wiring.md, "Setting the sample flow") and set
// it here or live with the host command "PUMP <0-255>". The pump runs while the board streams.
#define PUMP_DUTY 140
#define HEATER_DUTY 0

// ---- host watchdog ----
// The monitor app sends "HB" once a second. If the heartbeats stop for this long, the
// board flashes its LED (and sounds a buzzer if one is fitted), and sends the valve (if a
// servo drives it) back to room air, because nothing on the computer is checking the CO2.
#define WATCHDOG_TIMEOUT_MS 3000
