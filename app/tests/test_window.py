"""The window end to end, offscreen: a test server coming and going, the client, the window, key presses, and a stand-in
for the 8BitDo coming and going.

MMI_REVIEW_DIR=<folder> also saves screenshots of the states there (and runs test_review_screens).
"""
import os
import re
import socket
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QTextDocumentFragment
from PyQt6.QtWidgets import QApplication

from mmi_client import MmiClient
from mmi_client.test_server import TestServer

from mmi_app.config import Config
from mmi_app.controller import Controller
from mmi_app.pad import PadState
from mmi_app.window import MainWindow

REVIEW = os.environ.get("MMI_REVIEW_DIR")
ULTIMATE = dict(connected=True, name="8BitDo Ultimate 2C Wired Controller", ids=(0x2DC8, 0x310A))
DUALSENSE = dict(connected=True, name="DualSense Wireless Controller", ids=(0x054C, 0x0CE6))
STILL = [0.0, 0.0, 0.0, 0.0]


class FakePad:
    """Stands in for pad.SdlGamepad: the test sets what it reads, and the pads it ignores."""

    def __init__(self):
        self.state, self.ignored = PadState(), ()

    def poll(self) -> PadState:
        return self.state

    def close(self) -> None:
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def shot(window, name: str) -> None:
    if REVIEW:
        QApplication.processEvents()   # any layout still pending
        window.grab().save(str(Path(REVIEW) / f"mmi_app_{name}.png"))


def make(qtbot, port: int, window_s: float = 60.0, env_hz: int = 10, pad=None):
    cfg = Config(host="127.0.0.1", port=port)
    client = MmiClient("127.0.0.1", port, reconnect=True, retry_s=0.1, watch_hz=10, deadman=cfg.deadman)
    client.watch(env_hz, "light")   # the lid's readouts come by themselves
    window = MainWindow(window_s)
    qtbot.addWidget(window)
    ctl = Controller(window, client, cfg, pad=pad)
    window.show()
    ctl.start()
    return window, ctl


def line(box) -> str:
    return box.plain


def tap(qtbot, window, key) -> None:
    """A key pressed and let go, slowly enough for the input timer (10 ms) to see it, as a finger is."""
    qtbot.keyPress(window, key)
    qtbot.wait(40)
    qtbot.keyRelease(window, key)
    qtbot.wait(30)


def push(pad, qtbot, device=ULTIMATE, **controls) -> None:
    """A pad (the 8BitDo, unless told): held so, for long enough to be read, then let go."""
    pad.state = PadState(**device, **controls)
    qtbot.wait(40)
    pad.state = PadState(**device)
    qtbot.wait(30)


def near(level: float, pct: float) -> bool:
    return abs(level - pct) < 100 / 4096   # the unit sets the nearest of its 4097 steps


def test_from_disconnected_to_driving(qtbot):
    port = free_port()
    window, ctl = make(qtbot, port)
    try:
        qtbot.waitUntil(lambda: line(window.unit) == f"DISCONNECTED | 127.0.0.1:{port}", timeout=2000)
        assert line(window.drive) == "DISABLED" and window.hint.text() == "Press Esc for menu"
        assert window.drive.note.text() == "keyboard" and window.input.kind == "keyboard"
        shot(window, "disconnected")
        with TestServer("127.0.0.1", port) as unit:   # the unit plugged in
            qtbot.waitUntil(lambda: line(window.unit).startswith("CONNECTED"), timeout=3000)
            assert line(window.unit) == f"CONNECTED | 127.0.0.1:{port} | fw test | motors on"
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=2000)

            qtbot.keyPress(window, Qt.Key.Key_Right)          # a key held: a jog, half a stick's deflection
            qtbot.waitUntil(lambda: unit.targets[0] > 0, timeout=2000)
            qtbot.waitUntil(lambda: line(window.drive) == "JOG x 18.1um/s y 0um/s z 0um/s r 0um/s", timeout=2000)
            shot(window, "jog")
            qtbot.wait(400)                                   # held past the lease: still moving, no halt
            assert unit.targets[0] > 0 and not unit.halted
            qtbot.keyRelease(window, Qt.Key.Key_Right)
            qtbot.waitUntil(lambda: unit.targets[0] == 0 and line(window.drive) == "PARKED", timeout=2000)

            qtbot.keyPress(window, Qt.Key.Key_Right)          # moving again, then the halt
            qtbot.waitUntil(lambda: unit.targets[0] > 0, timeout=2000)
            qtbot.keyPress(window, Qt.Key.Key_Space)
            qtbot.waitUntil(lambda: unit.halted and line(window.drive).startswith("HALTED"), timeout=1000)
            shot(window, "halted")
            qtbot.keyRelease(window, Qt.Key.Key_Space)        # the key still held: still halted
            qtbot.wait(300)
            assert unit.halted and unit.targets[0] == 0 and line(window.drive).startswith("HALTED")
            qtbot.keyRelease(window, Qt.Key.Key_Right)        # everything let go: released
            qtbot.waitUntil(lambda: not unit.halted and line(window.drive) == "PARKED", timeout=2000)

            tap(qtbot, window, Qt.Key.Key_Escape)             # the menu, then out of it again
            qtbot.waitUntil(lambda: ctl.menu.is_open and line(window.drive) == "DISABLED | menu", timeout=1000)
            tap(qtbot, window, Qt.Key.Key_Escape)
            qtbot.waitUntil(lambda: not ctl.menu.is_open and line(window.drive) == "PARKED", timeout=1000)

            qtbot.waitUntil(lambda: window.plots.count() > 20, timeout=3000)   # light and lid, plotted
        qtbot.waitUntil(lambda: line(window.unit).startswith("DISCONNECTED"), timeout=4000)   # unplugged
        assert line(window.drive) == "DISABLED"
    finally:
        ctl.stop()


