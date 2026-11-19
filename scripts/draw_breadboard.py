"""Draws docs/images/breadboard-r4.svg: the whole Uno R4 build on a full-size breadboard, hole by hole.

    python scripts/draw_breadboard.py

A full-size (830-point) breadboard: columns 1-63, rows a-e above the centre channel and f-j
below it, and a pair of power rails along each long edge. Every part sits in named holes and
every wire runs from one named hole (or pin) to another, so the picture can be copied onto a
real board. Wire numbers (W1...) match the wire list in docs/wiring.md and the README.

Rails, as used here (follow the red + and blue - lines printed on your board):
    top    +  (outer)  = +5V_EXT   wall adapter, for the pump and the SprintIR-R
    top    -  (inner)  = GND
    bottom -  (inner)  = GND       joined to the top GND rail by W27
    bottom +  (outer)  = +5V       Arduino 5V (isolated USB), for the SFM3300 and the shifter
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "breadboard-r4.svg"

W, H = 1760, 1640

# ------------------------------------------------------------------ colours (suggested jumper colours)
COL = {
    "ext": "#f28e1c",  # +5V_EXT: orange
    "5v": "#d62728",  # +5V (Arduino): red
    "gnd": "#1b1b1b",  # ground: black
    "3v3": "#8e5cc4",  # 3V3: purple
    "sda": "#1f6fd1",  # SDA: blue
    "scl": "#d4c21a",  # SCL: yellow
    "tx": "#2ca02c",  # TX path: green
    "rx": "#17becf",  # RX path: cyan
    "pwm": "#e377c2",  # pump gate: pink
    "drain": "#8c564b",  # pump -: brown
    "sw": "#8a8a8a",  # reed switch: grey
    "opt": "#ffffff",  # optional parts: white
}
COLOUR_NAME = {
    "ext": "orange",
    "5v": "red",
    "gnd": "black",
    "3v3": "purple",
    "sda": "blue",
    "scl": "yellow",
    "tx": "green",
    "rx": "cyan",
    "pwm": "pink",
    "drain": "brown",
    "sw": "grey",
    "opt": "white",
}

out: list[str] = []


def add(s: str) -> None:
    out.append(s)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12, anchor="start", weight="normal", fill="#111", rotate=None, italic=False):
    tr = f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ""
    st = ' font-style="italic"' if italic else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{tr}{st}>{esc(s)}</text>')


def rect(x, y, w, h, fill="#fff", stroke="#333", r=6, sw=1.6, dash=False, opacity=None):
    d = ' stroke-dasharray="6 4"' if dash else ""
    o = f' fill-opacity="{opacity}"' if opacity is not None else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}{o}/>')


# ------------------------------------------------------------------ breadboard geometry
P = 20  # 0.1 inch
BX0 = 300  # x of column 1
NCOL = 63


def cx(c: int) -> float:
    return BX0 + (c - 1) * P


ROW_Y = {"a": 380, "b": 400, "c": 420, "d": 440, "e": 460, "f": 500, "g": 520, "h": 540, "i": 560, "j": 580}
RAIL_Y = {"T+": 318, "T-": 338, "B-": 622, "B+": 642}
RAIL_NAME = {"T+": "top + rail", "T-": "top − rail", "B-": "bottom − rail", "B+": "bottom + rail"}
RAIL_COLS = [c for g in range(10) for c in range(3 + 6 * g, 8 + 6 * g)]  # 10 groups of 5: 3-7, 9-13 ... 57-61
BOARD = (262, 290, BX0 + (NCOL - 1) * P + 38 - 262, 380)  # x, y, w, h


def hole(name: str) -> tuple[float, float]:
    """'j42' -> the hole's centre; 'T+:45' -> a rail hole."""
    if ":" in name:
        rail, c = name.split(":")
        assert int(c) in RAIL_COLS, name
        return cx(int(c)), RAIL_Y[rail]
    return cx(int(name[1:])), ROW_Y[name[0]]


def hole_label(name: str) -> str:
    if name[:2] in RAIL_NAME and name[2:3] == ":":
        rail, c = name.split(":")
        return f"{RAIL_NAME[rail]}, col {c}"
    return name


