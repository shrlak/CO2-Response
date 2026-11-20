"""Parametric 3D-printed parts for the CO2-Response clinic build.

Run:  python hardware/3d-prints/build_parts.py
Needs: pip install cadquery   (writes step/*.step and stl/*.stl next to this file)

Every dimension that depends on a purchased part is a named constant below,
with where the number came from. Measure your own parts with calipers and
change the constant if they differ; then re-run the script.
All units are millimetres.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import cadquery as cq

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Fits to purchased parts
# ---------------------------------------------------------------------------

# ISO 5356-1 22 mm breathing-circuit cone, 1:40 taper (diameter changes 1 mm
# per 40 mm of length). The SFM3300-250-D has a 22 mm male end and a 22 mm
# female socket (Sensirion datasheet); rebreathing (anaesthesia) bags and
# breathing filters use the same cone.
CONE_D_BIG = 22.40      # male cone diameter at its root
CONE_LEN = 18.0         # engagement length
CONE_D_SMALL = CONE_D_BIG - CONE_LEN / 40.0
FEMALE_CLEARANCE = 0.10  # added to the socket diameter (printed holes shrink)

# 1/2 in. Sch 40 PVC pipe: 0.840 in. = 21.34 mm outside, 0.622 in. = 15.8 mm
# inside. The adapters end in a spigot of this size that pushes into the
# 1/2 in. socket of the 3/4 x 1/2 in. reducer bushing (Home Depot
# PVC021070600HD), wrapped with the purchased PTFE tape.
PIPE_OD = 21.34
PIPE_ID = 15.8
SPIGOT_LEN = 22.0

# Collar every circuit adapter shares: the tripod cradle (P07) and the reed
# switch bracket (P05) clamp onto this diameter.
COLLAR_D = 30.0

# Luer-to-barb adapters (budget request, Amazon 25-packs). Their barb end is
# pushed into this bore and sealed with epoxy, so the Luer end faces out.
# Barbs for 1/8 in. (3.2 mm) tubing measure about 3.2 mm at the shank and
# 3.8-4.0 mm over the ridges: measure yours.
BARB_BORE = 3.6
BARB_DEPTH = 9.0
SIDE_HOLE = 2.0          # passage from the barb into the airway

# Adafruit 375 magnetic contact switch, each half (Adafruit product page):
# 29 x 15.2 x 9 mm. The reed closes with the magnet within about 13 mm.
REED_L, REED_W, REED_H = 29.0, 15.2, 9.0
POCKET_CLEAR = 0.4

# Arduino UNO R4 Minima: same board outline and mounting holes as the Uno R3
# (holes checked against the team's Arduino_R3.step). Origin = the corner by
# the USB connector, X along the 68.6 mm side.
UNO_L, UNO_W = 68.6, 53.3
UNO_HOLES = [(13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56)]

# Half-size breadboard (rows 1-30, columns a-j, the size docs/wiring.md is
# drawn for): 82.5 x 54.6 mm. The team's Breadboard.step is a 46 x 36 mm
# mini board; set BREADBOARD to (46.0, 36.0) if that is the one used.
BREADBOARD = (82.5, 54.6)

# Adafruit 2107 USB isolator: 52 x 30 x 10 mm (Adafruit product page).
ISOLATOR = (52.0, 30.0)

# ZR370-02PM pump (Adafruit 4699 drawing): head 27.0 dia x 27.3 long,
# motor 24 dia, 58.2 long overall, outlet nozzle 6.1 long on the head end,
# side inlet on the head.
PUMP_HEAD_D, PUMP_HEAD_L = 27.0, 27.3
PUMP_MOTOR_D, PUMP_L = 24.0, 58.2

# GSS SprintIR-R (CO2meter datasheet): 23.8 dia x 24 mm, gas nozzles out of
# one flat face, pins out of the other.
SPRINTIR_D, SPRINTIR_L = 23.8, 24.0

# Electronics tray: set to the inside floor of the ABS project enclosure
# (budget request, Newark) minus about 1 mm each way.
TRAY_L, TRAY_W = 180.0, 104.0   # needs an enclosure floor of at least 182 x 106 mm, 45 mm tall inside

# Tripod thread: 1/4-20 UNC camera screw. Captive hex nut 7/16 in. across
# flats (11.11 mm).
NUT_AF, NUT_H, TRIPOD_BOLT = 11.11 + 0.3, 5.8, 6.6

M3_CLEAR = 3.4
M3_TAP = 2.8             # an M3 screw cuts its own thread in PETG


# ---------------------------------------------------------------------------
# Helpers (all revolved parts stand on the XY plane, axis along +Z)
# ---------------------------------------------------------------------------

def cyl(d: float, h: float, z: float = 0.0) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z).circle(d / 2).extrude(h)


def cone(d0: float, d1: float, h: float, z: float = 0.0) -> cq.Workplane:
    """Frustum from diameter d0 at height z to d1 at z + h."""
    if abs(d0 - d1) < 1e-6:
        return cyl(d0, h, z)
    s = cq.Solid.makeCone(d0 / 2, d1 / 2, h, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Workplane("XY").add(s)


def chamfer_top(d: float, z_top: float, c: float = 0.8) -> cq.Workplane:
    """Material to cut for a 45 degree chamfer on the top edge of a diameter-d shaft."""
    return cyl(d + 4, c, z_top - c).cut(cone(d, d - 2 * c, c, z_top - c))


def chamfer_bottom(d: float, z_bot: float, c: float = 0.8) -> cq.Workplane:
    return cyl(d + 4, c, z_bot).cut(cone(d - 2 * c, d, c, z_bot))


def male_cone(z: float, bore: float) -> cq.Workplane:
    """ISO 22 mm male cone rising from z, root diameter at z, with a lead-in."""
    body = cone(CONE_D_BIG, CONE_D_SMALL, CONE_LEN, z)
    body = body.cut(chamfer_top(CONE_D_SMALL, z + CONE_LEN))
    return body.cut(cyl(bore, CONE_LEN + 1, z - 0.5))


def female_socket(z: float, depth: float = CONE_LEN) -> cq.Workplane:
    """Volume to cut for an ISO 22 mm socket whose mouth is at z, opening downwards."""
    mouth = CONE_D_BIG + FEMALE_CLEARANCE
    bottom = mouth - depth / 40.0
    s = cone(mouth, bottom, depth, z)
    return s.union(cone(mouth + 1.6, mouth, 0.8, z - 0.001))   # chamfered mouth


def side_barb_boss(z: float, body_r: float, length: float = 10.0) -> tuple[cq.Workplane, cq.Workplane]:
    """A boss on +X at height z that takes a Luer-to-barb adapter's barb.

    Returns (material to add, material to remove)."""
    boss = (cq.Workplane("YZ").workplane(offset=body_r - 2).center(0, z)
            .circle(5.0).extrude(length + 2))
    bore = (cq.Workplane("YZ").workplane(offset=body_r + length - BARB_DEPTH).center(0, z)
            .circle(BARB_BORE / 2).extrude(BARB_DEPTH + 1))
    hole = cq.Workplane("YZ").workplane(offset=0).center(0, z).circle(SIDE_HOLE / 2).extrude(body_r + length)
    return boss, bore.union(hole)


def label_ring(text: str, z: float, d: float, size: float = 4.0) -> cq.Workplane:
    """Text raised 0.6 mm on the -Y side of a cylinder of diameter d, following its curve."""
    prism = (cq.Workplane("XZ").workplane(offset=d / 2 - 3.0).center(0, z)
             .text(text, size, 3.6, kind="bold"))
    return prism.intersect(cyl(d + 1.2, 400, -200))


def pocket_block(base_extra_l: float = 0.0) -> cq.Workplane:
    """Open-top holder for one half of the Adafruit 375 switch."""
    pl, pw = REED_L + POCKET_CLEAR, REED_W + POCKET_CLEAR
    wall, floor = 2.0, 2.5
    depth = REED_H - 2.0          # the top 2 mm stands proud, so it can face the other half
    blk = cq.Workplane("XY").box(pl + 2 * wall, pw + 2 * wall, floor + depth, centered=(True, True, False))
    blk = blk.cut(cq.Workplane("XY").workplane(offset=floor).box(pl, pw, depth + 1, centered=(True, True, False)))
    # the switch's lead leaves from one short end
    blk = blk.cut(cq.Workplane("XY").workplane(offset=floor + 1.0)
                  .center(pl / 2 + wall / 2, 0).box(wall + 2, 6.0, depth, centered=(True, True, False)))
    # cable-tie slots across the pocket hold the switch down
    for x in (-pl / 4, pl / 4):
        blk = blk.cut(cq.Workplane("XY").center(x, 0).box(3.0, pw + 2 * wall + 2, 2.0, centered=(True, True, False))
                      .translate((0, 0, floor + depth - 4.5)))
    return blk


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------

def p00_fit_coupon() -> cq.Workplane:
    """Print first: checks the 22 mm cones and the pipe spigot on your printer."""
    a = cyl(28, 12).cut(female_socket(0, 12)).union(male_cone(12, 15.0))
    a = a.cut(cyl(15.0, 40, -1))
    b = cyl(PIPE_OD, 14).union(cyl(COLLAR_D, 4)).cut(cyl(PIPE_ID, 20, -1)).translate((40, 0, 0))
    return a.union(b)


def p01_sample_tee() -> cq.Workplane:
    """Mouthpiece adapter with the CO2 sample port.

    Bottom: 22 mm socket that pushes onto the SFM3300's 22 mm male end.
    Top: 22 mm male cone for the mouthpiece (or a breathing filter).
    Side: boss for a male Luer-to-barb adapter; the 3-way stopcock (T1)
    pushes onto its male Luer."""
    od = 28.0
    body = cyl(od, CONE_LEN + 16)
    body = body.union(male_cone(CONE_LEN + 16, 15.0))
    z_port = CONE_LEN + 8
    boss, bore = side_barb_boss(z_port, od / 2)
    body = body.union(boss)
    body = body.cut(female_socket(0))
    body = body.cut(cyl(15.0, 100, CONE_LEN - 0.5))
    body = body.cut(bore)
    body = body.union(label_ring("SFM", 7, od, 4.5))
    return body


def p02_flow_to_valve() -> cq.Workplane:
    """Joins the SFM3300's 22 mm female end to the valve.

    Bottom: 1/2 in. pipe spigot into the reducer bushing on the valve's
    subject port. Middle: 30 mm collar (tripod cradle grips it). Top: 22 mm
    male cone into the SFM3300's female socket."""
    body = cyl(PIPE_OD, SPIGOT_LEN).union(cyl(COLLAR_D, 12, SPIGOT_LEN))
    body = body.union(male_cone(SPIGOT_LEN + 12, PIPE_ID))
    body = body.cut(chamfer_bottom(PIPE_OD, 0))
    body = body.cut(cyl(PIPE_ID, 100, -1))
    body = body.union(label_ring("VALVE", SPIGOT_LEN + 6, COLLAR_D, 4.0))
    return body