def test_the_8bitdo_profile(qtbot):
    port = free_port()
    pad = FakePad()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port, pad=pad)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            assert window.hint.text() == "Press Esc for menu" and window.input.kind == "keyboard"
            pad.ignored = (("Xbox Wireless Controller", (0x045E, 0x0B13)),)   # another pad attached: not taken
            qtbot.waitUntil(lambda: window.drive.note.text() == "keyboard; other pad ignored", timeout=1000)
            pad.ignored = ()

            pad.state = PadState(**ULTIMATE)                              # the 8BitDo plugged in
            qtbot.waitUntil(lambda: window.hint.text() == "Press HOME for menu", timeout=1000)
            assert window.input.kind == "joystick" and window.drive.note.text() == "8BitDo Ultimate 2C"
            for k in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_Escape):   # the keyboard does nothing now
                qtbot.keyPress(window, k)
            qtbot.wait(300)
            assert unit.targets == STILL and not unit.halted and ctl.menu_presses == 0
            for k in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
                qtbot.keyRelease(window, k)                               # (Right stays held, for the unplug)

            pad.state = PadState(**ULTIMATE, rx=1.0)                      # the right stick, full right
            qtbot.waitUntil(lambda: unit.targets[0] == 250, timeout=2000)
            qtbot.waitUntil(lambda: line(window.drive) == "JOG x 250um/s y 0um/s z 0um/s r 0um/s", timeout=1000)
            pad.state = PadState(**ULTIMATE, rx=1.0, buttons=frozenset({"b"}))   # B: halt
            qtbot.waitUntil(lambda: unit.halted and line(window.drive).startswith("HALTED"), timeout=1000)
            pad.state = PadState(**ULTIMATE)                              # all let go: released
            qtbot.waitUntil(lambda: not unit.halted and line(window.drive) == "PARKED", timeout=2000)
            push(pad, qtbot, buttons=frozenset({"guide"}))               # HOME: the menu
            qtbot.waitUntil(lambda: ctl.menu.is_open and window.hint.text() == "Press HOME to exit menu", timeout=1000)
            push(pad, qtbot, buttons=frozenset({"guide"}))               # and out again
            qtbot.waitUntil(lambda: not ctl.menu.is_open and window.hint.text() == "Press HOME for menu", timeout=1000)

            pad.state = PadState()                                        # unplugged, Right held on the keyboard
            qtbot.waitUntil(lambda: window.hint.text() == "Press Esc for menu", timeout=1000)
            assert window.input.kind == "keyboard"
            qtbot.waitUntil(lambda: line(window.drive) == "HELD - let go to resume", timeout=1000)
            qtbot.wait(200)
            assert unit.targets == STILL                                  # the key doesn't move it by itself
            qtbot.keyRelease(window, Qt.Key.Key_Right)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=1000)
            qtbot.keyPress(window, Qt.Key.Key_Right)                      # pressed again: the keyboard drives
            qtbot.waitUntil(lambda: unit.targets[0] > 0, timeout=1000)
            qtbot.keyRelease(window, Qt.Key.Key_Right)
        finally:
            ctl.stop()