def draw_breadboard() -> None:
    x, y, w, h = BOARD
    add(f'<rect x="{x + 4}" y="{y + 5}" width="{w}" height="{h}" rx="10" fill="#000" fill-opacity="0.12"/>')
    rect(x, y, w, h, fill="#f4f1ea", stroke="#bdb6a3", r=10, sw=1.4)
    # centre channel
    add(f'<rect x="{x + 8}" y="{472}" width="{w - 16}" height="16" rx="3" fill="#e2ddd0"/>')
    # rail stripes
    xl, xr = cx(1) - 8, cx(NCOL) + 8
    for yy, c in ((306, "#d62728"), (350, "#1f6fd1"), (610, "#1f6fd1"), (654, "#d62728")):
        add(f'<line x1="{xl}" y1="{yy}" x2="{xr}" y2="{yy}" stroke="{c}" stroke-width="2"/>')
    for rail, yy in RAIL_Y.items():
        sign = "+" if rail.endswith("+") else "−"
        colr = "#d62728" if sign == "+" else "#1f6fd1"
        text(x + 14, yy + 5, sign, 15, "middle", "bold", colr)
        text(x + w - 14, yy + 5, sign, 15, "middle", "bold", colr)
    # rail holes
    for yy in RAIL_Y.values():
        for c in RAIL_COLS:
            add(f'<rect x="{cx(c) - 3.5}" y="{yy - 3.5}" width="7" height="7" rx="1.2" fill="#3b3b3b"/>')
    # terminal holes
    for r, yy in ROW_Y.items():
        for c in range(1, NCOL + 1):
            add(f'<rect x="{cx(c) - 3.5}" y="{yy - 3.5}" width="7" height="7" rx="1.2" fill="#3b3b3b"/>')
        text(x + 14, yy + 4, r, 11, "middle", "bold", "#7a7360")
        text(x + w - 14, yy + 4, r, 11, "middle", "bold", "#7a7360")
    # column numbers
    for c in range(1, NCOL + 1):
        big = c % 5 == 0 or c == 1
        text(cx(c), 369, str(c), 8.5 if big else 7.5, "middle", "bold", "#5f5845" if big else "#9c947c")
        text(cx(c), 598, str(c), 8.5 if big else 7.5, "middle", "bold", "#5f5845" if big else "#9c947c")


# ------------------------------------------------------------------ wires
WIRES: list[dict] = []


def path_d(pts, r=10) -> str:
    """A polyline with rounded corners."""
    if len(pts) == 2:
        return f"M{pts[0][0]} {pts[0][1]} L{pts[1][0]} {pts[1][1]}"
    d = f"M{pts[0][0]} {pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = max(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1e-6)
        l2 = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1e-6)
        rr = min(r, l1 / 2, l2 / 2)
        ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
        bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
        d += f" L{ax:.1f} {ay:.1f} Q{x1} {y1} {bx:.1f} {by:.1f}"
    d += f" L{pts[-1][0]} {pts[-1][1]}"
    return d


def wire(n, kind, pts, a, b, badge=None, note="", optional=False, label_ends=True):
    """Record a wire; it is drawn later, above the parts. a and b are the two ends, as text."""
    WIRES.append(dict(n=n, kind=kind, pts=pts, a=a, b=b, badge=badge, note=note, optional=optional, label_ends=label_ends))


def draw_wire(w) -> None:
    color = COL[w["kind"]]
    d = path_d(w["pts"])
    dash = ' stroke-dasharray="9 6"' if w["optional"] else ""
    add(f'<path d="{d}" fill="none" stroke="#000" stroke-opacity="0.75" stroke-width="7.5" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="4.6" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
    for x, y in (w["pts"][0], w["pts"][-1]):
        add(f'<circle cx="{x}" cy="{y}" r="5.6" fill="{color}" stroke="#000" stroke-width="1.6"/>')
        add(f'<circle cx="{x}" cy="{y}" r="1.8" fill="#fff"/>')


def draw_badge(w) -> None:
    pts = w["pts"]
    if w["badge"] is not None:
        bx, by = w["badge"]
    else:  # middle of the longest segment
        best = max(range(len(pts) - 1), key=lambda i: abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]))
        bx = (pts[best][0] + pts[best + 1][0]) / 2
        by = (pts[best][1] + pts[best + 1][1]) / 2
    color = COL[w["kind"]]
    txt = f"W{w['n']}"
    bw = 9 + 7.4 * len(txt)
    stroke = "#555" if w["kind"] in ("opt",) else color
    tc = {"scl": "#6b6100", "opt": "#333", "ext": "#b35c00"}.get(w["kind"], color)
    add(f'<rect x="{bx - bw / 2}" y="{by - 9.5}" width="{bw}" height="19" rx="9.5" fill="#fff" stroke="{stroke}" stroke-width="2.2"/>')
    text(bx, by + 4.5, txt, 11.5, "middle", "bold", tc)