def p03_bag_adapter() -> cq.Workplane:
    """Joins the rebreathing bag to the valve's bag port, with the pump's exhaust return.

    Bottom: 1/2 in. pipe spigot into the reducer bushing on the valve's bag
    port. Collar: side boss for a female Luer-to-barb adapter (the pump
    exhaust, T6, comes back here) and room for the reed-switch bracket.
    Top: 22 mm male cone for the bag's 22 mm neck."""
    collar_len = 30.0
    body = cyl(PIPE_OD, SPIGOT_LEN).union(cyl(COLLAR_D, collar_len, SPIGOT_LEN))
    body = body.union(male_cone(SPIGOT_LEN + collar_len, PIPE_ID))
    boss, bore = side_barb_boss(SPIGOT_LEN + 8, COLLAR_D / 2)
    body = body.union(boss).cut(bore)
    body = body.cut(cyl(PIPE_ID, 120, -1))
    body = body.union(label_ring("BAG", SPIGOT_LEN + 22, COLLAR_D, 4.5))
    return body


def p04_zero_cap() -> cq.Workplane:
    """Caps the mouth end of P01 to zero the flow sensor (pump running) and leak-test."""
    od, h = 28.0, CONE_LEN + 3
    cap = cyl(od, h).cut(female_socket(0))
    tab = (cq.Workplane("XY").workplane(offset=h - 3).center(od / 2 + 4, 0)
           .rect(12, 8).extrude(3).edges("|Z").fillet(2))
    cap = cap.union(tab).cut(cyl(4, 10, h - 4).translate((od / 2 + 5.5, 0, 0)))
    cap = cap.union(cq.Workplane("XY").workplane(offset=h).text("ZERO", 5, 0.8, kind="bold"))
    # grip ribs
    for i in range(12):
        a = i * 30
        rib = cq.Workplane("XY").center(od / 2, 0).rect(1.6, 1.6).extrude(h - 4).rotate((0, 0, 0), (0, 0, 1), a)
        cap = cap.union(rib)
    return cap


