"""The header bar and the fixed-height alarm strip under it."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ..alarms.engine import AlarmRecord
from ..engine.monitor import Snapshot
from . import theme
from .widgets import duration, fmt, hms, mss

PRI_LABEL = {"high": "HIGH", "medium": "MED", "advisory": "INFO"}


def describe(a: AlarmRecord) -> str:
    if a.state == "resolved":
        peak = ""
        if a.peak is not None and a.id in ("co2High", "co2Low", "co2Stop"):
            peak = f" ({'lowest' if a.id == 'co2Low' else 'peak'} {a.peak:.0f} mmHg)"
        return f"{a.message}{peak}: resolved at {hms(a.resolved_at or a.since)}"
    return a.message


class Header(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)
        self.setMinimumHeight(46)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(18)
        self.ruo = QLabel("RESEARCH USE ONLY")
        self.study = QLabel()
        self.dot = QLabel("●")
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.rec = QLabel()
        self.pill = QLabel()
        self.pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock.setMinimumWidth(110)
        st = QHBoxLayout()
        st.setSpacing(8)
        st.addWidget(self.dot)
        st.addWidget(self.status)
        for w in (self.ruo, self.study):
            lay.addWidget(w)
        lay.addLayout(st)
        lay.addStretch(1)
        for w in (self.rec, self.pill, self.clock):
            lay.addWidget(w)
        self._style_key = None

    def update_from(self, s: Snapshot) -> None:
        p = theme.current
        connected = s.status == "connected"
        any_active = any(a.state == "active" and a.priority != "advisory" for a in s.alarms)
        if s.silence_ms > 0:
            pill = ("silenced", f"SILENCED {mss(s.silence_ms)}")
        elif any_active:
            pill = ("alarm", "ALARM")
        elif not s.audio_armed:
            pill = ("audio-off", "AUDIO OFF")
        elif not connected:
            pill = ("", "DISARMED")
        else:
            pill = ("armed", "ARMED")

        key = (p.name, pill[0], s.status, s.recording.active)
        if key != self._style_key:
            self._style_key = key
            self.ruo.setStyleSheet(f"font-weight:700; font-size:13px; letter-spacing:1.3px; color:{p.alarm_med}; border:1px solid {p.alarm_med}; padding:2px 8px; border-radius:3px;")
            dot = {"connected": p.ok, "connecting": p.alarm_med, "error": p.alarm_high}.get(s.status, p.muted)
            self.dot.setStyleSheet(f"color:{dot}; font-size:14px;")
            self.rec.setStyleSheet(f"font-weight:600; color:{p.alarm_high if s.recording.active else p.muted};")
            fg, bg, border = {
                "alarm": (p.on_alarm, p.alarm_high, p.alarm_high),
                "silenced": (p.alarm_med, "transparent", p.alarm_med),
                "audio-off": (p.advisory, "transparent", p.advisory),
                "armed": (p.ok, "transparent", p.ok),
            }.get(pill[0], (p.text, "transparent", p.muted))
            self.pill.setStyleSheet(f"font-weight:700; font-size:13px; letter-spacing:1.3px; padding:3px 10px; border-radius:11px; color:{fg}; background:{bg}; border:1px solid {border};")
            self.clock.setStyleSheet("font-size:24px; font-weight:600;")
        self.study.setText(f"<span style='color:{p.muted}'>Study</span> <b>{s.settings.study_code or '—'}</b>")
        if s.status == "connected":
            text = f"<b>{s.source_label}</b>"
        elif s.status == "connecting":
            text = f"<b>Connecting… {s.source_label}</b>"
        elif s.status == "error":
            text = "<b>Connection lost</b>"
        else:
            text = "<b>Not connected</b>"
        m = p.muted
        if connected:
            text += f"<span style='color:{m}'> · {fmt(s.measured_rate_hz)} Hz · </span><b>{s.dropped}</b><span style='color:{m}'> dropped</span>"
            if s.bad_lines:
                text += f"<span style='color:{m}'> · </span><b>{s.bad_lines}</b><span style='color:{m}'> bad lines</span>"
        if s.using_file_calibration:
            text += f"<span style='color:{m}'> · file calibration</span>"
        if s.replay_progress is not None:
            text += f"<span style='color:{m}'> · {round(s.replay_progress * 100)} %</span>"
        self.status.setText(text)
        self.status.setToolTip(s.status_detail)
        self.rec.setText(f"● REC {duration(s.now - s.recording.started_at)}" if s.recording.active else "Not recording")
        self.pill.setText(pill[1])
        self.clock.setText(hms(s.now))


class AlarmStrip(QFrame):
    """Fixed height, so an alarm never moves the waveforms. Color, text and flashing
    all show the priority."""

    acknowledge = Signal(str)
    arm_audio = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(46)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 6, 0)
        lay.setSpacing(14)
        self.pri = QLabel()
        self.msg = QLabel()
        self.msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.msg.setTextFormat(Qt.TextFormat.RichText)
        self.more = QPushButton()
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.clicked.connect(self._show_list)
        self.action = QPushButton()
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._on_action)
        lay.addWidget(self.pri)
        lay.addWidget(self.msg, 1)
        lay.addWidget(self.more)
        lay.addWidget(self.action)
        self._alarms: list[AlarmRecord] = []
        self._top: AlarmRecord | None = None
        self._style_key = None

    def update_from(self, s: Snapshot, blink_fast: bool, blink_slow: bool) -> None:
        p = theme.current
        self._alarms = s.alarms
        top = s.alarms[0] if s.alarms else None
        self._top = top
        if top is None:
            kind = "none"
        elif top.state == "resolved":
            kind = f"resolved-{top.priority}"
        else:
            kind = top.priority
        flash = top is not None and top.state == "active" and not top.acknowledged and ((top.priority == "high" and blink_fast) or (top.priority == "medium" and blink_slow))
        key = (p.name, kind, flash)
        if key != self._style_key:
            self._style_key = key
            fg, bg, border, width = {
                "none": (p.muted, "transparent", p.border, 1),
                "advisory": (p.advisory, "transparent", p.advisory, 1),
                "high": (p.on_alarm, p.alarm_high, p.alarm_high, 1),
                "medium": (p.on_med, p.alarm_med, p.alarm_med, 1),
                "resolved-high": (p.alarm_high, "transparent", p.alarm_high, 2),
                "resolved-medium": (p.alarm_med, "transparent", p.alarm_med, 2),
            }[kind]
            if flash:
                c = p.q("on_alarm" if kind == "high" else "on_med")
                fg = f"rgba({c.red()},{c.green()},{c.blue()},80)"
            self.setStyleSheet(
                f"AlarmStrip {{ background:{bg}; border:{width}px solid {border}; border-radius:4px; }}"
                f"QLabel {{ color:{fg}; font-size:18px; font-weight:{500 if kind == 'none' else 600}; }}"
                f"QPushButton {{ color:{fg if kind != 'none' else p.text}; background:transparent; border:2px solid {fg}; border-radius:3px;"
                f" min-height:30px; padding:0 14px; font-weight:700; font-size:14px; letter-spacing:1px; }}"
            )
            pri_bg = {"high": (p.alarm_high, p.on_alarm), "medium": (p.alarm_med, p.on_med)}.get(kind)
            if pri_bg:
                self.pri.setStyleSheet(f"font-size:13px; font-weight:700; letter-spacing:1.5px; padding:2px 8px; border-radius:3px; color:{pri_bg[0]}; background:{pri_bg[1]};")
            else:
                self.pri.setStyleSheet(f"font-size:13px; font-weight:700; letter-spacing:1.5px; padding:2px 8px; border-radius:3px; border:1px solid {fg}; color:{fg};")
            self.more.setStyleSheet(f"font-size:13px; min-height:22px; padding:0 8px; border:1px solid {fg}; border-radius:3px; letter-spacing:0;")

        if top is None:
            self.pri.hide()
            self.more.hide()
            self.action.hide()
            self.msg.setText("No active alarms" if s.status == "connected" else "Not connected. Alarms work only while a source is connected.")
            return
        self.pri.show()
        self.pri.setText("RESOLVED" if top.state == "resolved" else PRI_LABEL[top.priority])
        text = describe(top)
        if top.state == "active" and top.priority != "advisory":
            text += f"<span style='font-weight:500'> · since {hms(top.since)}</span>"
        if s.silence_ms > 0 and top.priority != "advisory":
            text += f"<span style='font-weight:500'> · audio silenced {mss(s.silence_ms)}</span>"
        self.msg.setText(text)
        self.more.setVisible(len(s.alarms) > 1)
        self.more.setText(f"+{len(s.alarms) - 1} more")
        if top.id == "audioOff":
            self.action.setText("ARM AUDIO")
            self.action.show()
        elif top.priority != "advisory":
            self.action.setText("CLEAR" if top.state == "resolved" else "ACKNOWLEDGE")
            self.action.show()
        else:
            self.action.hide()

    def _on_action(self) -> None:
        top = self._top
        if top is None:
            return
        if top.id == "audioOff":
            self.arm_audio.emit()
        else:
            self.acknowledge.emit(top.id)

    def _show_list(self) -> None:
        menu = QMenu(self)
        for a in self._alarms:
            chip = "RESOLVED" if a.state == "resolved" else PRI_LABEL[a.priority]
            text = f"[{chip}]  {describe(a)}  ·  {hms(a.since)}"
            act = menu.addAction(text)
            if a.priority == "advisory":
                act.setEnabled(False)
            else:
                verb = "Clear" if a.state == "resolved" else "Acknowledge"
                act.setToolTip(verb)
                act.triggered.connect(lambda _=False, aid=a.id: self.acknowledge.emit(aid))
        menu.setToolTipsVisible(True)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))


class Banner(QFrame):
    """Research-use notice; dismissed for the rest of this run."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 6, 4)
        msg = QLabel(
            "Research use only. Not a medical device and not for clinical decision-making. Keep a certified monitor as the primary alarm; "
            "this alarm works only while this program is running, the computer is awake and sound is on."
        )
        msg.setWordWrap(True)
        ok = QPushButton("I understand")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self.hide)
        lay.addWidget(msg, 1)
        lay.addWidget(ok)

    def apply_theme(self) -> None:
        p = theme.current
        self.findChild(QPushButton).setStyleSheet(f"background:none; border:1px solid {p.alarm_med}; color:{p.alarm_med}; min-height:24px; padding:0 10px;")


def spacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return w