def pin(x, y, label=None, where="above", size=9, color="#00585e"):
    add(f'<rect x="{x - 4.5}" y="{y - 4.5}" width="9" height="9" fill="#222" stroke="#000"/>')
    if label:
        if where == "above":
            text(x, y - 9, label, size, "middle", "bold", color)
        elif where == "below":
            text(x, y + 17, label, size, "middle", "bold", color)
        elif where == "up90":
            text(x + 3.5, y - 9, label, size, "start", "bold", color, rotate=-90)
        elif where == "down90":
            text(x - 3.5, y + 9, label, size, "start", "bold", color, rotate=90)


def leg(name, color="#9a9a9a"):
    """A component leg pushed into a hole."""
    x, y = hole(name)
    add(f'<circle cx="{x}" cy="{y}" r="4.6" fill="{color}" stroke="#333" stroke-width="1.4"/>')


def resistor(a, b, label, bands):
    (x1, y1), (x2, y2) = hole(a), hole(b)
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#9a9a9a" stroke-width="2.4"/>')
    mx = (x1 + x2) / 2
    add(f'<rect x="{mx - 22}" y="{y1 - 7}" width="44" height="14" rx="6" fill="#e9d3a6" stroke="#8a7346" stroke-width="1.2"/>')
    for i, bc in enumerate(bands):
        add(f'<rect x="{mx - 15 + i * 8}" y="{y1 - 7}" width="4" height="14" fill="{bc}"/>')
    leg(a)
    leg(b)
    globals()["label"](mx, y1 - 11, label, 9, "middle", "bold", "#5a4a20")


def label(x, y, s, size=9.5, anchor="middle", weight="bold", fill="#333"):
    w = 0.56 * size * len(s) + 6
    x0 = x - w / 2 if anchor == "middle" else (x - 3 if anchor == "start" else x - w + 3)
    add(f'<rect x="{x0}" y="{y - size}" width="{w}" height="{size + 4}" rx="3" fill="#fff" fill-opacity="0.93"/>')
    text(x, y, s, size, anchor, weight, fill)


def hole_tag(name, dx=0, dy=-10, anchor="middle"):
    x, y = hole(name)
    add(f'<rect x="{x + dx - (12 if anchor == "middle" else 0) - (24 if anchor == "end" else 0)}" y="{y + dy - 9}" width="24" height="12" rx="3" fill="#fffbe6" fill-opacity="0.92"/>')
    text(x + dx, y + dy, name, 9.5, anchor, "bold", "#5c4b00")


# ------------------------------------------------------------------ page
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">')
add("<title>CO2-Response: the Uno R4 build on a full breadboard</title>")
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
text(24, 34, "CO2-Response · the whole Uno R4 build on a full-size breadboard, hole by hole", 20, weight="bold")
text(
    24,
    56,
    "Columns 1-63, rows a-e above the centre channel and f-j below it. The five holes of one column on one side of the channel "
    "(e.g. a12-e12) are joined inside the board; the channel separates them from f12-j12.",
    12,
    fill="#444",
)
text(
    24,
    74,
    "Each rail is one long strip. W = wire, numbered as in docs/wiring.md; every wire end is a dot on its hole or pin. "
    "Dashed = optional. Wires that only cross are not joined.",
    12,
    fill="#444",
)

draw_breadboard()

# rail names, at the left
for rail, name, c in (
    ("T+", "+5V_EXT", COL["ext"]),
    ("T-", "GND", "#1b1b1b"),
    ("B-", "GND", "#1b1b1b"),
    ("B+", "+5V", COL["5v"]),
):
    text(BOARD[0] - 22, RAIL_Y[rail] + 4, name, 11, "end", "bold", c)

# never-join warning
text(BOARD[0] + BOARD[2] - 6, 284, "The two + rails carry different supplies: never join them.", 11, "end", "bold", "#c0392b")

# ------------------------------------------------------------------ parts on the breadboard
# Barrel jack J1 (Adafruit 373): end pin c3 (+), middle pin c5 (-), side pin c7 (unused)
rect(cx(1) - 66, 408, cx(7) - cx(1) + 80, 30, fill="#2b2b2b", stroke="#000", r=5, opacity=0.55)
add(f'<circle cx="{cx(1) - 50}" cy="423" r="10" fill="#555" stroke="#111"/>')
add(f'<circle cx="{cx(1) - 50}" cy="423" r="3" fill="#bbb"/>')
for h in ("c3", "c5", "c7"):
    leg(h, "#c8c8c8")
label(cx(4) + 8, 456, "J1 barrel jack", 9.5, fill="#111")
label(cx(3), 476, "end +", 9, fill="#b35c00")
label(cx(5) + 6, 489, "mid −", 9, fill="#111")
label(cx(7) + 12, 476, "side: none", 9, fill="#999")