def p05_reed_bracket() -> tuple[cq.Workplane, cq.Workplane]:
    """Reed-switch bracket: a clamp ring on P03's collar with a slotted arm, and a switch carrier.

    The carrier bolts to the arm's slot with an M3 screw, so it slides
    outwards and turns to meet the magnet on the valve handle."""
    ri, ro, t = COLLAR_D / 2 + 0.3, COLLAR_D / 2 + 4.5, 10.0
    ring = cq.Workplane("XY").circle(ro).circle(ri).extrude(t)
    ears = cq.Workplane("XY").center(-(ro + 4), 0).rect(12, 12).extrude(t)
    ring = ring.union(ears)
    ring = ring.cut(cq.Workplane("XY").center(-(ro + 4), 0).rect(16, 2.0).extrude(t + 1))   # split
    ring = ring.cut(cq.Workplane("XZ").center(-(ro + 5), t / 2).circle(M3_CLEAR / 2).extrude(20, both=True))
    arm_len = 60.0
    arm = cq.Workplane("XY").center(ro - 1 + arm_len / 2, 0).rect(arm_len, 14).extrude(5)
    arm = arm.edges("|Z").fillet(3)
    slot = cq.Workplane("XY").center(ro + 8 + 20, 0).slot2D(42, M3_CLEAR).extrude(10, both=True)
    bracket = ring.union(arm).cut(slot)

    carrier = pocket_block()
    pl = REED_L + POCKET_CLEAR + 4
    ear = cq.Workplane("XY").center(-(pl / 2 + 6), 0).rect(14, 14).extrude(3.0).edges("|Z").fillet(3)
    carrier = carrier.union(ear).cut(cq.Workplane("XY").center(-(pl / 2 + 7), 0).circle(M3_CLEAR / 2).extrude(10))
    return bracket, carrier


