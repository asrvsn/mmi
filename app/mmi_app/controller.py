"""The glue: the client, the controls and the window, on two timers in Qt's main thread.

- **The input timer** (every 10 ms) reads the controls of the input profile in use (a pad of profiles.py, else the
  keyboard: inputs.py) and steps the hand controls: halt first, then `jog()`. A jog on every tick keeps the client's
  deadman fed, so a stuck window halts the unit.
- **The menu button** opens the menu (menu.py), stopping the drive first, and with it open closes it from wherever
  it is. While it's open the controls move the menu (nav.py), the back button goes back a level (out of an item,
  then out of the menu), and the hand controls send only zeros. The menu is redrawn as it moves, and Temp calib.'s
  switch is checked against the unit's lid on every pass (menu.sync()).
- **The view timer** (every 100 ms) writes the two lines from the client's state and the profile's hint and icon, and
  moves new samples into the plots.
- **The client's callbacks** run on its reader thread: they only append to a queue the view timer empties. No widget
  is touched off the main thread, and nothing here waits for a reply from the unit.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from mmi_client import MmiClient

from .config import Config
from .hand import HandControl
from .inputs import Inputs, Keyboard
from .menu import Menu
from .nav import Navigator
from .pad import fmt_ids
from .profiles import PADS
from .texts import drive_line, unit_line

log = logging.getLogger("mmi_app")

KEY_NAMES = {Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
             Qt.Key.Key_W: "w", Qt.Key.Key_S: "s", Qt.Key.Key_A: "a", Qt.Key.Key_D: "d", Qt.Key.Key_Shift: "shift",
             Qt.Key.Key_Space: "space", Qt.Key.Key_Escape: "escape", Qt.Key.Key_Return: "enter",
             Qt.Key.Key_Enter: "enter", Qt.Key.Key_Backspace: "backspace"}


class Controller(QObject):
    def __init__(self, window, client: MmiClient, cfg: Config, pad=None):
        super().__init__()
        self.window, self.client, self.cfg, self.pad = window, client, cfg, pad
        self.keyboard = Keyboard()
        self.inputs = Inputs(self.keyboard, pad, slow=cfg.slow, fast=cfg.fast)
        self.hand = HandControl(client, self.inputs, cfg.mapping, "halt")
        self.menu = Menu(client)
        self.nav = Navigator()
        self.samples: deque = deque(maxlen=100_000)   # (time, topic, values), from the reader thread
        self.menu_presses = 0
        self._menu_down = False
        self._back_down = False   # tracked with the menu shut too: a press only counts from inside it
        self._reapply = False   # the link is back (the client's thread): the menu's lights set again
        self._drawn = 0.0
        self._profile = None    # the profile last shown, and the gamepads ignored, for the log
        self._ignored: tuple = ()
        client.on_event(self._on_event)
        client.on_connect(self._on_connect)
        self.input_timer = QTimer(self)
        self.input_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.input_timer.timeout.connect(self._input_tick)
        self.view_timer = QTimer(self)
        self.view_timer.timeout.connect(self._view_tick)
        QApplication.instance().installEventFilter(self)

    def start(self) -> None:
        self.client.start()   # in the background: the window runs with or without the unit
        self.input_timer.start(max(1, round(1000 / self.cfg.poll_hz)))
        self.view_timer.start(100)
        self._input_tick()    # the controls read once, so the first frame shows the profile in use
        self._view_tick()

    def stop(self) -> None:
        self.input_timer.stop()
        self.view_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.menu.close()     # the calibration's lights off, if they were on: close() sends it before it closes
        self.client.jog()     # let go: close() then sends the zero before it closes
        self.client.close()
        if self.pad is not None:
            self.pad.close()

    # ---- the client's threads: record only
    def _on_event(self, ev) -> None:
        if ev.name in ("light", "lid"):
            self.samples.append((time.time(), ev.name, dict(ev.values)))

    def _on_connect(self, c) -> None:
        self.cfg.mapping.cap(c.params)   # full deflection never asks more than its vmax
        self._reapply = True

    # ---- the main thread
    def _input_tick(self) -> None:
        menu = self.menu
        pad = self.hand.step(drive=not menu.is_open)
        down, back = "menu" in pad.buttons, "back" in pad.buttons
        changed = False
        if down and not self._menu_down:
            self.menu_presses += 1
            changed = True
            if not menu.is_open:
                self.hand.stop()          # halt any drive, then the menu
                menu.open()
                self.nav.reset(pad)       # what's held now (the stick that was driving) counts once let go
                log.info("menu: open")
            else:
                menu.close()              # from wherever it is; nothing drives until the controls are let go
                log.info("menu: closed")
        elif back and not self._back_down and menu.is_open:
            changed = True
            if menu.back():
                log.info("menu: closed")
        self._menu_down, self._back_down = down, back
        if menu.is_open:
            for move in self.nav.moves(pad, time.monotonic()):
                menu.handle(move)
                changed = True
        if menu.sync():
            changed = True
            log.info("temp calib.: the Peltiers off (%s)", menu.heat_note)
        if self._reapply:
            self._reapply = False
            menu.reapply()
        if changed:
            self._draw_menu()

    def _draw_menu(self) -> None:
        w, menu = self.window, self.menu
        w.show_menu(menu.is_open)
        if menu.is_open:
            w.menu.show(menu, self.client)
        p = self.inputs.profile
        w.hint.set_text(p.menu_hint(menu.inside) if menu.is_open else p.hint)

    def _view_tick(self) -> None:
        profile, ignored = self.inputs.profile, self.inputs.ignored
        if profile is not self._profile:
            log.info("input: %s", profile.name)
            self._profile = profile
        if ignored != self._ignored:
            for name, ids in ignored:
                if (name, ids) not in self._ignored:
                    log.warning("ignoring the gamepad %r (USB %s): the app takes only the %s", name, fmt_ids(ids),
                                ", ".join(p.name for p in PADS))
            self._ignored = ignored
        self.window.input.set_kind(profile.icon)
        self._draw_menu()
        note = profile.name + ("; other pad ignored" if ignored and profile.icon == "keyboard" else "")
        self.window.unit.set(unit_line(self.client))
        self.window.drive.set(drive_line(self.client, self.hand.holding, self.hand.settling, self.menu.is_open),
                              note)
        fresh = bool(self.samples)
        while self.samples:
            t, topic, values = self.samples.popleft()
            self.window.plots.add(t, topic, values)
        now = time.monotonic()
        if fresh or now - self._drawn >= 1.0:   # new samples, or the time axis moving on without any
            self._drawn = now
            self.window.plots.redraw()

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            name = KEY_NAMES.get(ev.key())
            if name is None:
                return False
            if not ev.isAutoRepeat():
                (self.keyboard.press if t == QEvent.Type.KeyPress else self.keyboard.release)(name)
            return True
        # The focus gone elsewhere: a key held meanwhile mustn't stick. Only the window's own deactivation counts:
        # pyqtgraph's items each get one whenever the plots are hidden, as for the menu.
        if t == QEvent.Type.ApplicationDeactivate or (t == QEvent.Type.WindowDeactivate and obj is self.window):
            self.keyboard.clear()
        return False