# Q1 IRLZ44N in f12 (G), f13 (D), f14 (S); body in the channel, printed side facing down
rect(cx(12) - 12, 466, cx(14) - cx(12) + 24, 22, fill="#2b2b2b", stroke="#000", r=3)
add(f'<rect x="{cx(12) - 12}" y="460" width="{cx(14) - cx(12) + 24}" height="7" fill="#b8b8b8" stroke="#555"/>')
for h, s_ in (("f12", "G"), ("f13", "D"), ("f14", "S")):
    leg(h, "#c8c8c8")
    text(hole(h)[0], 482, s_, 9, "middle", "bold", "#fff")
label(cx(15) + 6, 484, "Q1 IRLZ44N: G f12 · D f13 · S f14", 9.5, "start", fill="#111")
# R1 220 ohm h8-h12, R2 100k i12 -> bottom - rail
resistor("h8", "h12", "R1 220 Ω h8-h12", ["#d62728", "#d62728", "#7b3f00", "#c9a227"])
x1, y1 = hole("i12")
x2, y2 = hole("B-:11")
add(f'<path d="M{x1} {y1} C{x1 + 4} {y1 + 30} {x2 + 4} {y2 - 30} {x2} {y2}" fill="none" stroke="#9a9a9a" stroke-width="2.4"/>')
add(f'<rect x="{(x1 + x2) / 2 - 6}" y="{(y1 + y2) / 2 - 15}" width="12" height="30" rx="5" fill="#e9d3a6" stroke="#8a7346" stroke-width="1.2"/>')
for i, bc in enumerate(["#7b3f00", "#1b1b1b", "#f2c200"]):
    add(f'<rect x="{(x1 + x2) / 2 - 6}" y="{(y1 + y2) / 2 - 10 + i * 7}" width="12" height="3.5" fill="{bc}"/>')
leg("i12")
leg("B-:11")
label(cx(12) + 6, 664, "R2 100 kΩ: i12 → bottom − rail", 9, "start", fill="#5a4a20")

# Buzzer (optional): + g20, - g23
add(f'<circle cx="{(cx(20) + cx(23)) / 2}" cy="520" r="24" fill="#2b2b2b" fill-opacity="0.18" stroke="#555" stroke-dasharray="5 3"/>')
leg("g20", "#c8c8c8")
leg("g23", "#c8c8c8")
text(cx(20), 538, "+", 12, "middle", "bold", "#c0392b")
label((cx(20) + cx(23)) / 2 + 60, 500, "buzzer (opt.)", 9, fill="#666")

# Reed switch leads into g27, g29 (switch itself sits on the valve)
leg("g27", "#bdbdbd")
leg("g29", "#bdbdbd")

# Push button (optional), straddling the channel: e33, e35, f33, f35
rect(cx(33) - 10, 452, cx(35) - cx(33) + 20, 56, fill="#ddd", stroke="#666", r=4, dash=True, opacity=0.65)
add(f'<circle cx="{(cx(33) + cx(35)) / 2}" cy="480" r="9" fill="#444" fill-opacity="0.6"/>')
for h in ("e33", "e35", "f33", "f35"):
    leg(h, "#c8c8c8")
text((cx(33) + cx(35)) / 2, 446, "button (opt.)", 9, "middle", "bold", "#666")

# Level shifter U1 (Adafruit 757): A side in d40-d45, B side in g40-g45
SH_A = ["A1", "A2", "LV", "GND", "A3", "A4"]
SH_B = ["B1", "B2", "HV", "GND", "B3", "B4"]
rect(cx(40) - 14, 428, cx(45) - cx(40) + 28, 104, fill="#1d4f91", stroke="#0b2c57", r=4)
text((cx(40) + cx(45)) / 2, 470, "U1 level shifter", 10, "middle", "bold", "#fff")
text((cx(40) + cx(45)) / 2, 483, "BSS138 · Adafruit 757", 8.5, "middle", fill="#d8e6ff")
for i, nm in enumerate(SH_A):
    leg(f"d{40 + i}", "#e3c35a")
    text(cx(40 + i), 457, nm, 7.5, "middle", "bold", "#fff")
for i, nm in enumerate(SH_B):
    leg(f"g{40 + i}", "#e3c35a")
    text(cx(40 + i), 511, nm, 7.5, "middle", "bold", "#fff")
label(cx(45) + 20, 444, "3 V side (A)", 9, "start", fill="#1d4f91")
label(cx(45) + 20, 524, "5 V side (B)", 9, "start", fill="#1d4f91")

