// CO2-Response firmware for the Uno R4 board with digital sensors. Everything you
// might need to change is here. Wiring: docs/wiring.md ("Uno R4 board") and the
// picture docs/images/wiring-r4.svg.
//
// Parts this is set up for (the team's order):
//   Arduino UNO R4 Minima (ABX00080)
//   Sensirion SFM3300-250-D flow sensor (I2C, 5 V, +/-250 slm)
//   GSS SprintIR-R 20 % CO2 sensor (UART 38400 baud, 3 V logic, 50 readings a second)
//   Adafruit 757 BSS138 4-channel level shifter (the SprintIR's 3.3 V UART)
//   Adafruit 2107 ADuM4160/ADuM5000 USB isolator (computer <-> board)
//   ZR370-02PM 4.5 V air pump (the CO2 sample pump) switched by an IRLZ44N
//   5 V / 2 A wall adapter + 2.1 mm barrel jack (the 5V_EXT rail: pump and SprintIR)
//   Adafruit 375 magnetic contact switch (the valve handle's position)
#pragma once

#define FW_VERSION "2.3.0"

// ---- serial link to the computer (USB, through the isolator) ----
#define BAUD_RATE 115200
#define DEFAULT_RATE_HZ 100   // data lines per second
#define MAX_RATE_HZ 200
#define AUTOSTART 1           // stream on power-up (the app also sends START)

// ---- Sensirion SFM3300-250-D flow sensor ----
// 0 (this build): the flow sensor is clipped into Sensirion's evaluation cable, which goes
//   straight to the laptop; the app reads it there (Settings > Connection > Flow sensor
//   cable) and puts it into these lines. The board sends the flow as 9999 and says FLOW,CABLE.
// 1: the flow sensor's I2C pads are wired to the Uno's SDA/SCL (needs a connector that
//   reaches its pads: Sensirion sells none), and the board reads it itself.
#define FLOW_ON_ARDUINO 0
// The rest of this section is used only with FLOW_ON_ARDUINO 1 (I2C, 5 V, no shifter).
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

// ---- GSS SprintIR-R-20 CO2 sensor (UART 38400 8N1 on Serial1 = D0/D1, via the BSS138) ----
// Datasheet (rev 4.12): pin 1 Rx_In, 2 Tx_Out, 3 GND, 4 VDD (3.25-5.5 V); 38,400 baud,
// 8 data bits, no parity, 1 stop bit; 50 readings a second. Its inputs are rated 5 V
// absolute maximum and its output high is 2.6-3.0 V, so the level shifter sits between it
// and the 5 V Uno in both directions.
#define CO2_BAUD 38400
// One value per reading: the raw value 'z' (follows a breath fastest) or, with
// CO2_USE_FILTERED 1, the sensor's own smoothed value 'Z'.
#define CO2_USE_FILTERED 0
#define CO2_DEFAULT_MULTIPLIER 10  // the 0-20 % part reports in units of 10 ppm
#define CO2_TIMEOUT_MS 500    // no reading for this long = the sensor is not answering
// While the sensor is not answering, the board sends it its set-up commands (streaming
// mode, the output field, the multiplier query) again this often. The wall adapter is
// plugged in after the USB cable, so at the board's start the sensor is often still
// unpowered and misses them; a dip in 5V_EXT when the pump starts can also restart it.
// The first resend after an outage is reported as E,CO2_RESTART. 0 = send them only at start-up.
#define CO2_REINIT_MS 2000

// ---- what a sensor that is not answering sends (the app shows --- and alarms) ----
#define NOT_ANSWERING 9999.0f

// ---- controls and indicators ----
#define BUTTON_PIN 2          // optional stop / marker button to GND (INPUT_PULLUP); unwired = never pressed
#define REED_PIN 3            // Adafruit 375 magnetic contact switch to GND (INPUT_PULLUP); -1 = none
#define REED_CLOSED_IS_BAG 1  // the magnet on the valve handle closes the switch at the bag position
// The board's own LED (D13). For a panel LED on the enclosure set D7 and wire the LED with
// 470 ohm to 1 kohm in series: the Uno R4 allows at most 8 mA per pin (330 ohm would be ~9 mA).
#define ALARM_LED_PIN LED_BUILTIN
// Optional ACTIVE piezo buzzer (it makes its own tone; the pin only switches it). Straight to
// the pin only if it draws under 8 mA; otherwise through the spare Q2 (gate via 1 kohm from D8,
// 100 kohm gate to GND). Power it from the Uno's 5V so it still sounds without 5V_EXT. -1 = none.
#define BUZZER_PIN 8
// The SFM3300 has an optional heater (51 ohm, 0.5 W max) against condensation from humid breath.
// To use it: heater pads -> Q2 IRLZ44N drain, other pad -> 5V_EXT, Q2 gate on D9, and set 9 here.
#define HEATER_PIN -1
#define PUMP_PIN 10           // gate of Q1 (IRLZ44N): the sample pump from 5V_EXT
#define SERVO_PIN -1          // D6 for a valve servo (powered from 5V_EXT); -1 = manual valve
#define SERVO_AIR_DEG 0
#define SERVO_BAG_DEG 90

// Sample pump PWM duty, 0-255. The ZR370-02PM (rated 4.5 V, 4.0-5.0 V, about 0.5 A) moves
// about 2.5 L/min with nothing on its inlet, far more than a sidestream sample needs;
// ~0.2 L/min (the SprintIR-R datasheet's test flow) through the filter and the sensor is the aim. Measure it (docs/wiring.md, "Setting the sample flow") and set
// it here or live with the host command "PUMP <0-255>". The pump runs while the board streams.
#define PUMP_DUTY 140
// The pump's PWM frequency in Hz. 490 is the Uno R4's analogWrite frequency and suits R1 (the
// gate resistor) at 220 ohm or at 1 kohm, which keeps the gate's charging current under the
// pin's 8 mA: through 1 kohm Q1's gate charges in roughly 10 us, about 1 % of each 2 ms period. Higher frequencies (e.g. 20000, above hearing) quieten the motor's whine, but
// only with R1 at 220 ohm or less or a gate driver: through 1 kohm Q1 would spend a quarter of
// each 50 us period half on, and heat. A different frequency changes the flow a duty gives, so
// measure the sample flow again after changing it. Off is always the pin held LOW, not 0 % PWM.
#define PUMP_PWM_HZ 490
#define HEATER_DUTY 128      // ~0.25 W at 5 V: half the heater's 0.5 W limit

// ---- host watchdog ----
// The monitor app sends "HB" once a second. If the heartbeats stop for this long, the
// board flashes its LED (and sounds a buzzer if one is fitted), and sends the valve (if a
// servo drives it) back to room air, because nothing on the computer is checking the CO2.
#define WATCHDOG_TIMEOUT_MS 3000

// ---- the microcontroller's own watchdog ----
// If the firmware itself stops running (a stuck I2C bus, a USB write that never finishes),
// nothing above runs either: no buzzer, the pump left at its last duty. The RA4M1's watchdog
// timer restarts the board when the main loop has not run for this long. After such a restart
// the board says RST,WDT in its info line and arms the host watchdog at once, so the buzzer
// sounds if the app does not send heartbeats within WATCHDOG_TIMEOUT_MS. The chip rounds up to
// the next timeout it offers (2000 gives 2796 ms, sent as WDT in the info line); at most about
// 5590 ms. 0 = off.
#define MCU_WATCHDOG_MS 2000
