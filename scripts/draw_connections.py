"""Draws docs/images/connections-r4.svg: every connection of the Uno R4 board, wire by wire.

    python scripts/draw_connections.py

The Arduino's two headers are drawn in their real order (top view, USB-C on the left), the
breadboard's three power rails run across the middle, and every wire has a number (W1...)
that matches the wire list in docs/wiring.md. Cables are C1-C2, the power plug P1, tubes T1-T6.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "connections-r4.svg"

W, H = 1720, 1230
COL = {
    "5v": "#d62728",  # +5V from the Arduino (isolated USB)
    "ext": "#ff7f0e",  # +5V_EXT from the wall adapter
    "3v3": "#9467bd",
    "gnd": "#111111",
    "sda": "#1f77b4",
    "scl": "#bcbd22",
    "tx": "#2ca02c",
    "rx": "#17becf",
    "pwm": "#e377c2",
    "sw": "#7f7f7f",
    "drain": "#8c564b",
}

out: list[str] = []


def add(s: str) -> None:
    out.append(s)


def text(x, y, s, size=12, anchor="start", weight="normal", fill="#111", rotate=None, italic=False):
    tr = f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ""
    st = ' font-style="italic"' if italic else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{tr}{st}>{s}</text>')


def box(x, y, w, h, fill="#fff", stroke="#333", dash=False, r=6, sw=1.6):
    d = ' stroke-dasharray="6 4"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def wire(points, color, width=2.6, dash=False):
    d = " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (x, y) in enumerate(points))
    da = ' stroke-dasharray="7 5"' if dash else ""
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{da}/>')


def dot(x, y, color="#111"):
    add(f'<circle cx="{x}" cy="{y}" r="4.2" fill="{color}"/>')


def tag(x, y, n, color):
    """A wire's number, on the wire."""
    w = 30 if n >= 10 else 24
    add(f'<rect x="{x - w / 2}" y="{y - 9}" width="{w}" height="18" rx="9" fill="#fff" stroke="{color}" stroke-width="2"/>')
    text(x, y + 4.5, f"W{n}", 11, "middle", "bold", color if color not in ("#bcbd22",) else "#6b6c00")


def pin(x, y, label=None, side="below", size=10):
    add(f'<rect x="{x - 5}" y="{y - 5}" width="10" height="10" fill="#222" stroke="#000"/>')
    if label:
        if side == "below":
            text(x + 4, y + 12, label, size, "start", "bold", "#00585e", rotate=90)
        else:
            text(x + 4, y - 12, label, size, "end", "bold", "#00585e", rotate=90)


def resistor(x1, y, x2, color, label):
    """A resistor drawn on a horizontal stretch of wire."""
    mid = (x1 + x2) / 2
    zig = " ".join(f"l4 {(-7 if i % 2 == 0 else 7)}" for i in range(6))
    add(f'<path d="M{x1} {y} H{mid - 12} {zig} l0 0 H{x2}" fill="none" stroke="{color}" stroke-width="2.4"/>')
    add(f'<path d="M{mid - 12} {y} l2 -7 l4 14 l4 -14 l4 14 l4 -14 l4 14 l2 -7" fill="none" stroke="{color}" stroke-width="2.4"/>')
    text(mid, y - 12, label, 11, "middle", "bold", "#444")


# ------------------------------------------------------------------ page
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">')
add("<title>CO2-Response: every connection of the Uno R4 board</title>")
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
text(24, 34, "CO2-Response · every connection, pin by pin (Uno R4 board, parts as ordered)", 20, weight="bold")
text(
    24,
    56,
    "W = wire (numbers match the wire list in docs/wiring.md) · C = USB cable · P = power plug · T = tube or fitting. "
    "Dashed = optional or not bought yet. A dot joins a wire to a rail; wires that only cross are not joined.",
    12,
    fill="#444",
)

# ------------------------------------------------------------------ the Arduino's headers (positions first: everything lines up with them)
AX, AY, AW, AH = 330, 410, 540, 300
TOP_Y = AY + 14
BOT_Y = AY + AH - 14
top = ["SCL", "SDA", "AREF", "GND", "D13", "D12", "D11", "D10", "D9", "D8", None, "D7", "D6", "D5", "D4", "D3", "D2", "D1/TX", "D0/RX"]
TX: dict[str, float] = {}
x = 420
for name in top:
    if name is None:
        x += 12
        continue
    TX[name] = x
    x += 22