# ------------------------------------------------------------------ parts off the breadboard
# Wall adapter + P1, top left, into J1
rect(20, 96, 200, 52, fill="#fff0ec", stroke="#c0392b")
text(120, 117, "5 V 2 A wall adapter", 12, "middle", "bold")
text(120, 134, "SparkFun TOL-15312 · into mains", 10, "middle", fill="#444")
add(f'<path d="M120 148 L120 425 L{cx(1) - 63} 425" fill="none" stroke="#444" stroke-width="7" stroke-linejoin="round"/>')
text(128, 190, "P1", 12, weight="bold")
text(128, 204, "5.5×2.1 mm plug,", 10, fill="#444")
text(128, 217, "centre positive", 10, fill="#444")

# Sample pump, above the board, left
PX, PY = 470, 110
rect(PX, PY, 210, 70, fill="#fff")
text(PX + 105, PY + 20, "Sample pump ZR370-02PM", 12, "middle", "bold")
text(PX + 105, PY + 36, "4.5 V · ~0.5 A · leads soldered on", 10, "middle", fill="#444")
text(PX + 105, PY + 51, "D1 1N5819 soldered across its", 10, "middle", fill="#444")
text(PX + 105, PY + 64, "contacts: band (cathode) to +", 10, "middle", fill="#444")
PM, PP = (PX + 40, PY + 70), (PX + 170, PY + 70)
pin(*PM, "−", "below", 12, "#111")
pin(*PP, "+", "below", 12, "#c0392b")

# SprintIR-R, above the board, over the shifter
SX, SY = 1020, 104
rect(SX, SY, 300, 84, fill="#fff6e6", stroke="#b36b00")
text(SX + 150, SY + 20, "GSS SprintIR-R 20 % CO2 sensor", 12, "middle", "bold")
text(SX + 150, SY + 36, "UART 38400 8N1 · VDD 3.25-5.5 V", 10, "middle", fill="#444")
text(SX + 150, SY + 50, "female ends of the jumpers on its header", 10, "middle", fill="#444")
SP = {1: (SX + 60, SY + 84), 2: (SX + 120, SY + 84), 3: (SX + 180, SY + 84), 4: (SX + 240, SY + 84)}
for k, nm in ((1, "1 Rx_In"), (2, "2 Tx_Out"), (3, "3 GND"), (4, "4 VDD")):
    pin(*SP[k])
    text(SP[k][0], SP[k][1] - 10, nm, 9, "middle", "bold", "#00585e")

# SFM3300, left
FX, FY = 20, 470
rect(FX, FY, 200, 120, fill="#fff6e6", stroke="#b36b00")
text(FX + 100, FY + 20, "Sensirion SFM3300-250-D", 11.5, "middle", "bold")
text(FX + 100, FY + 36, "flow sensor · I2C 0x40 · 5 V", 10, "middle", fill="#444")
text(FX + 100, FY + 52, "4-wire lead: check the pin order", 10, "middle", fill="#444")
text(FX + 100, FY + 65, "on Sensirion's note / your cable", 10, "middle", fill="#444")
SF = {"SCL": (FX + 40, FY + 120), "SDA": (FX + 80, FY + 120), "GND": (FX + 120, FY + 120), "VDD": (FX + 160, FY + 120)}
for k, (x, y) in SF.items():
    pin(x, y)
    text(x, y - 10, k, 9, "middle", "bold", "#00585e")

# Reed switch (on the valve), above the board; its own two leads come down into g27 and g29
RX, RY = 730, 104
rect(RX, RY, 250, 70, fill="#fff")
text(RX + 125, RY + 20, "Reed switch (Adafruit 375)", 12, "middle", "bold")
text(RX + 125, RY + 36, "on the valve; magnet on its handle", 10, "middle", fill="#444")
text(RX + 125, RY + 51, "its own 2 leads → g27, g29 (either way)", 10, "middle", fill="#444")
for i, h in enumerate(("g27", "g29")):
    x, y = hole(h)
    sx = RX + 95 + i * 60
    d = f"M{sx} {RY + 70} L{sx} 250 L{x + 10} 268 L{x + 10} {y - 12} L{x} {y}"
    add(f'<path d="{d}" fill="none" stroke="#555" stroke-width="5" stroke-linejoin="round"/>')
    add(f'<path d="{d}" fill="none" stroke="#e8e8e8" stroke-width="2.6" stroke-linejoin="round"/>')
    add(f'<circle cx="{sx}" cy="{RY + 70}" r="3" fill="#555"/>')
label(cx(28) + 10, 264, "reed leads", 9, fill="#555")

