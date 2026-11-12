"""The controls in use as one controller state, by input profile (profiles.py): the 8BitDo or the DualSense when one's
attached, else the keyboard. Each drives exactly as the others would: the same speed maps, the same halt rules
(hand.py).

The state's buttons are roles: "halt", "menu", "enter" and "back" (with the d-pad's directions, on the pad). On the
pad they're B, HOME, A and B again (the menu being open decides which of halt and back it is); on the keyboard Space,
Esc, Enter and Backspace.

The keyboard's keys, by the names controller.py gives Qt's: the arrows move fine x and y, W/S fine z, A/D the rail (as
the left and right triggers); Shift makes them fast. With the pad attached the keyboard does nothing at all, so the
computer can be used meanwhile (a terminal on the Pi, say) without moving or halting the stage.
"""
from __future__ import annotations

from dataclasses import replace

from .pad import PadState
from .profiles import KEYBOARD, pad_profile

AXES = {"left": ("rx", -1.0), "right": ("rx", 1.0), "up": ("ry", 1.0), "down": ("ry", -1.0),   # as the right stick
        "w": ("ly", 1.0), "s": ("ly", -1.0), "a": ("lt", 1.0), "d": ("rt", 1.0)}                  # the left's vertical
KEYS = set(AXES) | {"shift", "space", "escape", "enter", "backspace"}


def _clip(v: float) -> float:
    return max(-1.0, min(1.0, v))


class Keyboard:
    """The keys held now. The window feeds it presses and releases; clear() lets go of every key (focus lost)."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    def press(self, name: str) -> None:
        if name in KEYS:
            self.held.add(name)

    def release(self, name: str) -> None:
        self.held.discard(name)

    def clear(self) -> None:
        self.held.clear()

    def state(self, slow: float, fast: float) -> PadState:
        k = fast if "shift" in self.held else slow
        v = dict.fromkeys(("rx", "ry", "ly", "lt", "rt"), 0.0)
        for name in self.held:
            if name in AXES:
                axis, sign = AXES[name]
                v[axis] += sign * k
        buttons = {"halt"} if "space" in self.held else set()
        if "escape" in self.held:
            buttons.add("menu")
        if "enter" in self.held:
            buttons.add("enter")
        if "backspace" in self.held:
            buttons.add("back")
        return PadState(connected=True, name=KEYBOARD.name, rx=_clip(v["rx"]), ry=_clip(v["ry"]), ly=_clip(v["ly"]),
                        lt=_clip(v["lt"]), rt=_clip(v["rt"]), buttons=frozenset(buttons))


class Inputs:
    """poll(): the controls of the profile in use. It's always connected, since the keyboard is always there:
    unplugging the pad hands over to the keyboard. `profile` is the profile in use, `pad_state` the pad's own state
    at the last poll (its device name, its buttons by SDL's names), and `ignored` the gamepads attached that the app
    doesn't take."""

    def __init__(self, keyboard: Keyboard, pad=None, *, slow: float = 0.5, fast: float = 1.0):
        self.keyboard, self.pad = keyboard, pad
        self.slow, self.fast = slow, fast
        self.profile = KEYBOARD
        self.pad_state = PadState()

    @property
    def ignored(self) -> tuple:
        """The gamepads attached that the app doesn't take, as (name, USB ids)."""
        return tuple(getattr(self.pad, "ignored", ()))

    def poll(self) -> PadState:
        p = self.pad.poll() if self.pad is not None else PadState()
        self.pad_state = p
        profile = pad_profile(p.ids) if p.connected else None
        if profile is None:   # no pad, or not one the app takes
            self.profile = KEYBOARD
            return self.keyboard.state(self.slow, self.fast)
        self.profile = profile
        return replace(p, name=profile.name, buttons=profile.roles(p.buttons))