bot = ["BOOT", "IOREF", "RESET", "3V3", "5V", "GND ", "GND", "VIN", None, "A0", "A1", "A2", "A3", "A4", "A5"]
BX: dict[str, float] = {}
x = 452
for name in bot:
    if name is None:
        x += 12
        continue
    BX[name] = x
    x += 22

# ------------------------------------------------------------------ the breadboard's rails
RAIL = {"ext": 300, "5v": 316, "gnd": 332}
X0, X1 = 330, 1700
box(320, 288, X1 - 312, 56, fill="#f6f2e8", stroke="#c8bfa6", r=4, sw=1.2)
for k, y in RAIL.items():
    wire([(X0, y), (X1, y)], COL[k], 3.4)
text(316, RAIL["ext"] + 4, "+5V_EXT", 11, "end", "bold", COL["ext"])
text(316, RAIL["5v"] + 4, "+5V", 11, "end", "bold", COL["5v"])
text(316, RAIL["gnd"] + 4, "GND", 11, "end", "bold", COL["gnd"])
text(1196, 358, "BREADBOARD POWER RAILS:", 11, weight="bold", fill="#7a6a3a")
text(1368, 358, "+5V_EXT = wall adapter · +5V = Arduino 5V", 10, fill="#555")
text(1196, 374, "(isolated USB) · GND = every ground. Never join the two +5 V rails.", 10, weight="bold", fill="#c0392b")

# ------------------------------------------------------------------ the Arduino
box(AX, AY, AW, AH, fill="#e8f4f6", stroke="#00878f", r=10, sw=2.2)
for name, x in TX.items():
    pin(x, TOP_Y, name, "below")
for name, x in BX.items():
    pin(x, BOT_Y, name.strip(), "above")
text(AX + 270, AY + 160, "Arduino UNO R4 Minima", 18, "middle", "bold")
text(AX + 270, AY + 180, "top view · 5 V logic · max 8 mA per pin", 12, "middle", fill="#444")
text(AX + 270, AY + 198, "built-in LED on D13 = the alarm light (nothing to wire)", 11, "middle", fill="#444")
text(AX + 270, AY + 216, "D2, D8, D9: optional (box on the right)", 11, "middle", fill="#888")
box(AX - 14, AY + 30, 30, 34, fill="#bbb", stroke="#555", r=3)
text(AX + 22, AY + 52, "USB-C", 11, weight="bold", fill="#00585e")
text(BX["BOOT"] - 10, BOT_Y + 26, "power header", 10, fill="#555")
text(BX["A0"] - 4, BOT_Y + 26, "analog header (A4/A5 = SDA/SCL again)", 10, fill="#555")

# ------------------------------------------------------------------ computer and USB isolator
box(20, 250, 200, 70, fill="#fff")
text(120, 276, "Laptop", 15, "middle", "bold")
text(120, 295, "USB-A port (or USB-C)", 12, "middle", fill="#444")
text(120, 311, "runs co2response", 11, "middle", fill="#444")
wire([(120, 320), (120, 400)], "#555", 7)
text(130, 352, "C1", 12, weight="bold")
text(130, 366, "USB-A → Micro-B", 11, weight="bold")
text(130, 380, "data cable", 10, fill="#444")
box(20, 400, 200, 140, fill="#fff")
text(120, 420, "USB isolator", 13, "middle", "bold")
text(120, 435, "Adafruit 2107 (ADuM4160)", 10, "middle", fill="#444")
add('<line x1="120" y1="444" x2="120" y2="500" stroke="#c0392b" stroke-width="2" stroke-dasharray="4 3"/>')
text(68, 458, "Micro-B", 11, "middle", "bold", "#00585e")
text(68, 472, "computer side", 9, "middle", fill="#444")
text(172, 458, "USB-A socket", 11, "middle", "bold", "#00585e")
text(172, 472, "device side", 9, "middle", fill="#444")
text(120, 514, "isolation barrier", 10, "middle", fill="#c0392b")
text(120, 530, "≤100 mA: Uno + SFM3300 only", 10, "middle", fill="#444")
wire([(220, AY + 47), (AX - 14, AY + 47)], "#555", 7)
text(228, AY + 22, "C2", 12, weight="bold")
text(228, AY + 36, "USB-A → USB-C", 10, weight="bold")
text(228, AY + 72, "data cable", 10, fill="#444")
text(20, 560, "Check the board's markings for the", 10, fill="#666")
text(20, 573, "computer side before plugging in.", 10, fill="#666")
text(20, 586, "Use data cables, not charge-only ones.", 10, fill="#666")