# Arduino
AX, AY, AW, AH = 600, 800, 560, 230
TOP_Y = AY + 16
BOT_Y = AY + AH - 16
rect(AX, AY, AW, AH, fill="#e8f4f6", stroke="#00878f", r=10, sw=2.2)
TX: dict[str, float] = {}
x = 660
for name in ["SCL", "SDA", "AREF", "GND", "D13", "D12", "D11", "D10", "D9", "D8", None, "D7", "D6", "D5", "D4", "D3", "D2", "D1/TX", "D0/RX"]:
    if name is None:
        x += 12
        continue
    TX[name] = x
    x += 20
BXP: dict[str, float] = {}
x = 700
for name in ["BOOT", "IOREF", "RESET", "3V3", "5V", "GND ", "GND", "VIN", None, "A0", "A1", "A2", "A3", "A4", "A5"]:
    if name is None:
        x += 12
        continue
    BXP[name] = x
    x += 20
used = {"SCL", "SDA", "GND", "D10", "D8", "D3", "D2", "D1/TX", "D0/RX", "3V3", "5V", "GND "}
for name, xx in TX.items():
    pin(xx, TOP_Y, name, "down90", 9, "#00585e" if name in used else "#9ab")
for name, xx in BXP.items():
    pin(xx, BOT_Y, name.strip(), "up90", 9, "#00585e" if name in used else "#9ab")
text(AX + 300, AY + 128, "Arduino UNO R4 Minima", 17, "middle", "bold")
text(AX + 300, AY + 146, "top view · USB-C on the left", 11, "middle", fill="#444")
text(AX + 300, AY + 161, "D13 LED = alarm light (nothing to wire)", 10, "middle", fill="#444")
rect(AX - 14, AY + 50, 30, 34, fill="#bbb", stroke="#555", r=3)
text(AX + 22, AY + 72, "USB-C", 10, weight="bold", fill="#00585e")

# Laptop and isolator
rect(20, 790, 180, 66, fill="#fff")
text(110, 815, "Laptop", 14, "middle", "bold")
text(110, 833, "runs co2response", 10, "middle", fill="#444")
add('<path d="M110 856 L110 900" fill="none" stroke="#555" stroke-width="7"/>')
text(120, 884, "C1 USB-A → Micro-B", 10, weight="bold")
rect(20, 900, 300, 90, fill="#fff")
text(170, 922, "USB isolator (Adafruit 2107)", 12, "middle", "bold")
text(170, 940, "Micro-B: computer side · USB-A: device side", 10, "middle", fill="#444")
text(170, 956, "≤100 mA: the Uno and the SFM3300 only", 10, "middle", fill="#444")
text(170, 975, "nothing on the breadboard goes to it", 10, "middle", fill="#c0392b")
add(f'<path d="M320 945 L470 945 L470 {AY + 67} L{AX - 14} {AY + 67}" fill="none" stroke="#555" stroke-width="7" stroke-linejoin="round"/>')
text(478, 900, "C2 USB-A → USB-C", 10, weight="bold")

# ------------------------------------------------------------------ the wires
T = TOP_Y
B = BOT_Y

# Power
wire(1, "5v", [(BXP["5V"], B), (BXP["5V"], 1054), (1230, 1054), (1230, 652), hole("B+:48")], "Arduino 5V (power header)", "B+:48", badge=(1230, 760))
wire(2, "gnd", [(BXP["GND "], B), (BXP["GND "], 1044), (1210, 1044), (1210, 632), hole("B-:47")], "Arduino GND (power header)", "B-:47", badge=(1210, 800))
wire(3, "3v3", [(BXP["3V3"], B), (BXP["3V3"], 1064), (1290, 1064), (1290, 390), (1132, 390), hole("b42")], "Arduino 3V3 (power header)", "b42", badge=(1290, 900))
wire(22, "ext", [hole("a3"), (cx(3), 362), (cx(4) + 10, 352), (cx(4) + 10, 326), hole("T+:4")], "J1 end pin (c3 column): a3", "T+:4", badge=(cx(6) + 4, 352))
wire(23, "gnd", [hole("a5"), (cx(5), 360), (cx(6), 352), hole("T-:6")], "J1 middle pin (c5 column): a5", "T-:6", badge=(cx(8) + 14, 352))
wire(27, "gnd", [hole("T-:3"), (cx(3) - 12, 344), (BOARD[0] - 10, 344), (BOARD[0] - 10, 616), (cx(3) - 12, 616), hole("B-:3")], "T-:3", "B-:3", badge=(BOARD[0] - 10, 560))