def test_the_dualsense_profile(qtbot):
    """The PS5's pad drives as the 8BitDo does: its name and PS in the hint, the stick, Circle halts, PS the menu."""
    port = free_port()
    pad = FakePad()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port, pad=pad)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            pad.state = PadState(**DUALSENSE)                             # plugged in (USB or Bluetooth)
            qtbot.waitUntil(lambda: window.hint.text() == "Press PS for menu", timeout=1000)
            assert window.input.kind == "joystick" and window.drive.note.text() == "DualSense (PS5)"
            pad.state = PadState(**DUALSENSE, rx=1.0)                     # the right stick, full right
            qtbot.waitUntil(lambda: unit.targets[0] == 250, timeout=2000)
            pad.state = PadState(**DUALSENSE, rx=1.0, buttons=frozenset({"b"}))   # Circle: halt
            qtbot.waitUntil(lambda: unit.halted and line(window.drive).startswith("HALTED"), timeout=1000)
            pad.state = PadState(**DUALSENSE)                             # all let go: released
            qtbot.waitUntil(lambda: not unit.halted and line(window.drive) == "PARKED", timeout=2000)
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"guide"}))    # PS: the menu
            qtbot.waitUntil(lambda: ctl.menu.is_open and window.hint.text() == "Press PS to exit menu", timeout=1000)
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"a"}))        # Cross: into Light calib.
            qtbot.waitUntil(lambda: ctl.menu.inside and window.hint.text() == "Circle: back, PS: exit menu",
                            timeout=1000)
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"guide"}))    # PS from inside: all the way out
            qtbot.waitUntil(lambda: not ctl.menu.is_open, timeout=1000)
            halts = len(unit.commands("halt"))
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"guide"}))    # in again, and into the item
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"a"}))
            qtbot.waitUntil(lambda: ctl.menu.inside, timeout=1000)
            push(pad, qtbot, DUALSENSE, buttons=frozenset({"b"}))        # Circle: back to the list
            qtbot.waitUntil(lambda: ctl.menu.is_open and not ctl.menu.inside, timeout=1000)
            pad.state = PadState(**DUALSENSE, buttons=frozenset({"b"}))  # Circle again: out, and still held
            qtbot.waitUntil(lambda: not ctl.menu.is_open, timeout=1000)
            qtbot.wait(200)
            assert len(unit.commands("halt")) == halts and not unit.halted   # it went back; it never halted
            pad.state = PadState(**DUALSENSE)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=1000)
        finally:
            ctl.stop()


def test_limit_in_red_while_pushing_into_it(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            qtbot.keyPress(window, Qt.Key.Key_D)                          # the rail right: MAX is 0.6 mm away
            qtbot.waitUntil(lambda: ctl.client.limits["r"] == "+", timeout=2000)   # told by the unit, unasked
            qtbot.waitUntil(lambda: line(window.drive) == "JOG x 0um/s y 0um/s z 0um/s r LIMIT", timeout=1000)
            assert window.drive._shown[0][-1] == ("LIMIT", "alert")       # in red
            assert unit.targets[3] == 5000 and unit.stage.v[3] == 0       # still asked for; the unit won't
            shot(window, "limit")
            qtbot.keyRelease(window, Qt.Key.Key_D)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=1000)
            qtbot.keyPress(window, Qt.Key.Key_A)                          # away: its speed, and the switch closes
            qtbot.waitUntil(lambda: line(window.drive) == "JOG x 0um/s y 0um/s z 0um/s r -5000um/s", timeout=1000)
            qtbot.waitUntil(lambda: ctl.client.limits["r"] == "0", timeout=1000)
            for k in (Qt.Key.Key_A, Qt.Key.Key_Shift):
                qtbot.keyRelease(window, k)
        finally:
            ctl.stop()