# ------------------------------------------------------------------ SFM3300 (top left)
SFX = 360
box(SFX, 96, 250, 92, fill="#fff6e6", stroke="#b36b00")
text(SFX + 125, 118, "Sensirion SFM3300-250-D", 13, "middle", "bold")
text(SFX + 125, 134, "I2C 0x40 · VDD 5 V ±5% · <50 mW", 11, "middle", fill="#444")
text(SFX + 125, 149, "4-wire lead: pin order from Sensirion's", 10, "middle", fill="#444")
text(SFX + 125, 162, "connector note or your cable's label", 10, "middle", fill="#444")
S_Y = 188
SFM = {"SCL": TX["SCL"], "SDA": TX["SDA"], "GND": TX["GND"], "VDD": 560}
for k, x in SFM.items():
    pin(x, S_Y)
    text(x, S_Y - 9, k, 9, "middle", "bold", "#00585e")
wire([(SFM["SCL"], S_Y), (TX["SCL"], TOP_Y)], COL["scl"])
tag(SFM["SCL"], 236, 4, COL["scl"])
wire([(SFM["SDA"], S_Y), (TX["SDA"], TOP_Y)], COL["sda"])
tag(SFM["SDA"], 376, 5, COL["sda"])
wire([(SFM["GND"], S_Y), (TX["GND"], TOP_Y)], COL["gnd"])
tag(SFM["GND"], 236, 6, COL["gnd"])
wire([(SFM["VDD"], S_Y), (SFM["VDD"], RAIL["5v"])], COL["5v"])
dot(SFM["VDD"], RAIL["5v"], COL["5v"])
tag(SFM["VDD"], 236, 7, COL["5v"])
text(SFX, 84, "optional 4.7 kΩ pull-ups R3, R4: SDA → +5V, SCL → +5V", 10, fill="#666")

# ------------------------------------------------------------------ SprintIR-R and the level shifter (top middle)
A1, A2, LG, LV = TX["D1/TX"], TX["D0/RX"], TX["D0/RX"] + 22, TX["D0/RX"] + 66
SP_X0, SP_Y0, SP_Y1 = 660, 80, 150
box(SP_X0, SP_Y0, 340, SP_Y1 - SP_Y0, fill="#fff6e6", stroke="#b36b00")
text(SP_X0 + 10, SP_Y0 + 18, "GSS SprintIR-R 20 % (CO2meter)", 13, weight="bold")
text(SP_X0 + 10, SP_Y0 + 33, "UART 38400 8N1 · 50 readings/s · VDD 3.25-5.5 V", 10, fill="#444")
text(SP_X0 + 10, SP_Y0 + 46, "4-pin header (datasheet): 1 Rx_In, 2 Tx_Out, 3 GND, 4 VDD", 10, fill="#444")
sp = {"1": A1, "2": A2, "3": LG, "4": LG + 22}
for k, x in sp.items():
    pin(x, SP_Y1)
    text(x, SP_Y1 - 9, k, 10, "middle", "bold", "#00585e")
LS_Y0, LS_Y1 = 200, 254
box(A1 - 22, LS_Y0, (LV + 22) - (A1 - 22), LS_Y1 - LS_Y0, fill="#fff")
for k, x in (("A1", A1), ("A2", A2), ("GND", LG), ("LV", LV)):
    pin(x, LS_Y0)
    text(x, LS_Y0 + 18, k, 9, "middle", "bold", "#00585e")