# Flow sensor: straight from the Arduino's top header, and VDD from the bottom + rail
wire(4, "scl", [(TX["SCL"], T), (TX["SCL"], 784), (SF["SCL"][0], 784), SF["SCL"]], "Arduino SCL (top header)", "SFM3300 SCL", badge=(330, 784))
wire(5, "sda", [(TX["SDA"], T), (TX["SDA"], 774), (SF["SDA"][0], 774), SF["SDA"]], "Arduino SDA (top header)", "SFM3300 SDA", badge=(400, 774))
wire(6, "gnd", [(TX["GND"], T), (TX["GND"], 764), (SF["GND"][0], 764), SF["GND"]], "Arduino GND (top header)", "SFM3300 GND", badge=(470, 764))
wire(7, "5v", [hole("B+:3"), (cx(3), 700), (SF["VDD"][0], 700), SF["VDD"]], "B+:3", "SFM3300 VDD", badge=(250, 700))

# Level shifter and SprintIR-R
wire(8, "5v", [hole("B+:43"), (cx(43) - 10, 632), (cx(43) - 10, 600), (cx(42), 592), hole("j42")], "B+:43", "j42 (shifter HV)", badge=(cx(42) - 2, 666))
wire(9, "gnd", [hole("B-:45"), (cx(45), 604), (cx(43) + 10, 596), (cx(43), 588), hole("j43")], "B-:45", "j43 (shifter GND, 5 V side)", badge=(cx(46) + 10, 611))
wire(10, "tx", [(TX["D1/TX"], T), (TX["D1/TX"], 712), (cx(40), 712), hole("j40")], "Arduino D1/TX", "j40 (shifter B1)", badge=(cx(40) - 2, 690))
wire(11, "rx", [(TX["D0/RX"], T), (TX["D0/RX"], 762), (cx(41), 762), hole("j41")], "Arduino D0/RX", "j41 (shifter B2)", badge=(cx(41) + 2, 740))
wire(12, "tx", [hole("a40"), (cx(40), 260), (SP[1][0], 240), SP[1]], "a40 (shifter A1)", "SprintIR-R pin 1 Rx_In", badge=(cx(40) - 2, 286))
wire(13, "rx", [hole("a41"), (cx(41), 250), (SP[2][0], 232), SP[2]], "a41 (shifter A2)", "SprintIR-R pin 2 Tx_Out", badge=(cx(41) + 20, 262))
wire(14, "gnd", [hole("a43"), (cx(43), 240), (SP[3][0], 224), SP[3]], "a43 (shifter GND, 3 V side)", "SprintIR-R pin 3 GND", badge=(cx(43) + 22, 238))
wire(15, "ext", [hole("T+:45"), (cx(45), 270), (SP[4][0], 250), SP[4]], "T+:45", "SprintIR-R pin 4 VDD", badge=(SP[4][0] + 26, 262))

# Pump
wire(16, "pwm", [(TX["D10"], T), (TX["D10"], 740), (cx(8), 740), hole("j8")], "Arduino D10", "j8 (R1 to the gate)", badge=(cx(8), 660))
wire(17, "gnd", [hole("j14"), (cx(14), 600), (cx(15), 606), hole("B-:15")], "j14 (Q1 source)", "B-:15", badge=(cx(16) + 12, 611))
wire(18, "drain", [PM, (PM[0], 230), (cx(13), 250), hole("g13")], "Pump − contact", "g13 (Q1 drain)", badge=(cx(13) - 22, 268))
wire(19, "ext", [PP, (PP[0], 230), (cx(21), 250), hole("T+:21")], "Pump + contact", "T+:21", badge=(cx(21) + 20, 268))

# Reed switch
wire(20, "sw", [(TX["D3"], T), (TX["D3"], 702), (cx(27), 702), hole("j27")], "Arduino D3", "j27 (reed lead)", badge=(cx(27) - 4, 640))
wire(21, "sw", [hole("j29"), (cx(29), 600), (cx(30), 606), hole("B-:30")], "j29 (reed lead)", "B-:30", badge=(cx(31) + 10, 611))

# Optional: buzzer, button
wire(24, "opt", [(TX["D8"], T), (TX["D8"], 730), (cx(20), 730), hole("j20")], "Arduino D8", "j20 (buzzer +)", badge=(cx(20), 660), optional=True)
wire(28, "gnd", [hole("j23"), (cx(23), 600), (cx(24), 606), hole("B-:24")], "j23 (buzzer −)", "B-:24", badge=(cx(25) + 6, 611), optional=True)
wire(25, "opt", [(TX["D2"], T), (TX["D2"], 692), (cx(33), 692), hole("j33")], "Arduino D2", "j33 (button)", badge=(cx(33) + 2, 640), optional=True)
wire(29, "gnd", [hole("a35"), (cx(35), 360), (cx(36), 352), hole("T-:36")], "a35 (button, diagonal leg)", "T-:36", badge=(cx(37) + 8, 352), optional=True)

