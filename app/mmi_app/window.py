"""The window (ui_sketch.png): a vertical hint bar, and to its right the unit's status with the input in use in the
top-right corner (a gamepad or a keyboard), the drive, and Light over Temp; or, while the menu is open, the menu in
their place (ui_sketch_menu.png). Only widgets and drawing; what goes in them comes from controller.py."""
from __future__ import annotations

import html
import time
from collections import deque

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSlider, QStackedWidget, QVBoxLayout, QWidget

import pyqtgraph as pg   # after PyQt6, so pyqtgraph uses it
import qtawesome as qta  # likewise (QtPy picks the binding already imported)

from .menu import ITEMS, SLIDERS
from .texts import BOARD_MAX, MEDIUM_WARN, SENSORS, lid_line, sd_text, temp_text

COLOURS = {"ok": "#57c26b", "busy": "#55a0ff", "alert": "#ff6161", "off": "#8c8c8c"}   # the unit LED's code
TEXT, DIM, LINE, BG, PANEL = "#d6d6d6", "#7c7c7c", "#3d3d3d", "#0b0b0b", "#131313"
WARM, COOL, MEDIUM, MEDIUM2 = "#ffb347", "#8fd3ff", "#e8e8e8", "#a6a6a6"
CHOSEN, ON_CHOSEN = "#f2d675", "#141414"   # the sketch's yellow: the menu's chosen item, and its text

STYLE = f"""
QWidget#root {{ background: {BG}; }}
QFrame#box {{ background: {PANEL}; border: 2px solid {LINE}; border-radius: 12px; }}
QLabel {{ color: {TEXT}; background: transparent; border: none; }}
QLabel#note {{ color: {DIM}; }}
"""


def _font(px: int) -> QFont:
    f = QFont()
    f.setPixelSize(px)
    return f


class HintBar(QWidget):
    """A narrow rounded bar, its text running upwards: Press HOME for menu (the 8BitDo), PS (the DualSense) or Esc."""

    def __init__(self, text: str, px: int):
        super().__init__()
        self._text, self._font = text, _font(px)
        self.setFixedWidth(int(px * 2.2))

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(LINE), 2))
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        p.setPen(QColor(DIM))
        p.setFont(self._font)
        p.translate(0, self.height())
        p.rotate(-90)
        p.drawText(QRectF(0, 0, self.height(), self.width()), Qt.AlignmentFlag.AlignCenter, self._text)


class LineBox(QFrame):
    """One rounded line, as drawn: a caption (`unit status:`), the value, each segment in its own colour (texts.py), a
    note on the right. `plain` is the value as plain text."""

    def __init__(self, caption: str, px: int):
        super().__init__()
        self.setObjectName("box")
        self.caption, self.value, self.note = QLabel(caption), QLabel(), QLabel()
        self.value.setTextFormat(Qt.TextFormat.RichText)
        self.plain = ""
        self.note.setObjectName("note")
        self.caption.setFont(_font(px))
        self.value.setFont(_font(px))
        self.note.setFont(_font(int(px * 0.7)))
        self._note_max = int(px * 10)   # a long note is cut short, not the value
        self.note.setMaximumWidth(self._note_max)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.addWidget(self.caption)
        lay.addWidget(self.value, 1)
        lay.addWidget(self.note)
        self._shown: tuple | None = None

    def set(self, line: list[tuple[str, str]], note: str = "") -> None:
        if (line, note) == self._shown:
            return
        self._shown = (list(line), note)
        self.plain = "".join(text for text, _ in line)
        self.value.setText("".join(f'<span style="color: {COLOURS[level]}">{html.escape(text).replace(" ", "&nbsp;")}'
                                   "</span>" for text, level in line))
        self.note.setText(QFontMetrics(self.note.font()).elidedText(note, Qt.TextElideMode.ElideRight, self._note_max))