for k, x in (("B1", A1), ("B2", A2), ("GND", LG), ("HV", LV)):
    pin(x, LS_Y1)
    text(x, LS_Y1 - 9, k, 9, "middle", "bold", "#00585e")
text(A1 - 30, LS_Y0 + 16, "BSS138 level shifter", 12, "end", "bold")
text(A1 - 30, LS_Y0 + 31, "(Adafruit 757)", 10, "end", fill="#444")
text(A1 - 30, LS_Y0 + 45, "top: 3 V side · bottom: 5 V side", 10, "end", fill="#444")
wire([(A1, LS_Y0), (A1, SP_Y1)], COL["tx"])
wire([(A2, LS_Y0), (A2, SP_Y1)], COL["rx"])
wire([(LG, LS_Y0), (LG, SP_Y1)], COL["gnd"])
tag(A1 - 18, 176, 12, COL["tx"])
tag(A2 + 2, 176, 13, COL["rx"])
tag(LG + 22, 176, 14, COL["gnd"])
VX = 1010
wire([(sp["4"], SP_Y1), (sp["4"], 166), (VX, 166), (VX, RAIL["ext"])], COL["ext"])
dot(VX, RAIL["ext"], COL["ext"])
tag(VX, 236, 15, COL["ext"])
wire([(A1, LS_Y1), (TX["D1/TX"], TOP_Y)], COL["tx"])
wire([(A2, LS_Y1), (TX["D0/RX"], TOP_Y)], COL["rx"])
tag(A1 - 18, 272, 10, COL["tx"])
tag(A2 + 2, 372, 11, COL["rx"])
wire([(LG, LS_Y1), (LG, RAIL["gnd"])], COL["gnd"])
dot(LG, RAIL["gnd"])
tag(LG + 6, 276, 9, COL["gnd"])
wire([(LV, LS_Y1), (LV, RAIL["5v"])], COL["5v"])
dot(LV, RAIL["5v"], COL["5v"])
tag(LV, 276, 8, COL["5v"])

# ------------------------------------------------------------------ Arduino power header to the rails, 3V3 to LV
x5, xg, x3 = BX["5V"], BX["GND"], BX["3V3"]
wire([(x5, BOT_Y), (x5, 750), (940, 750), (940, RAIL["5v"])], COL["5v"])
dot(940, RAIL["5v"], COL["5v"])
tag(760, 750, 1, COL["5v"])
wire([(xg, BOT_Y), (xg, 736), (925, 736), (925, RAIL["gnd"])], COL["gnd"])
dot(925, RAIL["gnd"])
tag(820, 736, 2, COL["gnd"])
wire([(x3, BOT_Y), (x3, 764), (980, 764), (980, 186), (LV, 186), (LV, LS_Y0)], COL["3v3"])
tag(700, 764, 3, COL["3v3"])
text(LV + 8, 182, "3V3 → LV", 10, weight="bold", fill=COL["3v3"])

# ------------------------------------------------------------------ pump driver (top right)
QX, QY = 1060, 200
box(QX, QY, 130, 56, fill="#fff")
text(QX + 65, QY + 21, "Q1 IRLZ44N", 12, "middle", "bold")
text(QX + 65, QY + 35, "front view, legs down", 9, "middle", fill="#444")
gx, dxl, sx = QX + 30, QX + 65, QX + 100
for k, x in (("G", gx), ("D", dxl), ("S", sx)):
    pin(x, QY + 56)
    text(x, QY + 50, k, 9, "middle", "bold", "#00585e")