def p06_magnet_clip() -> cq.Workplane:
    """Holds the magnet half of the switch on the valve handle with two cable ties."""
    clip = pocket_block()
    pl = REED_L + POCKET_CLEAR + 4
    base = cq.Workplane("XY").box(pl + 22, REED_W + POCKET_CLEAR + 4, 2.5, centered=(True, True, False))
    clip = clip.union(base)
    for x in (pl / 2 + 5.5, -(pl / 2 + 5.5)):
        clip = clip.cut(cq.Workplane("XY").center(x, 0).rect(3.0, 9.0).extrude(10, both=True))
    # shallow V underneath seats it on a round or flat handle
    v = (cq.Workplane("YZ").polyline([(-5, -0.01), (5, -0.01), (0, 1.6)]).close()
         .extrude(pl + 30, both=True))
    return clip.cut(v)


def p07_tripod_cradle() -> cq.Workplane:
    """Holds the circuit at P02's collar on a tripod (1/4-20 screw) or a pole (cable ties).

    Drawn as a profile in XZ and extruded 16 mm along Y: print it lying on its side."""
    w = 16.0
    ri = COLLAR_D / 2 + 0.2
    ro = ri + 4.0
    zc = 8 + 30 + ro
    base = cq.Workplane("XZ").center(0, 4).rect(44, 8).extrude(w / 2, both=True)
    post = cq.Workplane("XZ").center(0, 8 + 16).rect(14, 34).extrude(w / 2, both=True)
    ring = cq.Workplane("XZ").center(0, zc).circle(ro).circle(ri).extrude(w / 2, both=True)
    gap = math.radians(52)       # snap opening: half-angle from the top
    opening = (cq.Workplane("XZ").center(0, zc)
               .polyline([(0, 0), (ro * 2 * math.sin(gap), ro * 2 * math.cos(gap)),
                          (-ro * 2 * math.sin(gap), ro * 2 * math.cos(gap))]).close()
               .extrude(w, both=True))
    part = base.union(post).union(ring).cut(cq.Workplane("XZ").center(0, zc).circle(ri).extrude(w, both=True))
    part = part.cut(opening)
    # 1/4-20 bolt hole and captive nut from below
    part = part.cut(cyl(TRIPOD_BOLT, 40, -1))
    nut = cq.Workplane("XY").polygon(6, NUT_AF / math.cos(math.radians(30))).extrude(NUT_H)
    part = part.cut(nut.translate((0, 0, -0.01)))
    # cable-tie slots for a pole instead of a tripod
    for x in (-16, 16):
        part = part.cut(cq.Workplane("XY").center(x, 0).rect(3.0, w + 2).extrude(3).translate((0, 0, 2.5)))
    return part