class InputIcon(QFrame):
    """The top-right corner: what's driving, as Font Awesome draws it (QtAwesome): its gamepad or its keyboard."""

    GLYPHS = {"joystick": "fa6s.gamepad", "keyboard": "fa6s.keyboard"}

    def __init__(self, px: int):
        super().__init__()
        self.setObjectName("box")
        self.kind = "keyboard"
        self.setFixedWidth(int(px * 3.2))
        self._icons = {kind: qta.icon(glyph, color=TEXT) for kind, glyph in self.GLYPHS.items()}

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)   # the rounded box
        p = QPainter(self)
        h = round(self.height() * 0.8)   # the glyph's em; the drawing fills ~2/3 of it
        w = round(h * 1.25)              # the glyphs are 1.1 (keyboard) to 1.25 (gamepad) ems wide
        self._icons[self.kind].paint(p, QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h))
        p.end()


class Plots(QFrame):
    """Light (warm, cool, %) over Temp (the two medium probes, °C), on one time axis: the last window_s."""

    SERIES = {("light", "warm"): (WARM, "warm"), ("light", "cool"): (COOL, "cool"),
              ("lid", "med.j2"): (MEDIUM, "medium J2"), ("lid", "med.j3"): (MEDIUM2, "medium J3")}

    def __init__(self, window_s: float, px: int):
        super().__init__()
        self.setObjectName("box")
        self.window_s = window_s
        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(PANEL)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.view)
        self.light = self.view.addPlot(row=0, col=0)
        self.temp = self.view.addPlot(row=1, col=0, axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        small = _font(int(px * 0.6))
        for plot, label in ((self.light, "Light (%)"), (self.temp, "Temp (°C)")):
            plot.setMouseEnabled(x=False, y=False)
            plot.hideButtons()
            plot.setMenuEnabled(False)
            plot.showGrid(x=True, y=True, alpha=0.12)
            plot.addLegend(offset=(8, 4), labelTextColor=DIM, labelTextSize=f"{int(px * 0.55)}px",
                           brush=pg.mkBrush(19, 19, 19, 210))   # over the curves, readable
            for side in ("left", "bottom"):
                axis = plot.getAxis(side)
                axis.setPen(LINE)
                axis.setTextPen(DIM)
                axis.setStyle(tickFont=small)
            plot.setLabel("left", label, **{"color": DIM, "font-size": f"{int(px * 0.65)}px"})
            plot.getAxis("left").setWidth(int(px * 2.6))   # the same for both, so their time axes line up
        self.light.hideAxis("bottom")
        self.light.setXLink(self.temp)
        self.light.setYRange(0, 100, padding=0.02)
        self.temp.setLimits(minYRange=1.0)   # flat noise shouldn't fill the plot
        self.curves = {}
        for (topic, key), (colour, name) in self.SERIES.items():
            plot = self.light if topic == "light" else self.temp
            self.curves[(topic, key)] = plot.plot(pen=pg.mkPen(colour, width=2), name=name)
        self.data: dict[tuple, deque] = {k: deque() for k in self.SERIES}

    def add(self, t: float, topic: str, values: dict) -> None:
        for (tp, key), d in self.data.items():
            v = values.get(key)
            if tp == topic and isinstance(v, (int, float)):
                d.append((t, float(v)))

    def redraw(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        lo = now - self.window_s
        for k, d in self.data.items():
            while d and d[0][0] < lo:
                d.popleft()
            self.curves[k].setData([p[0] for p in d], [p[1] for p in d])
        self.temp.setXRange(lo, now, padding=0)

    def count(self) -> int:
        return sum(len(d) for d in self.data.values())


class MenuPanel(QFrame):
    """The menu (ui_sketch_menu.png): its items in a column on the left, the chosen one's page on the right. Drawn from
    menu.py's state by show(); it takes no input itself."""

    def __init__(self, items, sliders, px: int):
        super().__init__()
        self.setObjectName("box")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)
        column = QVBoxLayout()
        column.setSpacing(8)
        self.items = []
        for item in items:
            label = QLabel(item.label)
            label.setFont(_font(px))
            label.setContentsMargins(14, 8, 14, 8)
            column.addWidget(label)
            self.items.append(label)
        column.addStretch(1)
        lay.addLayout(column)
        self.pages = QStackedWidget()
        self.light = LightPage(sliders, px)
        self.temp = TempPage(px)
        self.pages.addWidget(self.light)
        self.pages.addWidget(self.temp)
        page = QFrame()
        page.setObjectName("box")
        inner = QVBoxLayout(page)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.addWidget(self.pages)
        lay.addWidget(page, 1)
        self._shown: tuple | None = None

    def show(self, menu, client) -> None:
        """From the menu's state and the client's mirror of the unit (its lights, its lid)."""
        lights, lid = client.lights, client.lid
        state = (menu.item, menu.inside, menu.row, tuple(s.value for s in menu.sliders), menu.duty.value,
                 menu.heat_on, menu.heat_note, menu.duty_max, menu.medium_max, tuple(sorted(lights.items())),
                 tuple(sorted((k, str(v)) for k, v in lid.items() if k != "t")), tuple(sorted(client.set_errors.items())),
                 client.connected)
        if state == self._shown:
            return
        self._shown = state
        for i, label in enumerate(self.items):
            if i != menu.item:
                label.setStyleSheet(f"color: {TEXT}; border: 2px solid {LINE}; border-radius: 10px;")
            elif menu.inside:   # inside it: still marked, the slider chosen in it filled
                label.setStyleSheet(f"color: {CHOSEN}; border: 2px solid {CHOSEN}; border-radius: 10px;")
            else:
                label.setStyleSheet(f"color: {ON_CHOSEN}; background: {CHOSEN}; border: 2px solid {CHOSEN}; "
                                    "border-radius: 10px;")
        self.pages.setCurrentIndex(menu.item)
        if menu.chosen.key == "light":
            self.light.show(menu, lights, client.set_errors.get("light"), client.connected)
        else:
            self.temp.show(menu, client)


class LightPage(QWidget):
    """Light calib.: a slider per channel (QSlider, 0.1 % a step), its level beside it, and what the unit has in force."""

    def __init__(self, sliders, px: int):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.rows = []
        for key, label in sliders:
            row = QFrame()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 8)
            top = QHBoxLayout()
            caption, value = QLabel(label), QLabel()
            caption.setFont(_font(px))
            value.setFont(_font(px))
            top.addWidget(caption)
            top.addStretch(1)
            top.addWidget(value)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)   # 0.1 %, the fine step; the unit sets to 0.024 %
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # moved by the menu only
            fill = WARM if key == "warm" else COOL
            slider.setStyleSheet(
                f"QSlider::groove:horizontal {{ height: 2px; background: {DIM}; }}"
                f"QSlider::sub-page:horizontal {{ height: 2px; background: {fill}; }}"
                f"QSlider::handle:horizontal {{ background: {PANEL}; border: 2px solid {TEXT}; border-radius: 6px; "
                f"width: {int(px * 0.9)}px; margin: -{int(px * 0.45)}px 0; }}")
            slider.setMinimumHeight(int(px * 1.4))
            rl.addLayout(top)
            rl.addWidget(slider)
            lay.addWidget(row)
            self.rows.append((row, value, slider))
        lay.addStretch(1)
        self.help, self.unit = QLabel(), QLabel()
        for label in (self.help, self.unit):
            label.setObjectName("note")
            label.setFont(_font(int(px * 0.7)))
            lay.addWidget(label)

    def show(self, menu, lights: dict, error: str | None, connected: bool) -> None:
        here = menu.inside and menu.chosen.key == "light"
        for i, (row, value, slider) in enumerate(self.rows):
            s = menu.sliders[i]
            slider.setValue(round(s.value * 10))
            value.setText(f"{s.value:.1f} %")
            chosen = here and menu.row == i
            row.setStyleSheet(f"QFrame {{ border: 2px solid {CHOSEN if chosen else 'transparent'}; "
                              "border-radius: 10px; } QLabel { border: none; }")
        self.help.setText("up, down: choose · left, right: set, 0.1 % (pushed all the way, or Shift: 1 %)" if here
                          else "right: in, and the lights go to these levels (out again: off)")
        if not connected:
            self.unit.setText("the unit: not connected")
            self.unit.setStyleSheet(f"color: {DIM}")
        elif error:
            self.unit.setText(f"the unit refused: {error}")
            self.unit.setStyleSheet(f"color: {COLOURS['alert']}")
        else:
            said = ", ".join(f"{k} {lights[k]:g} %" for k in ("warm", "cool") if k in lights) or "not said yet"
            self.unit.setText(f"the unit's lights: {said}")
            self.unit.setStyleSheet(f"color: {COLOURS['ok'] if any(lights.values()) else DIM}")


