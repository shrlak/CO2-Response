"""Bedside-monitor look. Dark is the default for low-light and overnight rooms; light
is for daytime benches. Every color is a token here; widgets never use literals."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    wave_bg: str
    panel: str
    panel2: str
    panel3: str
    grid: str
    border: str
    text: str
    muted: str
    co2: str
    flow: str
    pneumo: str
    alarm_high: str
    alarm_med: str
    on_alarm: str
    on_med: str
    advisory: str
    ok: str
    input_bg: str

    def channel(self, cid: str) -> str:
        return {"co2": self.co2, "flow": self.flow, "pneumo": self.pneumo}[cid]

    def q(self, name: str, alpha: float = 1.0) -> QColor:
        c = QColor(getattr(self, name))
        c.setAlphaF(alpha)
        return c


DARK = Palette(
    "dark",
    bg="#000000",
    wave_bg="#000000",
    panel="#0b0f14",
    panel2="#131a23",
    panel3="#1a222d",
    grid="#2a3643",
    border="#26313d",
    text="#e6ebf0",
    muted="#8693a1",
    co2="#ffd60a",
    flow="#38d9f5",
    pneumo="#5be37d",
    alarm_high="#ff3b30",
    alarm_med="#ff9f0a",
    on_alarm="#ffffff",
    on_med="#1a1000",
    advisory="#8ab4f8",
    ok="#5be37d",
    input_bg="#0f151c",
)

LIGHT = Palette(
    "light",
    bg="#e9edf1",
    wave_bg="#ffffff",
    panel="#ffffff",
    panel2="#f3f5f8",
    panel3="#e6eaef",
    grid="#d0d7df",
    border="#c9d0d8",
    text="#0f151c",
    muted="#56616d",
    co2="#8a6500",
    flow="#00739a",
    pneumo="#187a37",
    alarm_high="#d0021b",
    alarm_med="#b75400",
    on_alarm="#ffffff",
    on_med="#ffffff",
    advisory="#1a5bbf",
    ok="#187a37",
    input_bg="#ffffff",
)

#: the palette in use; custom-painted widgets read it when they paint
current: Palette = DARK

FONT_FAMILIES = ["Barlow Semi Condensed", "Arial Narrow", "Roboto Condensed", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"]
MONO_FAMILIES = ["Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "monospace"]


def resolve(pref: str) -> Palette:
    if pref == "light":
        return LIGHT
    if pref == "system":
        try:
            scheme = QGuiApplication.styleHints().colorScheme()
            return LIGHT if scheme == Qt.ColorScheme.Light else DARK
        except AttributeError:
            return DARK
    return DARK


def ui_font(px: int = 15, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    return f


def mono_font(px: int = 12) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(px)
    return f


def stylesheet(p: Palette) -> str:
    fam = ", ".join(f'"{f}"' for f in FONT_FAMILIES[:-1]) + ", sans-serif"
    return f"""
