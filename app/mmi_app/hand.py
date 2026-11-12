"""The hand controls to the drive: a controller state (the pad, the keyboard) to jogs, and the halt button to a halt.

Halt, as the operator sees it: the button stops everything at once. Nothing moves again while it's held, nor until
every stick, trigger and key has been let go; the next deflection after that moves normally. The client library adds
the rest: the halt goes out ahead of any jog, and after a reconnect or MOTORS coming on nothing moves until the
controls have been let go either.

A handover from one device to another (the pad plugged in or out: the state's name changes) stops the jog, and nothing
moves until the new device's controls are at rest: a key held, or a trigger pressed as the pad goes in, doesn't move
the stage by itself.

While the menu is open (menu.py) the controls move the menu, not the stage: `step(drive=False)` sends only zeros, and
once driving resumes nothing moves until the controls have been let go. The keyboard's halt key still halts there. A
pad's halt button is its back button in the menu (the roles carry both), so it doesn't halt, nor if it's still held
as the menu closes behind it.
"""
from __future__ import annotations

from typing import Protocol

from mmi_client import ZERO, MmiClient, MmiDisconnected

from .pad import PadState
from .shaping import Mapping, at_rest, pad_to_jog


class PadSource(Protocol):
    def poll(self) -> PadState: ...


class HandControl:
    def __init__(self, client: MmiClient, pad: PadSource, mapping: Mapping, halt_button: str = "halt"):
        self.client, self.pad, self.mapping, self.halt_button = client, pad, mapping, halt_button
        self.last_pad = PadState()
        self.vector = ZERO
        self.holding = False        # after a halt: until the button and every control are let go
        self.settling = False       # after a handover: until the new device's controls are let go
        self.halts = 0
        self._halt_down = False
        self._pad_was_up = False
        self._source: str | None = None   # the device driving: the state's name

    def step(self, drive: bool = True) -> PadState:
        """One pass, from a timer: read the controls, halt first if the button went down, then the jog. With drive
        False (the menu has the controls) nothing moves. Returns the controls as read."""
        pad = self.pad.poll()
        self.last_pad = pad
        if not pad.connected:
            if self._pad_was_up:   # unplugged mid-move: stop now, don't wait for the lease
                self.vector = ZERO
                self.client.jog()
            self._pad_was_up = self._halt_down = False
            return pad
        self._pad_was_up = True
        if pad.name != self._source:
            if self._source is not None:
                self.settling = True
                self._halt_down = False
            self._source = pad.name
        down = self.halt_button in pad.buttons
        going_back = not drive and "back" in pad.buttons   # the menu's back, on the same button: no halt
        if down and not self._halt_down and not going_back:
            self.halts += 1
            self.holding = True
            try:
                self.client.halt()
            except MmiDisconnected:
                pass   # with the link down the unit has stopped by itself
        self._halt_down = down
        if not drive:
            if self.holding and not down and at_rest(pad, self.mapping):
                self.holding = False
            self.vector = ZERO
            self.client.jog()
            self.settling = True   # when driving resumes: nothing until let go
            return pad
        if self.holding:
            if down or not at_rest(pad, self.mapping):
                return pad
            self.holding = False
        if self.settling:
            if not at_rest(pad, self.mapping):
                self.vector = ZERO
                self.client.jog()
                return pad
            self.settling = False
        self.vector = pad_to_jog(pad, self.mapping)
        self.client.jog(*self.vector)
        return pad

    def stop(self) -> None:
        """The menu taking the controls: if the drive is moving, or being asked to, a halt now, as the button's (and
        held as one); otherwise nothing more than a zero."""
        if self.vector != ZERO or self.client.sent_vector != ZERO:
            self.holding = True
            try:
                self.client.halt()
            except MmiDisconnected:
                pass   # with the link down the unit has stopped by itself
        self.vector = ZERO
        self.client.jog()