class TempPage(QWidget):
    """Temp calib.: the Peltiers' switch, the duty slider under it (centred: - cools, + heats), the lid's five
    temperatures (each readout's mean, its SD under it), the unit's limits, and what the unit's lid is doing."""

    def __init__(self, px: int):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.switch_row, self.duty_row = QFrame(), QFrame()
        sl = QHBoxLayout(self.switch_row)
        sl.setContentsMargins(10, 6, 10, 6)
        caption, self.switch = QLabel("Peltiers"), QLabel()
        for label in (caption, self.switch):
            label.setFont(_font(px))
        self.switch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.switch.setMinimumWidth(int(px * 3.4))
        sl.addWidget(caption)
        sl.addStretch(1)
        sl.addWidget(self.switch)
        dl = QVBoxLayout(self.duty_row)
        dl.setContentsMargins(10, 6, 10, 8)
        top = QHBoxLayout()
        self.caption, self.value = QLabel(), QLabel()
        for label in (self.caption, self.value):
            label.setFont(_font(px))
        top.addWidget(self.caption)
        top.addStretch(1)
        top.addWidget(self.value)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # moved by the menu only
        self.slider.setMinimumHeight(int(px * 1.4))
        self._px, self._cap, self._handle = px, None, None
        dl.addLayout(top)
        dl.addWidget(self.slider)
        lay.addWidget(self.switch_row)
        lay.addWidget(self.duty_row)
        grid = QGridLayout()
        grid.setContentsMargins(10, 4, 10, 0)
        grid.setHorizontalSpacing(int(px * 0.8))
        grid.setVerticalSpacing(0)
        small, tiny = _font(int(px * 0.8)), _font(int(px * 0.6))
        self.sensors = {}
        for r, (group, names) in enumerate(SENSORS):
            head = QLabel(group)
            head.setFont(small)
            head.setObjectName("note")
            grid.addWidget(head, 2 * r, 0)
            for i, (name, key) in enumerate(names):
                label, sd = QLabel(), QLabel()
                label.setFont(small)
                label.setTextFormat(Qt.TextFormat.RichText)
                sd.setFont(tiny)
                sd.setObjectName("note")
                grid.addWidget(label, 2 * r, 1 + i)
                grid.addWidget(sd, 2 * r + 1, 1 + i)
                self.sensors[key] = (name, label, sd)
            grid.setRowMinimumHeight(2 * r + 1, int(px * 1.1))
        grid.setColumnStretch(4, 1)
        lay.addLayout(grid)
        self.limits = QLabel()
        self.limits.setObjectName("note")
        self.limits.setFont(_font(int(px * 0.7)))
        self.limits.setContentsMargins(10, 0, 10, 0)
        lay.addWidget(self.limits)
        lay.addStretch(1)
        self.help, self.unit = QLabel(), QLabel()
        for label in (self.help, self.unit):
            label.setObjectName("note")
            label.setFont(_font(int(px * 0.7)))
            lay.addWidget(label)

    def show(self, menu, client) -> None:
        here = menu.inside and menu.chosen.key == "temp"
        for row, chosen in ((self.switch_row, here and menu.row == 0), (self.duty_row, here and menu.row == 1)):
            row.setStyleSheet(f"QFrame {{ border: 2px solid {CHOSEN if chosen else 'transparent'}; "
                              "border-radius: 10px; } QLabel { border: none; }")
        on = menu.heat_on
        self.switch.setText("ON" if on else "OFF")
        self.switch.setStyleSheet(f"color: {ON_CHOSEN if on else DIM}; background: {WARM if on else 'transparent'}; "
                                  f"border: 2px solid {WARM if on else DIM}; border-radius: 10px; padding: 2px 8px;")
        cap = menu.duty_max
        if cap != self._cap:
            self._cap = cap
            self.slider.setRange(-round(cap * 10), round(cap * 10))   # 0.1 %; the unit sets to 0.024 %
            self.caption.setText(f"{menu.duty.label}, ±{cap:g} %")
        d = menu.duty.value
        self.slider.setValue(round(d * 10))
        self.value.setText(f"{d:+.0f} %" if d else "0 %")
        handle = WARM if d > 0 else COOL if d < 0 else TEXT
        if handle != self._handle:   # no fill from the left: the groove is centred on 0, the handle coloured
            self._handle = handle
            px = self._px
            self.slider.setStyleSheet(
                f"QSlider::groove:horizontal {{ height: 2px; background: {DIM}; }}"
                f"QSlider::handle:horizontal {{ background: {PANEL}; border: 2px solid {handle}; border-radius: 6px; "
                f"width: {int(px * 0.9)}px; margin: -{int(px * 0.45)}px 0; }}")
        lid = client.lid if client.connected else {}
        medium_max = menu.medium_max
        for key, (name, label, sd) in self.sensors.items():
            board = key.startswith("board.")
            text, level = temp_text(lid.get(key), board=board, warn=None if board else medium_max - MEDIUM_WARN)
            label.setText(f'{name} <span style="color: {COLOURS[level] if level != "ok" else TEXT}">'
                          f'{html.escape(text).replace(" ", "&nbsp;")}</span>')
            sd.setText(sd_text(lid.get(key + ".sd")))
        self.limits.setText(f"the unit cuts the duty at {BOARD_MAX:g} °C on the board, {medium_max:g} °C in the medium")
        if not here:
            self.help.setText("right: in, with the Peltiers off until switched on there")
        elif menu.row == 0:
            self.help.setText("A / Enter: the Peltiers on or off · down: the duty")
        else:
            self.help.setText("left, right: the duty, 1 % (pushed all the way, or Shift: 5 %) · up: the switch")
        text, level = lid_line(client, menu)
        self.unit.setText(text)
        self.unit.setStyleSheet(f"color: {COLOURS[level] if level != 'off' else DIM}")


class MainWindow(QWidget):
    def __init__(self, window_s: float = 600.0, size: tuple[int, int] = (800, 480)):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("mmi")
        self.setStyleSheet(STYLE)
        px = max(14, round(size[1] / 24))   # 20 px lines at 480 high
        self.hint = HintBar("Press Esc for menu", round(px * 0.85))   # the keyboard's, until a pad is seen
        self.unit = LineBox("unit status:", px)
        self.input = InputIcon(px)
        self.drive = LineBox("drive:", px)
        self.plots = Plots(window_s, px)
        self.menu = MenuPanel(ITEMS, SLIDERS, px)
        self.main = QStackedWidget()   # the plots, or the menu in their place
        self.main.addWidget(self.plots)
        self.main.addWidget(self.menu)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.unit, 1)
        top.addWidget(self.input)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(top)
        right.addWidget(self.drive)
        right.addWidget(self.main, 1)
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self.hint)
        root.addLayout(right, 1)
        self.resize(*size)

    def show_menu(self, shown: bool) -> None:
        self.main.setCurrentWidget(self.menu if shown else self.plots)