def test_light_calib_from_the_keyboard(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            qtbot.keyPress(window, Qt.Key.Key_Right)                      # driving as the menu opens
            qtbot.waitUntil(lambda: unit.targets[0] > 0, timeout=1000)
            tap(qtbot, window, Qt.Key.Key_Escape)
            qtbot.waitUntil(lambda: line(window.drive) == "DISABLED | menu", timeout=1000)
            assert "halt on" in [e for _, e in unit.events] and unit.targets == STILL   # halted, then nothing
            assert window.main.currentWidget() is window.menu and window.hint.text() == "Press Esc to exit menu"
            assert ctl.menu.chosen.label == "Light calib." and unit.light == {"warm": 0.0, "cool": 0.0}
            qtbot.wait(300)
            assert not ctl.menu.inside                                     # the key that drove counts once let go
            qtbot.keyRelease(window, Qt.Key.Key_Right)
            qtbot.wait(50)                                                 # let go long enough to be seen
            shot(window, "menu")

            tap(qtbot, window, Qt.Key.Key_Right)                          # in: the lights go to its levels
            qtbot.waitUntil(lambda: window.hint.text() == "Backspace: back, Esc: exit menu", timeout=1000)
            assert ctl.menu.lights_on and ctl.menu.row == 0
            for _ in range(2):
                tap(qtbot, window, Qt.Key.Key_Right)                      # fine: 0.1 % a press
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            tap(qtbot, window, Qt.Key.Key_Right)                          # coarse, with Shift: 1 %
            qtbot.waitUntil(lambda: near(unit.light["warm"], 1.2), timeout=1000)
            tap(qtbot, window, Qt.Key.Key_Down)                           # the cool slider
            qtbot.keyPress(window, Qt.Key.Key_Right)                      # held: it repeats
            qtbot.wait(1000)
            qtbot.keyRelease(window, Qt.Key.Key_Right)
            qtbot.keyRelease(window, Qt.Key.Key_Shift)
            cool = ctl.menu.sliders[1].value
            assert 6 <= cool <= 11                                        # 1 at once, then every 80 ms after 0.4 s
            qtbot.waitUntil(lambda: near(unit.light["cool"], cool) and near(ctl.client.lights["cool"], cool),
                            timeout=1000)                                 # set, and said back (!light)
            assert window.menu.light.rows[1][1].text() == f"{cool:.1f} %"
            assert window.menu.light.rows[1][2].value() == round(cool * 10)
            qtbot.waitUntil(lambda: window.menu.light.unit.text().startswith("the unit's lights: warm 1.2 %"),
                            timeout=1000)
            shot(window, "menu_light")

            tap(qtbot, window, Qt.Key.Key_Backspace)                      # back to the list: the lights off
            qtbot.waitUntil(lambda: unit.light == {"warm": 0.0, "cool": 0.0}, timeout=1000)
            assert window.hint.text() == "Press Esc to exit menu" and not ctl.menu.inside and ctl.menu.is_open
            tap(qtbot, window, Qt.Key.Key_Right)                          # in again: as it was
            qtbot.waitUntil(lambda: near(unit.light["cool"], cool) and near(unit.light["warm"], 1.2), timeout=1000)
            tap(qtbot, window, Qt.Key.Key_Escape)                         # Esc from inside: out of the whole menu
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED" and window.main.currentWidget() is window.plots,
                            timeout=1000)
            assert unit.light == {"warm": 0.0, "cool": 0.0} and window.hint.text() == "Press Esc for menu"
            qtbot.keyPress(window, Qt.Key.Key_Up)                         # and the keys drive again
            qtbot.waitUntil(lambda: unit.targets[1] > 0, timeout=1000)
            qtbot.keyRelease(window, Qt.Key.Key_Up)
        finally:
            ctl.stop()