box(QX + 45, QY - 12, 40, 12, fill="#bbb", stroke="#555", r=2)
text(QX + 92, QY - 3, "tab = D", 9, fill="#444")
# W16 D10 -> R1 220 -> gate
GY_ = 392
wire([(TX["D10"], TOP_Y), (TX["D10"], GY_), (gx - 70, GY_)], COL["pwm"])
resistor(gx - 70, GY_, gx - 10, COL["pwm"], "R1 220 Ω")
wire([(gx - 10, GY_), (gx, GY_), (gx, QY + 56)], COL["pwm"])
tag(900, GY_, 16, COL["pwm"])
# R2 100k: gate -> GND rail
wire([(gx, 272), (gx - 26, 272)], COL["pwm"], 2)
add(f'<rect x="{gx - 33}" y="272" width="14" height="10" fill="#fff" stroke="{COL["pwm"]}" stroke-width="2"/>')
wire([(gx - 26, 282), (gx - 26, RAIL["gnd"])], COL["pwm"], 2)
dot(gx - 26, RAIL["gnd"])
text(gx - 36, 270, "R2 100 kΩ", 9, "end", "bold", "#444")
dot(gx, 272, COL["pwm"])
# W17 source -> GND
wire([(sx, QY + 56), (sx, RAIL["gnd"])], COL["gnd"])
dot(sx, RAIL["gnd"])
tag(sx + 18, 278, 17, COL["gnd"])
# pump
PX0, PY0 = 1230, 96
box(PX0, PY0, 190, 60, fill="#fff")
text(PX0 + 95, PY0 + 20, "Sample pump ZR370-02PM", 12, "middle", "bold")
text(PX0 + 95, PY0 + 35, "4.5 V (4.0-5.0 V) · ~0.5 A", 10, "middle", fill="#444")
text(PX0 + 95, PY0 + 49, "two solder contacts", 10, "middle", fill="#444")
pm, pp = PX0 + 30, PX0 + 160
pin(pm, PY0 + 60)
pin(pp, PY0 + 60)
text(pm - 10, PY0 + 74, "−", 14, "middle", "bold")
text(pp + 10, PY0 + 74, "+", 14, "middle", "bold")
wire([(dxl, QY - 12), (dxl, 176), (pm, 176), (pm, PY0 + 60)], COL["drain"])
tag(1180, 176, 18, COL["drain"])
DY = 214
wire([(pm, 176), (pm, DY), (pm + 50, DY)], "#333", 2)
add(f'<path d="M{pm + 50} {DY - 8} L{pm + 50} {DY + 8} L{pm + 70} {DY} Z" fill="#333"/>')
add(f'<line x1="{pm + 72}" y1="{DY - 8}" x2="{pm + 72}" y2="{DY + 8}" stroke="#333" stroke-width="3"/>')
wire([(pm + 72, DY), (pp, DY)], "#333", 2)
dot(pm, 176, COL["drain"])
dot(pp, DY, "#333")
text(pm + 62, DY + 22, "D1 1N5819: band to +", 10, "middle", "bold", "#444")
wire([(pp, PY0 + 60), (pp, RAIL["ext"])], COL["ext"])
dot(pp, RAIL["ext"], COL["ext"])
tag(pp + 20, 262, 19, COL["ext"])

# ------------------------------------------------------------------ power input (top right)
JX0 = 1470
box(JX0, 70, 220, 46, fill="#fff0ec", stroke="#c0392b")
text(JX0 + 110, 90, "5 V 2 A wall adapter", 12, "middle", "bold")
text(JX0 + 110, 106, "SparkFun TOL-15312 · into mains", 10, "middle", fill="#444")
wire([(JX0 + 110, 116), (JX0 + 110, 156)], "#555", 6)
text(JX0 + 102, 134, "P1 · 5.5×2.1 mm plug", 10, "end", "bold")
text(JX0 + 102, 147, "centre positive", 10, "end", fill="#444")
box(JX0, 156, 220, 70, fill="#fff")
text(JX0 + 110, 176, "2.1 mm barrel jack (Adafruit 373)", 11, "middle", "bold")
text(JX0 + 110, 191, "plugged into the breadboard", 10, "middle", fill="#444")
je, jm, js = JX0 + 40, JX0 + 110, JX0 + 180
for x in (je, jm, js):
    pin(x, 226)
text(je, 214, "end pin +", 9, "middle", "bold", "#00585e")
text(jm, 214, "middle pin −", 9, "middle", "bold", "#00585e")
text(js, 214, "side pin: none", 9, "middle", "bold", "#999")
wire([(je, 226), (je, RAIL["ext"])], COL["ext"])
dot(je, RAIL["ext"], COL["ext"])
tag(je, 264, 22, COL["ext"])
wire([(jm, 226), (jm, RAIL["gnd"])], COL["gnd"])
dot(jm, RAIL["gnd"])
tag(jm, 264, 23, COL["gnd"])

