"""A very small PDF writer: text in the standard fonts, lines, rectangles and circles.

It exists so the test report can be made anywhere the monitor runs (the desktop app,
the browser under Pyodide, the command line) with nothing to install. Coordinates are
in points from the top-left corner of the page; text is limited to Windows-1252.
"""

from __future__ import annotations

from typing import Sequence

#: font name → PDF resource
FONTS = {"regular": ("F1", "Helvetica"), "bold": ("F2", "Helvetica-Bold"), "mono": ("F3", "Courier"), "mono-bold": ("F4", "Courier-Bold")}
LETTER = (612.0, 792.0)
#: approximate widths of Helvetica glyphs, in thousandths of the font size
_NARROW = {" ": 278, ".": 278, ",": 278, ":": 278, ";": 278, "i": 222, "l": 222, "j": 222, "I": 278, "'": 191, "|": 260, "-": 333, "(": 333, ")": 333, "f": 278, "t": 278, "r": 333, "/": 278}
_WIDE = {"m": 833, "w": 722, "M": 833, "W": 944, "%": 889, "@": 1015}

Color = Sequence[float]
BLACK: Color = (0, 0, 0)


def text_width(s: str, size: float, font: str = "regular") -> float:
    """Width of ``s`` in points: exact for the monospaced fonts, close for Helvetica."""
    if font.startswith("mono"):
        return 0.6 * size * len(s)
    w = 0
    for ch in s:
        w += _NARROW.get(ch) or _WIDE.get(ch) or (667 if ch.isupper() else 556)
    return w * size / 1000 * (1.06 if font == "bold" else 1)


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def _escape(s: str) -> bytes:
    raw = s.replace("−", "-").replace("≥", ">=").replace("≤", "<=").encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class Pdf:
    def __init__(self, size: tuple[float, float] = LETTER, title: str = "", author: str = "") -> None:
        self.w, self.h = size
        self.title = title
        self.author = author
        self._pages: list[list[bytes]] = []
        self._cur = 0

    # ---------- pages and drawing

    def page(self) -> None:
        """Start a new page; what is drawn next goes on it."""
        self._pages.append([])
        self._cur = len(self._pages) - 1

    @property
    def pages(self) -> int:
        return len(self._pages)

    def select(self, index: int) -> None:
        """Go back to an earlier page (0 is the first), e.g. to add "page 1 of 3"."""
        self._cur = index

    def _op(self, b: bytes) -> None:
        if not self._pages:
            self.page()
        self._pages[self._cur].append(b)

    def _y(self, y: float) -> float:
        return self.h - y

    def text(self, x: float, y: float, s: str, size: float = 10, font: str = "regular", color: Color = BLACK, align: str = "left") -> None:
        """Write one line with its baseline at ``y``."""
        if align != "left":
            w = text_width(s, size, font)
            x -= w if align == "right" else w / 2
        res = FONTS[font][0]
        r, g, b = color
        self._op(f"BT /{res} {_n(size)} Tf {_n(r)} {_n(g)} {_n(b)} rg {_n(x)} {_n(self._y(y))} Td (".encode() + _escape(s) + b") Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 1, color: Color = BLACK, dash: Sequence[float] | None = None) -> None:
        r, g, b = color
        d = f"[{' '.join(_n(v) for v in dash)}] 0 d" if dash else "[] 0 d"
        self._op(f"{_n(r)} {_n(g)} {_n(b)} RG {_n(width)} w {d} {_n(x1)} {_n(self._y(y1))} m {_n(x2)} {_n(self._y(y2))} l S".encode())

    def rect(self, x: float, y: float, w: float, h: float, stroke: Color | None = BLACK, fill: Color | None = None, width: float = 1) -> None:
        self._shape(f"{_n(x)} {_n(self._y(y + h))} {_n(w)} {_n(h)} re", stroke, fill, width)

    def circle(self, x: float, y: float, r: float, stroke: Color | None = BLACK, fill: Color | None = None, width: float = 1) -> None:
        k = 0.5523 * r
        cy = self._y(y)
        p = (
            f"{_n(x + r)} {_n(cy)} m "
            f"{_n(x + r)} {_n(cy + k)} {_n(x + k)} {_n(cy + r)} {_n(x)} {_n(cy + r)} c "
            f"{_n(x - k)} {_n(cy + r)} {_n(x - r)} {_n(cy + k)} {_n(x - r)} {_n(cy)} c "
            f"{_n(x - r)} {_n(cy - k)} {_n(x - k)} {_n(cy - r)} {_n(x)} {_n(cy - r)} c "
            f"{_n(x + k)} {_n(cy - r)} {_n(x + r)} {_n(cy - k)} {_n(x + r)} {_n(cy)} c h"
        )
        self._shape(p, stroke, fill, width)

    def cross(self, x: float, y: float, r: float, color: Color = BLACK, width: float = 1) -> None:
        self.line(x - r, y - r, x + r, y + r, width, color)
        self.line(x - r, y + r, x + r, y - r, width, color)

    def _shape(self, path: str, stroke: Color | None, fill: Color | None, width: float) -> None:
        ops = [f"{_n(width)} w [] 0 d"]
        if stroke is not None:
            ops.append("{} {} {} RG".format(*(_n(v) for v in stroke)))
        if fill is not None:
            ops.append("{} {} {} rg".format(*(_n(v) for v in fill)))
        paint = "B" if stroke is not None and fill is not None else "f" if fill is not None else "S"
        self._op(f"{' '.join(ops)} {path} {paint}".encode())

    # ---------- output

    def bytes(self) -> bytes:
        """The finished document."""
        if not self._pages:
            self.page()
        objs: list[bytes] = []  # object n is objs[n - 1]

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        catalog = add(b"")
        pages = add(b"")
        fonts = {res: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode()) for res, base in FONTS.values()}
        font_dict = " ".join(f"/{res} {n} 0 R" for res, n in fonts.items())
        kids = []
        for ops in self._pages:
            stream = b"\n".join(ops)
            content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
            kids.append(
                add(f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {_n(self.w)} {_n(self.h)}] /Resources << /Font << {font_dict} >> >> /Contents {content} 0 R >>".encode())
            )
        objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>".encode()
        objs[pages - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
        info = add(b"<< /Title (" + _escape(self.title) + b") /Author (" + _escape(self.author) + b") /Producer (CO2-Response) >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