UNO_STANDOFF = 6.0
UNO_LEFT = TRAY_L - 2.0 - UNO_L   # x of the Uno's USB-C edge, which faces the breadboard (-X)
UNO_FRONT = TRAY_W - 4.0 - UNO_W  # y of the Uno's front long edge


def uno_holes_on_tray() -> list[tuple[float, float]]:
    return [(UNO_LEFT + hx, UNO_FRONT + hy) for hx, hy in UNO_HOLES]


def p08_tray() -> cq.Workplane:
    """Electronics tray that sits in the ABS project enclosure.

    Holds the Uno R4 Minima (4 standoffs), the breadboard (rim; use its
    adhesive back), the USB isolator (rim, its Micro-B end at the tray edge
    so the laptop cable reaches it), the pump (two saddles, cable ties, foam
    tape against vibration) and the SprintIR-R (saddle, cable tie)."""
    t = 3.0
    tray = cq.Workplane("XY").box(TRAY_L, TRAY_W, t, centered=False).edges("|Z").fillet(4)
    # Back row: breadboard on the left, Uno on the right with its USB-C edge
    # facing the breadboard across a gap for the cable's plug.
    for hx, hy in uno_holes_on_tray():
        tray = tray.union(cq.Workplane("XY").center(hx, hy).circle(3.2).extrude(t + UNO_STANDOFF))
        tray = tray.cut(cq.Workplane("XY").center(hx, hy).circle(M3_TAP / 2).extrude(20))

    def rim(cx, cy, length, w, h=3.0, wall=1.6):
        r = cq.Workplane("XY").workplane(offset=t).center(cx, cy).rect(length + 2 * wall, w + 2 * wall).extrude(h)
        return r.cut(cq.Workplane("XY").workplane(offset=t).center(cx, cy).rect(length + 0.6, w + 0.6).extrude(h + 1))

    bl, bw = BREADBOARD
    tray = tray.union(rim(bl / 2 + 3.6, TRAY_W - 4 - bw / 2, bl, bw))
    il, iw = ISOLATOR
    ix = il / 2 + 2.6
    iso = rim(ix, 4 + iw / 2, il, iw)
    iso = iso.cut(cq.Workplane("XY").center(ix - il / 2, 4 + iw / 2).rect(6, iw - 6).extrude(20))   # open towards the wall
    tray = tray.union(iso)

    def saddle(cx, cy, d, length, zc):
        """Cradle for a cylinder lying along X with its axis at height zc; open on top.

        A cable tie runs through the tunnel under it and over the part."""
        blk = cq.Workplane("XY").workplane(offset=t).center(cx, cy).rect(length, d + 6).extrude(zc - t)
        bore = (cq.Workplane("YZ").workplane(offset=cx - length).center(cy, zc)
                .circle(d / 2 + 0.2).extrude(2 * length))
        tunnel = (cq.Workplane("XY").workplane(offset=t - 1.5).center(cx, cy)
                  .rect(3.6, d + 10).extrude(2.0))
        return blk.cut(bore), tunnel

    def add_saddle(tray, *args):
        blk, tunnel = saddle(*args)
        return tray.union(blk).cut(tunnel)

    # Pump: motor end (solder tags) towards the isolator, head and outlet nozzle
    # towards the SprintIR-R; one axis for both saddles.
    py = 4 + iw / 2
    px = il + 12
    pz = t + 1.0 + PUMP_HEAD_D / 2
    motor_l = PUMP_L - PUMP_HEAD_L
    tray = add_saddle(tray, px + motor_l / 2, py, PUMP_MOTOR_D, 12, pz)
    tray = add_saddle(tray, px + motor_l + PUMP_HEAD_L / 2, py, PUMP_HEAD_D, 12, pz)
    # SprintIR-R: gas nozzles face the pump, pins face the right-hand wall.
    sx = px + PUMP_L + 6.1 + 18
    tray = add_saddle(tray, sx + SPRINTIR_L / 2, py, SPRINTIR_D, 16, t + 1.0 + SPRINTIR_D / 2)
    # four countersunk holes to screw the tray to the enclosure floor; the
    # heads sit flush because the breadboard and isolator cover two of them
    for hx, hy in ((6, 6), (TRAY_L - 6, 6), (6, TRAY_W - 6), (TRAY_L - 6, TRAY_W - 6)):
        tray = tray.cut(cq.Workplane("XY").center(hx, hy).circle(M3_CLEAR / 2).extrude(10, both=True))
        tray = tray.cut(cone(M3_CLEAR, 6.6, 1.7, t - 1.7 + 0.01).translate((hx, hy, 0)))
    tray = tray.union(cq.Workplane("XY").workplane(offset=t).center(ix, 4 + iw + 6)
                      .text("ISOLATOR  Micro-B to wall", 3.2, 0.6))
    tray = tray.union(cq.Workplane("XY").workplane(offset=t).center(px + PUMP_L / 2, 4 + iw + 6)
                      .text("PUMP  motor <  > head", 3.2, 0.6))
    tray = tray.union(cq.Workplane("XY").workplane(offset=t).center(sx + SPRINTIR_L / 2, 4 + iw + 6)
                      .text("< gas  CO2  pins >", 3.2, 0.6))
    return tray