def test_light_calib_from_the_8bitdo(qtbot):
    port = free_port()
    pad = FakePad()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port, pad=pad)
        try:
            pad.state = PadState(**ULTIMATE)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED" and window.input.kind == "joystick", timeout=3000)
            push(pad, qtbot, buttons=frozenset({"guide"}))               # HOME: the menu
            qtbot.waitUntil(lambda: ctl.menu.is_open, timeout=1000)
            push(pad, qtbot, buttons=frozenset({"dpad_down"}))           # the d-pad down: Temp calib.
            qtbot.waitUntil(lambda: ctl.menu.chosen.label == "Temp calib.", timeout=1000)
            push(pad, qtbot, ly=0.9)                                      # the left stick up: Light calib.
            push(pad, qtbot, buttons=frozenset({"a"}))                   # A: in
            qtbot.waitUntil(lambda: ctl.menu.lights_on and window.hint.text() == "B: back, HOME: exit menu", timeout=1000)
            push(pad, qtbot, rx=0.6)                                      # the right stick part way: fine
            push(pad, qtbot, buttons=frozenset({"dpad_right"}))          # the d-pad: coarse
            qtbot.waitUntil(lambda: near(unit.light["warm"], 1.1), timeout=1000)
            halts = len(unit.commands("halt"))
            push(pad, qtbot, buttons=frozenset({"b"}))                   # B: back to the list, the lights off
            qtbot.waitUntil(lambda: unit.light["warm"] == 0 and not ctl.menu.inside and ctl.menu.is_open, timeout=1000)
            assert len(unit.commands("halt")) == halts and window.hint.text() == "Press HOME to exit menu"   # no halt
            push(pad, qtbot, buttons=frozenset({"a"}))                   # in again: the lights back
            qtbot.waitUntil(lambda: near(unit.light["warm"], 1.1), timeout=1000)
            pad.state = PadState(**ULTIMATE, rx=1.0, buttons=frozenset({"guide"}))   # HOME from inside, the stick held
            qtbot.waitUntil(lambda: not ctl.menu.is_open and unit.light["warm"] == 0, timeout=1000)   # all the way out
            pad.state = PadState(**ULTIMATE, rx=1.0)
            qtbot.waitUntil(lambda: line(window.drive).startswith(("HELD", "HALTED")), timeout=1000)
            qtbot.wait(200)
            assert unit.targets == STILL                                  # the stick doesn't drive until let go
            pad.state = PadState(**ULTIMATE)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=1000)
        finally:
            ctl.stop()


