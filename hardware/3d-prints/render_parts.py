"""Render a preview PNG of every part (matplotlib, no CAD program needed).

Run after build_parts.py:  python hardware/3d-prints/render_parts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import build_parts as B
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

HERE = Path(__file__).resolve().parent
LIGHT = np.array([0.4, -0.6, 0.8]) / np.linalg.norm([0.4, -0.6, 0.8])
BASE = np.array([0.31, 0.56, 0.80])       # one blue for every part


def mesh(part):
    shape = part.val() if len(part.vals()) == 1 else part.combine().val()
    verts, tris = shape.tessellate(0.05, 0.2)
    v = np.array([(p.x, p.y, p.z) for p in verts])
    return v[np.array(tris)]


def draw(ax, tri, title, elev=24, azim=-58):
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    shade = 0.35 + 0.65 * np.clip(np.abs(n @ LIGHT), 0, 1)
    cols = np.clip(BASE[None, :] * shade[:, None] + 0.08, 0, 1)
    pc = Poly3DCollection(tri, facecolors=cols, edgecolors="none", linewidths=0)
    ax.add_collection3d(pc)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev, azim)
    ax.set_axis_off()
    size = hi - lo
    ax.set_title(f"{title}\n{size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm", fontsize=10)


VIEWS = {"P08_electronics_tray": (48, -70), "P09_enclosure_gland_plate": (60, -80),
         "P05a_reed_switch_bracket": (40, -60), "P07_tripod_cradle": (14, -35)}


def main():
    out = HERE / "renders"
    out.mkdir(exist_ok=True)
    names = list(B.PARTS)
    sheet = plt.figure(figsize=(16, 12), dpi=110)
    for i, name in enumerate(names):
        tri = mesh(B.PARTS[name]())
        elev, azim = VIEWS.get(name, (24, -58))
        fig = plt.figure(figsize=(5, 5), dpi=130)
        draw(fig.add_subplot(projection="3d"), tri, name.replace("_", " "), elev, azim)
        fig.savefig(out / f"{name}.png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        draw(sheet.add_subplot(3, 4, i + 1, projection="3d"), tri, name.replace("_", " "), elev, azim)
        print("rendered", name, file=sys.stderr)
    sheet.tight_layout()
    sheet.savefig(out / "all_parts.png", facecolor="white")




def tray_layout():
    """Top view of P08 with every purchased part it holds outlined and named."""
    import matplotlib.patches as mp
    tri = mesh(B.p08_tray())
    fig, ax = plt.subplots(figsize=(11, 7), dpi=130)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = n[:, 2] > 0
    z = tri[up][:, :, 2].mean(1)
    order = np.argsort(z)
    shade = 0.55 + 0.45 * (z[order] - z.min()) / (np.ptp(z) + 1e-9)
    from matplotlib.collections import PolyCollection
    fc = np.clip(BASE * shade[:, None] + 0.1, 0, 1)
    ax.add_collection(PolyCollection(tri[up][order][:, :, :2], facecolors=fc, edgecolors=fc, linewidths=0.4))
    il, iw = B.ISOLATOR
    bl, bw = B.BREADBOARD
    px = il + 12
    py = 4 + iw / 2
    sx = px + B.PUMP_L + 6.1 + 18
    items = [
        ((3.6 + 0.3, B.TRAY_W - 4 - bw), bl, bw, "Half-size breadboard\n(level shifter, Q1, R1, R2, D1,\nbarrel jack J1)"),
        ((B.UNO_LEFT, B.UNO_FRONT), B.UNO_L, B.UNO_W, "Arduino UNO R4 Minima\n4 x M3 screws\nUSB-C faces left  <"),
        ((2.6, 4), il, iw, "USB isolator\n(Adafruit 2107)\n< Micro-B: laptop"),
        ((px, py - B.PUMP_HEAD_D / 2), B.PUMP_L, B.PUMP_HEAD_D, "ZR370 pump\nmotor | head"),
        ((sx, py - B.SPRINTIR_D / 2), B.SPRINTIR_L, B.SPRINTIR_D, "SprintIR-R"),
    ]
    for (x, y), w, h, label in items:
        ax.add_patch(mp.Rectangle((x, y), w, h, fill=False, ec="#c0392b", lw=1.6, ls="--"))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=8.5, color="#111",
                bbox=dict(fc="white", ec="none", alpha=0.75, pad=1.5))
    ax.annotate("gas nozzles face the pump;\nCO2 IN tube from the gland plate\nto the SprintIR-R's inlet", (sx, py), (B.TRAY_L - 18, -24),
                fontsize=8, arrowprops=dict(arrowstyle="->"), ha="center")
    ax.annotate("outlet nozzle: EXHAUST tube\nto the gland plate", (px + B.PUMP_L + 3, py), (px + 40, -24),
                fontsize=8, arrowprops=dict(arrowstyle="->"), ha="center")
    ax.annotate("cable ties pass under each saddle", (px + 15.5, py - 15), (28, -24), fontsize=8,
                arrowprops=dict(arrowstyle="->"), ha="center")
    ax.text(B.TRAY_L / 2, -38, "Front wall of the enclosure, with the gland plate (P09), is along this bottom edge",
            ha="center", fontsize=9, style="italic")
    ax.set_xlim(-5, B.TRAY_L + 5)
    ax.set_ylim(-42, B.TRAY_W + 6)
    ax.set_aspect("equal")
    ax.set_xlabel("mm")
    ax.set_title("P08 electronics tray: what goes where (top view)")
    fig.tight_layout()
    fig.savefig(HERE / "renders" / "P08_tray_layout.png", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    if "--layout" not in sys.argv:
        main()
    tray_layout()