# ------------------------------------------------------------------ reed switch (right of the Arduino)
RX0, RY0 = 1330, 400
box(RX0, RY0, 230, 52, fill="#fff")
text(RX0 + 115, RY0 + 20, "Reed switch (Adafruit 375)", 12, "middle", "bold")
text(RX0 + 115, RY0 + 35, "normally open · closes < 13 mm from the magnet", 9, "middle", fill="#444")
text(RX0 + 115, RY0 + 47, "two leads, either way round", 9, "middle", fill="#444")
SWY = 406
wire([(TX["D3"], TOP_Y), (TX["D3"], SWY - 2), (TX["D3"], 404)], COL["sw"])
wire([(TX["D3"], 404), (RX0 - 30, 404), (RX0 - 30, RY0 + 26), (RX0, RY0 + 26)], COL["sw"])
tag(1250, 404, 20, COL["sw"])
wire([(RX0 + 230, RY0 + 26), (RX0 + 260, RY0 + 26), (RX0 + 260, RAIL["gnd"])], COL["sw"])
dot(RX0 + 260, RAIL["gnd"])
tag(RX0 + 260, 372, 21, COL["sw"])

# ------------------------------------------------------------------ optional parts (dashed)
OX0, OY0 = 1000, 470
box(OX0, OY0, 380, 130, dash=True, fill="#fafafa", stroke="#999")
text(OX0 + 12, OY0 + 20, "Optional (from the electronics kit)", 12, weight="bold")
for i, line in enumerate(
    (
        "W24  D8 → active buzzer +; buzzer − → GND rail",
        "W25  D2 → push button → GND rail (stop / marker)",
        "W26  D9 → Q2 gate (220 Ω, 100 kΩ to GND); Q2 drain → one",
        "         SFM3300 heater pad, other pad → +5V_EXT (51 Ω heater)",
        "R3, R4  4.7 kΩ: SDA → +5V rail, SCL → +5V rail",
    )
):
    text(OX0 + 12, OY0 + 42 + i * 17, line, 10, fill="#444")

# ------------------------------------------------------------------ gas path and tubing
GY = 820
add(f'<line x1="20" y1="{GY - 30}" x2="{W - 20}" y2="{GY - 30}" stroke="#ccc"/>')
text(24, GY - 4, "Gas path and tubing", 15, weight="bold")
text(190, GY - 4, "(main circuit in grey; the CO2 sample line in green)", 11, fill="#555")
my = GY + 52
main = [
    (20, 170, "Subject +", "mouthpiece (from mentors)", None, True),
    (220, 170, "Sample port", "luer fitting between mouth-", "piece and sensor (requested)", True),
    (420, 330, "SFM3300-250-D", "22 mm MALE end → subject  |  22 mm", "FEMALE end → circuit (+ = inspiration)", False),
    (780, 190, "½\" Sch 40 PVC pipe", "21.3 mm OD into the sensor's", "22 mm socket: tape if loose", False),
    (1000, 200, "¾×½\" reducer bushing", "+ ¾\" S×FPT adapter;", "PTFE tape on the threads", False),
    (1230, 190, "3-way valve", "¾\" PVC (requested); magnet", "on its handle for the reed switch", True),
]
for x, w, a, b, c, dash in main:
    box(x, my - 30, w, 64, fill="#f4f4f4" if dash else "#fff", stroke="#888" if dash else "#333", dash=dash)
    text(x + 8, my - 11, a, 11, weight="bold")
    text(x + 8, my + 5, b, 10, fill="#444")
    if c:
        text(x + 8, my + 19, c, 10, fill="#444")
for i in range(len(main) - 1):
    x1 = main[i][0] + main[i][1]
    x2 = main[i + 1][0]
    wire([(x1, my), (x2, my)], "#888", 8)