def test_quitting_inside_light_calib_switches_them_off(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        stopped = False
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            tap(qtbot, window, Qt.Key.Key_Escape)
            tap(qtbot, window, Qt.Key.Key_Right)
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            tap(qtbot, window, Qt.Key.Key_Right)
            qtbot.keyRelease(window, Qt.Key.Key_Shift)
            qtbot.waitUntil(lambda: near(unit.light["warm"], 1.0), timeout=1000)
            ctl.stop()                                                     # the app quitting from inside
            stopped = True
            assert unit.light == {"warm": 0.0, "cool": 0.0}               # off before the link closed
        finally:
            if not stopped:
                ctl.stop()


def temp_row(window) -> str:
    """Temp calib.'s list of the lid's temperatures, as shown (the means)."""
    return " ".join(QTextDocumentFragment.fromHtml(label.text()).toPlainText()
                    for _, label, _ in window.menu.temp.sensors.values())


def temp_sds(window) -> list[str]:
    """... and their SDs, under them."""
    return [sd.text() for _, _, sd in window.menu.temp.sensors.values()]


def test_temp_calib_from_the_keyboard(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            tap(qtbot, window, Qt.Key.Key_Escape)                         # the menu
            tap(qtbot, window, Qt.Key.Key_Down)                           # Temp calib.
            qtbot.waitUntil(lambda: ctl.menu.chosen.key == "temp", timeout=1000)
            assert window.menu.temp.help.text().startswith("right: in")
            tap(qtbot, window, Qt.Key.Key_Right)                          # in: the switch off, the unit at 0
            qtbot.waitUntil(lambda: ctl.menu.inside and window.hint.text() == "Backspace: back, Esc: exit menu",
                            timeout=1000)
            assert not ctl.menu.heat_on and window.menu.temp.switch.text() == "OFF" and unit.lid.code == 0
            listed = r"A 2\d\.\d\d\W°C B 2\d\.\d\d\W°C edge 2\d\.\d\d\W°C J2 2\d\.\d\d\W°C J3 2\d\.\d\d\W°C"
            qtbot.waitUntil(lambda: bool(re.fullmatch(listed, temp_row(window))), timeout=1000)   # the five, read
            assert all(re.fullmatch(r"sd \d+ mK", t) for t in temp_sds(window))                  # each with its SD
            assert window.menu.temp.limits.text() == "the unit cuts the duty at 60 °C on the board, 40 °C in the medium"
            tap(qtbot, window, Qt.Key.Key_Down)                           # the duty: set first, sent once on
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            for _ in range(4):
                tap(qtbot, window, Qt.Key.Key_Right)                      # coarse: 5 % a press
            qtbot.keyRelease(window, Qt.Key.Key_Shift)
            assert ctl.menu.duty.value == 20 and window.menu.temp.value.text() == "+20 %"
            assert window.menu.temp.slider.value() == 200 and window.menu.temp.slider.maximum() == 500
            qtbot.wait(100)
            assert unit.lid.code == 0                                     # nothing goes while it's off
            tap(qtbot, window, Qt.Key.Key_Up)
            tap(qtbot, window, Qt.Key.Key_Return)                         # Enter on the switch: on
            qtbot.waitUntil(lambda: unit.lid.code == 819 and window.menu.temp.switch.text() == "ON", timeout=1000)
            qtbot.waitUntil(lambda: window.menu.temp.unit.text() == "the lid: heating at 20 %", timeout=1000)
            shot(window, "menu_temp")
            tap(qtbot, window, Qt.Key.Key_Down)
            tap(qtbot, window, Qt.Key.Key_Left)                           # each move goes, as it's made
            qtbot.waitUntil(lambda: unit.lid.code == 778, timeout=1000)   # 19 %
            unit.set_lid_switch(False)                                    # SW10 off: held, the switch stays on
            qtbot.waitUntil(lambda: window.menu.temp.unit.text().startswith("LID HEAT (SW10) is off"), timeout=1000)
            assert ctl.menu.heat_on
            unit.set_lid_switch(True)
            unit.lid_temps(board=61.0)                                    # the board at its limit: the unit cuts it
            qtbot.waitUntil(lambda: not ctl.menu.heat_on and window.menu.temp.switch.text() == "OFF", timeout=2000)
            assert window.menu.temp.unit.text() == "cut by the unit: a board NTC reached 60 °C" and unit.lid.code == 0
            shot(window, "menu_temp_cut")
            tap(qtbot, window, Qt.Key.Key_Up)
            tap(qtbot, window, Qt.Key.Key_Return)                         # on again while it's hot: refused
            qtbot.waitUntil(lambda: not ctl.menu.heat_on and ctl.menu.heat_note.startswith("refused"), timeout=2000)
            assert "hot" in window.menu.temp.unit.text() and unit.lid.code == 0
            unit.lid_temps(board=30.0)
            qtbot.waitUntil(lambda: ctl.client.lid.get("board.a", 99) < 40, timeout=2000)   # the next readout
            tap(qtbot, window, Qt.Key.Key_Return)
            qtbot.waitUntil(lambda: ctl.menu.heat_on and unit.lid.code == 778, timeout=1000)
            tap(qtbot, window, Qt.Key.Key_Backspace)                      # back to the list: off, and 0
            qtbot.waitUntil(lambda: unit.lid.code == 0 and not ctl.menu.inside, timeout=1000)
            assert not ctl.menu.heat_on
            tap(qtbot, window, Qt.Key.Key_Right)                          # in again: the duty kept, the switch off
            qtbot.waitUntil(lambda: ctl.menu.inside, timeout=1000)
            assert ctl.menu.duty.value == 19 and not ctl.menu.heat_on and unit.lid.code == 0
            tap(qtbot, window, Qt.Key.Key_Backspace)                      # back, and back again: out
            tap(qtbot, window, Qt.Key.Key_Backspace)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=1000)
        finally:
            ctl.stop()


def test_temp_calib_from_the_8bitdo(qtbot):
    port = free_port()
    pad = FakePad()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port, pad=pad)
        try:
            pad.state = PadState(**ULTIMATE)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED" and window.input.kind == "joystick", timeout=3000)
            push(pad, qtbot, buttons=frozenset({"guide"}))               # HOME: the menu
            push(pad, qtbot, buttons=frozenset({"dpad_down"}))           # Temp calib.
            push(pad, qtbot, buttons=frozenset({"a"}))                   # A: in
            qtbot.waitUntil(lambda: ctl.menu.inside and ctl.menu.chosen.key == "temp", timeout=1000)
            push(pad, qtbot, ry=-0.6)                                     # the right stick down: the duty
            push(pad, qtbot, buttons=frozenset({"dpad_left"}))           # the d-pad: coarse, -5 %: cooling
            push(pad, qtbot, rx=-0.6)                                     # the stick part way: -1 %
            assert ctl.menu.duty.value == -6
            push(pad, qtbot, ly=0.9)                                      # up: the switch
            push(pad, qtbot, buttons=frozenset({"a"}))                   # A: on
            qtbot.waitUntil(lambda: unit.lid.code == 246 and unit.lid.sign == -1, timeout=1000)
            push(pad, qtbot, buttons=frozenset({"b"}))                   # B: back to the list, the Peltiers off
            qtbot.waitUntil(lambda: unit.lid.code == 0 and not ctl.menu.inside and ctl.menu.is_open, timeout=1000)
            assert not ctl.menu.heat_on and not unit.halted
            push(pad, qtbot, buttons=frozenset({"a"}))                   # in again, on again
            push(pad, qtbot, buttons=frozenset({"a"}))
            qtbot.waitUntil(lambda: unit.lid.code == 246, timeout=1000)
            push(pad, qtbot, buttons=frozenset({"misc1"}))               # STAR from inside: all the way out, off
            qtbot.waitUntil(lambda: unit.lid.code == 0 and not ctl.menu.is_open, timeout=1000)
        finally:
            ctl.stop()