def p09_gland_plate() -> cq.Workplane:
    """Covers one 70 x 16 mm cut-out in the enclosure wall: tubes and cables pass through it, labelled."""
    L, H, t = 90.0, 30.0, 3.0
    plate = cq.Workplane("XY").box(L, H, t, centered=(True, True, False)).edges("|Z").fillet(3)
    holes = [(-30, 2, 5.2, "CO2 IN"), (-15, 2, 5.2, "EXHAUST"), (-2, 2, 3.6, "VALVE")]
    for x, y, d, txt in holes:
        plate = plate.cut(cq.Workplane("XY").center(x, y).circle(d / 2).extrude(10, both=True))
        plate = plate.union(cq.Workplane("XY").workplane(offset=t).center(x, -9).text(txt, 2.4, 0.6, kind="bold"))
    # one slot for the laptop USB cable and the 5 V adapter lead (their plugs pass through)
    plate = plate.cut(cq.Workplane("XY").center(20, 1).slot2D(22, 11).extrude(10, both=True))
    plate = plate.union(cq.Workplane("XY").workplane(offset=t).center(20, -9.5).text("USB + 5V", 2.4, 0.6, kind="bold"))
    for x in (-41, 41):
        for y in (-10, 10):
            plate = plate.cut(cq.Workplane("XY").center(x, y).circle(M3_CLEAR / 2).extrude(10, both=True))
    return plate


PARTS = {
    "P00_fit_coupon": p00_fit_coupon,
    "P01_sample_port_tee": p01_sample_tee,
    "P02_flow_sensor_to_valve_adapter": p02_flow_to_valve,
    "P03_bag_adapter_exhaust_return": p03_bag_adapter,
    "P04_zero_cap": p04_zero_cap,
    "P05a_reed_switch_bracket": lambda: p05_reed_bracket()[0],
    "P05b_reed_switch_carrier": lambda: p05_reed_bracket()[1],
    "P06_magnet_clip": p06_magnet_clip,
    "P07_tripod_cradle": p07_tripod_cradle,
    "P08_electronics_tray": p08_tray,
    "P09_enclosure_gland_plate": p09_gland_plate,
}


def main(names: list[str]) -> None:
    (HERE / "step").mkdir(exist_ok=True)
    (HERE / "stl").mkdir(exist_ok=True)
    for name, fn in PARTS.items():
        if names and not any(n in name for n in names):
            continue
        part = fn()
        shape = part.val() if len(part.vals()) == 1 else part.combine().val()
        bb = shape.BoundingBox()
        cq.exporters.export(part, str(HERE / "step" / f"{name}.step"))
        cq.exporters.export(part, str(HERE / "stl" / f"{name}.stl"), tolerance=0.02, angularTolerance=0.1)
        print(f"{name:36s} {bb.xlen:6.1f} x {bb.ylen:6.1f} x {bb.zlen:6.1f} mm  "
              f"{shape.Volume() / 1000:6.1f} cm3  valid={shape.isValid()}")


if __name__ == "__main__":
    main(sys.argv[1:])