for w_ in WIRES:
    draw_wire(w_)
for w_ in WIRES:
    draw_badge(w_)

# ------------------------------------------------------------------ the rail map
MY = 1084
rect(20, MY, W - 40, 112, fill="#fbf9f3", stroke="#d8d0b8")
text(34, MY + 22, "What goes on each rail", 13, weight="bold")
rails = [
    ("T+", COL["ext"], "Top + rail = +5V_EXT", "in: W22 from J1's end pin · out: W15 SprintIR-R VDD, W19 pump +"),
    ("T-", "#1b1b1b", "Top − rail = GND", "in: W23 from J1's middle pin · out: W29 button (opt.) · joined to the bottom − rail by W27"),
    ("B-", "#1b1b1b", "Bottom − rail = GND", "in: W2 Arduino GND · out: W9 shifter, W17 Q1 source, R2, W21 reed, W28 buzzer (opt.)"),
    ("B+", COL["5v"], "Bottom + rail = +5V", "in: W1 Arduino 5V · out: W7 SFM3300 VDD, W8 shifter HV"),
]
text(1000, MY + 44, "Never join the two + rails: they are different supplies.", 11, weight="bold", fill="#c0392b")
text(1000, MY + 63, "Both − rails are GND and must be joined (W27).", 11, fill="#333")
text(1000, MY + 82, "If the red/blue lines on your board break in the middle, each", 11, fill="#333")
text(1000, MY + 101, "rail is two halves: bridge each break with a short jumper.", 11, fill="#333")
for i, (_k, c, a, b) in enumerate(rails):
    yy = MY + 44 + i * 19
    add(f'<line x1="34" y1="{yy - 4}" x2="70" y2="{yy - 4}" stroke="{c}" stroke-width="5"/>')
    text(80, yy, a, 11, weight="bold")
    text(250, yy, b, 11, fill="#333")

# ------------------------------------------------------------------ every wire, end to end
LY = MY + 128
text(24, LY, "Every wire, end to end (hole names as printed on the breadboard)", 14, weight="bold")
ordered = sorted(WIRES, key=lambda w_: w_["n"])
half = (len(ordered) + 1) // 2
for i, w_ in enumerate(ordered):
    col_ = i // half
    row = i % half
    x0 = 24 + col_ * 860
    yy = LY + 26 + row * 22
    c = COL[w_["kind"]]
    dash = ' stroke-dasharray="7 4"' if w_["optional"] else ""
    add(f'<line x1="{x0}" y1="{yy - 4}" x2="{x0 + 34}" y2="{yy - 4}" stroke="#000" stroke-opacity="0.75" stroke-width="7"{dash}/>')
    add(f'<line x1="{x0}" y1="{yy - 4}" x2="{x0 + 34}" y2="{yy - 4}" stroke="{c}" stroke-width="4.4"{dash}/>')
    text(x0 + 42, yy, f"W{w_['n']}", 11, weight="bold")
    a = hole_label(w_["a"])
    b = hole_label(w_["b"])
    cname = COLOUR_NAME[w_["kind"]] + (", optional" if w_["optional"] else "")
    text(x0 + 76, yy, f"{a}  →  {b}", 11)
    text(x0 + 820, yy, cname, 10, "end", fill="#666")

PY2 = LY + 26 + half * 22 + 8
text(
    24,
    PY2,
    "Parts in holes: J1 c3/c5/c7 · Q1 IRLZ44N f12 G, f13 D, f14 S (printed side towards row j) · R1 220 Ω h8-h12 · R2 100 kΩ i12 → bottom − rail · "
    "U1 shifter A1-A4 side d40-d45, B side g40-g45 · reed leads g27, g29 · buzzer g20 (+), g23 (opt.) · button e33/e35/f33/f35 (opt.)",
    10.5,
    fill="#333",
)
text(
    24,
    PY2 + 17,
    "Check the shifter's printed pin names against this order (A1 A2 LV GND A3 A4 / B1 B2 HV GND B3 B4) and keep each wire on the column of the named pin. "
    "Optional R3, R4 4.7 kΩ I2C pull-ups and the Q2 heater driver (W26) are not drawn; see docs/wiring.md.",
    10.5,
    fill="#333",
)
add("</svg>")

OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {OUT}")