def test_quitting_inside_temp_calib_switches_the_lid_off(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        stopped = False
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            for k in (Qt.Key.Key_Escape, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Down):
                tap(qtbot, window, k)
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            tap(qtbot, window, Qt.Key.Key_Right)
            qtbot.keyRelease(window, Qt.Key.Key_Shift)
            for k in (Qt.Key.Key_Up, Qt.Key.Key_Return):
                tap(qtbot, window, k)
            qtbot.waitUntil(lambda: unit.lid.code == 205, timeout=1000)   # 5 %
            ctl.stop()                                                     # the app quitting from inside
            stopped = True
            assert unit.lid.code == 0 and unit.lid.cut == "0"             # sent 0 before the link closed
        finally:
            if not stopped:
                ctl.stop()


def test_a_dropped_link_turns_temp_calib_off(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            for k in (Qt.Key.Key_Escape, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Right,
                      Qt.Key.Key_Right, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Return):
                tap(qtbot, window, k)
            qtbot.waitUntil(lambda: unit.lid.code == 123, timeout=1000)   # 3 %
            unit.drop("stuck")                                            # the unit ends the session: its duty goes
            qtbot.waitUntil(lambda: not ctl.menu.heat_on, timeout=3000)
            assert unit.lid.code == 0
            qtbot.waitUntil(lambda: ctl.client.connected, timeout=3000)   # back: nothing resumes
            qtbot.wait(300)
            assert unit.lid.code == 0 and not ctl.menu.heat_on
        finally:
            ctl.stop()


def test_focus_lost_lets_go(qtbot):
    port = free_port()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            qtbot.keyPress(window, Qt.Key.Key_Up)
            qtbot.waitUntil(lambda: unit.targets[1] > 0, timeout=2000)
            QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.ApplicationDeactivate))
            qtbot.waitUntil(lambda: unit.targets[1] == 0 and line(window.drive) == "PARKED", timeout=2000)
        finally:
            ctl.stop()


@pytest.mark.skipif(not REVIEW, reason="only to make the review screenshots (MMI_REVIEW_DIR)")
def test_review_screens(qtbot):
    port = free_port()
    pad = FakePad()
    with TestServer("127.0.0.1", port) as unit:
        window, ctl = make(qtbot, port, window_s=30.0, env_hz=10, pad=pad)
        try:
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=3000)
            unit.light.update(warm=35.0, cool=12.5)       # the lights on and the lid warming: something to plot
            unit.lid.code = 2048
            end = time.monotonic() + 31
            while time.monotonic() < end:
                qtbot.wait(200)
            unit.lid.code = 0
            shot(window, "parked")
            qtbot.keyPress(window, Qt.Key.Key_Shift)
            qtbot.keyPress(window, Qt.Key.Key_Up)
            qtbot.keyPress(window, Qt.Key.Key_A)                          # the rail left: MIN is ~109 mm off
            qtbot.waitUntil(lambda: unit.targets[3] < 0, timeout=2000)
            qtbot.wait(300)
            shot(window, "jog_fast")
            for k in (Qt.Key.Key_A, Qt.Key.Key_Up, Qt.Key.Key_Shift):
                qtbot.keyRelease(window, k)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=2000)
            pad.state = PadState(**ULTIMATE)              # the 8BitDo plugged in, then driven
            qtbot.waitUntil(lambda: window.input.kind == "joystick", timeout=1000)
            pad.state = PadState(**ULTIMATE, rx=-0.6, ly=0.4, lt=0.5)
            qtbot.waitUntil(lambda: unit.targets[3] < 0, timeout=2000)
            qtbot.wait(300)
            shot(window, "joystick")
            pad.state = PadState(**ULTIMATE)
            qtbot.waitUntil(lambda: line(window.drive) == "PARKED", timeout=2000)
        finally:
            ctl.stop()
