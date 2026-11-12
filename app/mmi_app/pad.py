"""The gamepad: its state, normalised, and a reader for the controllers SDL2 knows (through pygame).

SDL's game-controller layer names the inputs the same way on every platform (left stick, triggers, buttons a, b, ...),
whatever the device's own layout. A pad is a USB HID device, not a serial port: SDL finds it, and picks up plugging
and unplugging, on the Mac (IOKit) and on the Pi (evdev) alike. It runs headless: no window, no display.

Each controller is known by its USB ids, (vendor, product), from SDL's GUID. The reader takes only the pads it's told
to (profiles.py: the 8BitDo Ultimate 2C and the DualSense), and lists the others it ignores.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

AXIS_FULL = 32767.0

# SDL's game-controller buttons, lower case: the 15 every mapping has (pygame CONTROLLER_BUTTON_*), then the extras
# some pads have, by SDL's numbers since pygame doesn't name them: misc1 (a share, capture or similar button), the four
# paddles (back buttons, e.g. L4/R4) and the touchpad.
BUTTON_NAMES = ("a", "b", "x", "y", "back", "guide", "start", "leftstick", "rightstick", "leftshoulder",
                "rightshoulder", "dpad_up", "dpad_down", "dpad_left", "dpad_right",
                "misc1", "paddle1", "paddle2", "paddle3", "paddle4", "touchpad")
EXTRA_BUTTONS = {"misc1": 15, "paddle1": 16, "paddle2": 17, "paddle3": 18, "paddle4": 19, "touchpad": 20}


@dataclass(frozen=True)
class PadState:
    connected: bool = False
    name: str = ""
    lx: float = 0.0    # left stick, -1..1; ly: + = pushed up (away from the operator)
    ly: float = 0.0
    rx: float = 0.0    # right stick; ry: + = pushed up
    ry: float = 0.0
    lt: float = 0.0    # triggers, 0..1
    rt: float = 0.0
    buttons: frozenset = field(default_factory=frozenset)   # the names of the buttons held down
    ids: tuple = (0, 0)                                      # the device's USB (vendor, product)


def _axis(raw: int) -> float:
    return max(-1.0, min(1.0, raw / AXIS_FULL))


def guid_ids(guid: str) -> tuple[int, int]:
    """(vendor, product) from an SDL joystick GUID, or (0, 0) for a device without them. The standard form, as SDL
    reads it (SDL_GetJoystickGUIDInfo): 16-bit little-endian bus, CRC, vendor, 0, product, 0, version, driver."""
    try:
        b = bytes.fromhex(guid)
    except ValueError:
        return 0, 0
    if len(b) != 16:
        return 0, 0
    w = [int.from_bytes(b[i:i + 2], "little") for i in range(0, 16, 2)]
    if (w[0] < 0x20 or w[0] == 0xFF) and w[3] == 0 and w[5] == 0:
        return w[2], w[4]
    return 0, 0


def fmt_ids(ids: tuple[int, int]) -> str:
    return f"{ids[0]:04x}:{ids[1]:04x}"


class SdlGamepad:
    """Reads the first game controller attached whose USB ids are in `accept` (any, if None), and picks up
    hot-plugging. Call poll() regularly from one thread (the main thread on a Mac).

    `devices` is every joystick SDL sees, as (name, ids, mapped as a game controller); `ignored` the game controllers
    attached that it doesn't take, as (name, ids)."""

    _DEVICE_EVENTS = ("JOYDEVICEADDED", "JOYDEVICEREMOVED", "CONTROLLERDEVICEADDED", "CONTROLLERDEVICEREMOVED",
                      "CONTROLLERDEVICEREMAPPED")

    def __init__(self, accept=None) -> None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")                    # no window, works headless
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")   # read it without a focused window
        os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")   # else SDL takes SIGTERM for its own quit event
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        from pygame._sdl2 import controller

        self._pg, self._ctl = pygame, controller
        pygame.display.init()   # the event queue needs the video subsystem (the dummy driver here)
        pygame.joystick.init()
        controller.init()
        self.accept = None if accept is None else frozenset(accept)
        self.devices: list[tuple[str, tuple[int, int], bool]] = []
        self.ignored: tuple[tuple[str, tuple[int, int]], ...] = ()
        self._pad = None
        self._ids = (0, 0)
        self._count = -1          # joysticks at the last scan
        self._dirty = True        # a device came or went: scan again
        self._events = {getattr(pygame, n) for n in self._DEVICE_EVENTS if hasattr(pygame, n)}
        self._axes = {name: getattr(pygame, "CONTROLLER_AXIS_" + name.upper()) for name in
                      ("leftx", "lefty", "rightx", "righty", "triggerleft", "triggerright")}
        self._buttons = {name: getattr(pygame, "CONTROLLER_BUTTON_" + name.upper(), EXTRA_BUTTONS.get(name))
                         for name in BUTTON_NAMES}

    def _takes(self, ids: tuple[int, int]) -> bool:
        return self.accept is None or ids in self.accept

    def _scan(self) -> None:
        """Who's attached, by name and USB ids; opens the first pad it takes if none is open. Only on a change, since
        opening a device to read its ids isn't free."""
        pg, ctl = self._pg, self._ctl
        self._dirty = False
        self._count = ctl.get_count()
        devices, pick = [], None
        for i in range(self._count):
            try:
                j = pg.joystick.Joystick(i)
                name, ids = j.get_name(), guid_ids(j.get_guid())
                mapped = ctl.is_controller(i)
            except pg.error:   # gone meanwhile
                continue
            devices.append((name, ids, mapped))
            if self._pad is None and pick is None and mapped and self._takes(ids):
                pick = (i, ids)
        self.devices = devices
        self.ignored = tuple((n, ids) for n, ids, mapped in devices if mapped and not self._takes(ids))
        if pick is not None:
            self._open(*pick)

    def _open(self, index: int, ids: tuple[int, int]) -> None:
        try:
            self._pad = self._ctl.Controller(index)
        except self._pg.error:
            self._dirty = True
            return
        self._ids = ids
        for name, b in list(self._buttons.items()):   # an SDL older than the extras refuses their numbers
            try:
                self._pad.get_button(b)
            except Exception:
                del self._buttons[name]

    def _drop(self) -> None:
        try:
            self._pad.quit()
        except Exception:
            pass
        self._pad, self._ids = None, (0, 0)
        self._dirty = True

    def poll(self) -> PadState:
        pg = self._pg
        for ev in pg.event.get():   # plugging and unplugging come through the event queue
            if ev.type in self._events:
                self._dirty = True
        if self._pad is not None and not self._pad.attached():
            self._drop()
        if self._dirty or self._ctl.get_count() != self._count:
            self._scan()
        if self._pad is None:
            return PadState()
        p, a = self._pad, self._axes
        return PadState(
            connected=True,
            name=p.name,
            lx=_axis(p.get_axis(a["leftx"])),
            ly=-_axis(p.get_axis(a["lefty"])),       # SDL: down is +
            rx=_axis(p.get_axis(a["rightx"])),
            ry=-_axis(p.get_axis(a["righty"])),
            lt=max(0.0, _axis(p.get_axis(a["triggerleft"]))),
            rt=max(0.0, _axis(p.get_axis(a["triggerright"]))),
            buttons=frozenset(n for n, b in self._buttons.items() if p.get_button(b)),
            ids=self._ids,
        )

    def close(self) -> None:
        if self._pad is not None:
            self._drop()
        self._ctl.quit()
        self._pg.quit()