* {{ font-family: {fam}; font-size: 15px; color: {p.text}; }}
QMainWindow, QWidget#root {{ background: {p.bg}; }}
QToolTip {{ background: {p.panel3}; color: {p.text}; border: 1px solid {p.border}; padding: 4px; }}
QFrame[panel="true"] {{ background: {p.panel}; border-radius: 4px; }}
QFrame[card="true"] {{ background: {p.panel2}; border: 1px solid {p.border}; border-radius: 6px; }}
QFrame[card="true"][accent="co2"] {{ border-top: 3px solid {p.co2}; }}
QFrame[card="true"][accent="flow"] {{ border-top: 3px solid {p.flow}; }}
QFrame[card="true"][accent="pneumo"] {{ border-top: 3px solid {p.pneumo}; }}
QFrame[stat="true"] {{ background: {p.panel2}; border-radius: 4px; }}
QFrame[stat="inner"] {{ background: {p.panel}; border-radius: 4px; }}
QFrame[rule="true"] {{ background: {p.grid}; border: none; }}
QFrame#overlayCard, QFrame#drawer, QFrame#popover {{ background: {p.panel}; border: 1px solid {p.border}; border-radius: 6px; }}
QFrame#toast {{ background: {p.panel3}; border: 1px solid {p.border}; border-radius: 4px; }}
QWidget#backdrop {{ background: rgba(0, 0, 0, 110); }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {p.muted}; }}
QLabel[role="note"] {{ color: {p.muted}; font-size: 13px; }}
QLabel[role="lead"] {{ color: {p.muted}; }}
QLabel[role="warn"] {{ color: {p.alarm_med}; font-size: 14px; font-weight: 600; }}
QLabel[role="err"] {{ color: {p.alarm_high}; font-weight: 600; }}
QLabel[role="ok"] {{ color: {p.ok}; font-weight: 600; }}
QLabel[role="h1"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 16px; font-weight: 600; letter-spacing: 1px; }}
QLabel[role="k"] {{ color: {p.muted}; font-size: 12px; letter-spacing: 1px; }}
QLabel[role="v"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="dirty"] {{ color: {p.alarm_med}; font-size: 13px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: "Consolas", "Menlo", "DejaVu Sans Mono", monospace; font-size: 12px; }}
QLabel[chip] {{ font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 9px; border: 1px solid {p.muted}; color: {p.muted}; }}
QLabel[chip="pass"], QLabel[chip="complete"] {{ color: {p.ok}; border-color: {p.ok}; }}
QLabel[chip="warn"], QLabel[chip="interrupted"] {{ color: {p.alarm_med}; border-color: {p.alarm_med}; }}
QLabel[chip="fail"], QLabel[chip="recording"] {{ color: {p.alarm_high}; border-color: {p.alarm_high}; }}
QLabel[chip="info"], QLabel[chip="imported"] {{ color: {p.advisory}; border-color: {p.advisory}; }}
QPushButton {{
  min-height: 34px; padding: 0 14px; border-radius: 4px; border: 1px solid {p.border};
  background: {p.panel2}; font-weight: 600;
}}
QPushButton:hover {{ border-color: {p.muted}; }}
QPushButton:disabled {{ color: {p.muted}; border-color: {p.grid}; }}
QPushButton[kind="primary"] {{ background: {p.text}; color: {p.bg}; border-color: {p.text}; }}
QPushButton[kind="primary"]:disabled {{ background: {p.muted}; border-color: {p.muted}; color: {p.panel}; }}
QPushButton[kind="danger"] {{ border-color: {p.alarm_high}; color: {p.alarm_high}; }}
QPushButton[size="small"] {{ min-height: 26px; padding: 0 10px; font-size: 13px; }}
QPushButton[seg="true"] {{ border-radius: 0; min-height: 32px; padding: 0 14px; font-weight: 500; }}
QPushButton[seg="true"]:checked {{ background: {p.text}; color: {p.bg}; font-weight: 600; }}
QPushButton[tab="true"] {{
  background: none; border: none; border-bottom: 2px solid transparent; border-radius: 0;
  color: {p.muted}; font-size: 16px; font-weight: 700; letter-spacing: 1px; padding: 2px 0; min-height: 24px;
}}
QPushButton[tab="true"]:checked {{ color: {p.co2}; border-bottom-color: {p.co2}; }}
QPushButton[nav="true"] {{
  text-align: left; background: none; border: none; border-left: 3px solid transparent; border-radius: 0;
  padding: 10px 12px; font-size: 16px; color: {p.muted};
}}
QPushButton[nav="true"]:checked {{ color: {p.text}; border-left-color: {p.co2}; background: {p.panel2}; }}
QPushButton[link="true"] {{ background: none; border: none; color: {p.advisory}; text-decoration: underline; min-height: 0; padding: 0; font-weight: 400; }}
QPushButton#softkey {{ min-height: 54px; background: {p.panel2}; padding: 2px 6px; }}
QPushButton#softkey[state="armed"] {{ border-color: {p.ok}; }}
QPushButton#softkey[state="need"] {{ border-color: {p.advisory}; }}
QPushButton#softkey[state="active"] {{ border-color: {p.alarm_med}; }}
QPushButton#softkey[state="rec"] {{ border-color: {p.alarm_high}; }}
QPushButton[expand="true"] {{ min-height: 0; padding: 0; font-size: 17px; font-weight: 700; background: {p.panel2}; border: 1px solid {p.border}; border-radius: 3px; }}
QPushButton[expand="true"]:checked {{ color: {p.co2}; border-color: {p.co2}; }}
QFrame#simpanel {{ background: {p.panel}; border: 1px solid {p.border}; border-radius: 4px; }}
QFrame#simstart {{ border: 1px solid {p.advisory}; border-radius: 4px; }}
QLabel#simtitle {{ color: {p.co2}; font-size: 19px; font-weight: 700; letter-spacing: 2px; }}
QLabel#simsection {{ font-size: 14px; font-weight: 700; letter-spacing: 1px; border-top: 1px solid {p.border}; padding-top: 8px; margin-top: 4px; }}
QLabel[readback="set"] {{ font-size: 17px; font-weight: 600; }}
QLabel[readback="monitor"] {{ font-size: 17px; font-weight: 600; color: {p.ok}; }}
QPushButton[sit="true"] {{ min-height: 30px; padding: 2px 8px; text-align: left; font-size: 14px; }}
QPushButton[sit="true"]:checked {{ color: {p.co2}; border-color: {p.co2}; }}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPlainTextEdit {{
  background: {p.input_bg}; border: 1px solid {p.border}; border-radius: 3px; padding: 3px 6px; min-height: 26px;
  selection-background-color: {p.co2}; selection-color: {p.bg};
}}
QLineEdit:disabled, QDoubleSpinBox:disabled {{ color: {p.muted}; }}
QComboBox QAbstractItemView {{ background: {p.panel}; border: 1px solid {p.border}; selection-background-color: {p.panel3}; selection-color: {p.text}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {p.muted}; border-radius: 3px; background: {p.input_bg}; }}
QCheckBox::indicator:checked {{ background: {p.co2}; border-color: {p.co2}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.panel3}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; border-radius: 8px; background: {p.co2}; }}
QProgressBar {{ height: 6px; max-height: 6px; background: {p.panel3}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {p.co2}; border-radius: 3px; }}
QTableWidget {{ background: transparent; border: none; gridline-color: {p.grid}; font-size: 14px; }}
QTableWidget::item {{ border-bottom: 1px solid {p.grid}; padding: 2px 4px; }}
QTableWidget::item:selected {{ background: {p.panel3}; color: {p.text}; }}
QHeaderView {{ background: transparent; border: none; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; color: {p.muted}; border: none; border-bottom: 1px solid {p.border}; padding: 4px; font-weight: 500; }}
QListWidget {{ background: {p.input_bg}; border: 1px solid {p.border}; border-radius: 4px; font-size: 13px; }}
QListWidget::item {{ border-bottom: 1px solid {p.grid}; padding: 2px 4px; }}
QListWidget::item:selected {{ background: {p.panel3}; color: {p.text}; }}
QMenu {{ background: {p.panel}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; }}
QMenu::item:selected {{ background: {p.panel3}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p.panel3}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p.panel3}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QDialog {{ background: {p.panel}; }}
"""