box(1480, my - 64, 170, 34, dash=True, fill="#f4f4f4", stroke="#888")
text(1492, my - 42, "Rebreathing bag", 11, weight="bold")
box(1480, my + 30, 170, 34, dash=True, fill="#f4f4f4", stroke="#888")
text(1492, my + 52, "Room air", 11, weight="bold")
wire([(1420, my), (1450, my), (1450, my - 47), (1480, my - 47)], "#888", 8)
wire([(1450, my), (1450, my + 47), (1480, my + 47)], "#888", 8)

sy = GY + 190
samp = [
    (220, "T1 · 0.2 µm PTFE filter", "13 mm syringe filter: female", "luer in, male luer out"),
    (470, "T2 · P-859 adapter", "female luer lock → ⅛\" barb", "(onto the filter's male luer)"),
    (720, "T3 · silicone tube", "3 mm ID / 5 mm OD", "(Adafruit 4661)"),
    (970, "SprintIR-R flow adaptor", "tube on one barb, out the other", "(check the barb fits 3 mm ID)"),
    (1220, "T4 · tube → pump SIDE port", "the inlet: 2 mm ID, 4.3 mm OD", "(the pump pulls the sample)"),
    (1470, "T5 · pump END port → tube", "the outlet: 1 mm ID, 4.3 mm OD", "→ back into the bag side"),
]
for x, a, b, c in samp:
    box(x, sy - 32, 220, 66, fill="#eef7f2", stroke="#0a7d55")
    text(x + 8, sy - 12, a, 11, weight="bold")
    text(x + 8, sy + 4, b, 10, fill="#444")
    text(x + 8, sy + 18, c, 10, fill="#444")


def garrow(points):
    wire(points, "#0a7d55", 3)
    (xa, ya), (xb, yb) = points[-2], points[-1]
    if xa == xb:
        s_ = 1 if yb > ya else -1
        add(f'<path d="M{xb - 6} {yb - 10 * s_} L{xb} {yb} L{xb + 6} {yb - 10 * s_} Z" fill="#0a7d55"/>')
    else:
        s_ = 1 if xb > xa else -1
        add(f'<path d="M{xb - 10 * s_} {yb - 6} L{xb} {yb} L{xb - 10 * s_} {yb + 6} Z" fill="#0a7d55"/>')


for i in range(len(samp) - 1):
    garrow([(samp[i][0] + 220, sy), (samp[i + 1][0], sy)])
garrow([(305, my + 34), (305, sy - 32)])
garrow([(1580, sy - 32), (1580, sy - 60), (1700, sy - 60), (1700, my - 47), (1650, my - 47)])
text(1572, sy - 42, "exhaust back into the bag side", 10, "end", fill="#0a7d55")

# ------------------------------------------------------------------ legend
LY = H - 140
box(20, LY, W - 40, 124, fill="#fafafa", stroke="#ccc")
text(34, LY + 22, "Wire colours (suggested; keep red, orange and black for power)", 12, weight="bold")
items = [
    ("5v", "+5V from the Arduino (W1, W7, W8)"),
    ("ext", "+5V_EXT, wall adapter (W15, W19, W22)"),
    ("3v3", "3V3 reference (W3)"),
    ("gnd", "ground (W2, W6, W9, W14, W17, W23)"),
    ("sda", "I2C SDA (W5)"),
    ("scl", "I2C SCL (W4)"),
    ("tx", "Arduino TX → sensor Rx_In (W10, W12)"),
    ("rx", "sensor Tx_Out → Arduino RX (W11, W13)"),
    ("pwm", "pump gate, PWM (W16)"),
    ("drain", "Q1 drain → pump − (W18)"),
    ("sw", "reed switch leads (W20, W21)"),
]
for i, (k, label) in enumerate(items):
    cx = 34 + (i % 4) * 420
    cy = LY + 46 + (i // 4) * 24
    wire([(cx, cy - 4), (cx + 34, cy - 4)], COL[k], 3.4)
    text(cx + 42, cy, label, 11)
text(
    34,
    LY + 114,
    "Jumpers: male-to-male from the Arduino's headers to the breadboard; female-to-male onto a sensor's header pins; solder the pump's contacts and the SFM3300's lead. "
    "The Arduino's GND pins are joined inside the board.",
    10,
    fill="#444",
)
add("</svg>")

OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {OUT}")
