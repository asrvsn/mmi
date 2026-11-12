"""The input profiles: which device drives the app, and what its buttons are.

- **The gamepads the app takes,** each known by its USB ids, which SDL reports the same on the Mac and the Pi, by USB
  or Bluetooth. They drive alike: the same sticks and triggers, and the same buttons by position.
  - **The east face button** halts. With the menu open it goes back instead: out of an item to the list, or out of
    the menu from the list. It doesn't halt there (nothing drives while the menu is open).
  - **The centre button** (SDL's `guide`) opens the menu, and with it open closes it, from wherever it is.
  - **In the menu** the south face button enters, and the d-pad points, as the sticks do.
  - **The 8BitDo Ultimate 2C** (wired): vendor 2dc8, product 310a. B halts and goes back, HOME is the menu (and
    STAR, which SDL calls `misc1`), A enters.
  - **Sony's DualSense** (the PS5's pad) or DualSense Edge: vendor 054c, product 0ce6 or 0df2. Circle halts and goes
    back, the PS button is the menu, Cross enters.
- **The keyboard** drives whenever none of those is attached: Space halts (in the menu too), Esc is the menu,
  Backspace goes back, Enter enters. Any other gamepad is ignored, and the window says so. With two of the app's pads
  attached, the first SDL lists drives.

With the pad attached, the keyboard does nothing at all, not even halt: the computer stays usable (a terminal on the
Pi, say) while the app runs. When one profile hands over to the other, nothing moves until the new one's controls are
let go (hand.py).
"""
from __future__ import annotations

from dataclasses import dataclass


DPAD = (("up", "dpad_up"), ("down", "dpad_down"), ("left", "dpad_left"), ("right", "dpad_right"))


@dataclass(frozen=True)
class Profile:
    name: str                       # what the window calls it
    icon: str                       # "joystick" or "keyboard": the window's top-right corner
    menu_label: str                 # the hint bar: Press <menu_label> for menu
    back_label: str                 # ... and, inside a menu item, <back_label>: back
    ids: frozenset = frozenset()    # a pad's (vendor, product) USB ids
    # A pad's buttons for the roles, by SDL's names (pad.BUTTON_NAMES); the keyboard's come from inputs.Keyboard.
    halt_button: str = ""
    menu_buttons: tuple = ()        # any of them is the menu button
    enter_button: str = ""
    back_button: str = ""           # the pads': the halt button, which only goes back with the menu open
    dpad: bool = False              # its d-pad points in the menu ("up", "down", "left", "right")

    @property
    def hint(self) -> str:
        return f"Press {self.menu_label} for menu"

    def menu_hint(self, inside: bool) -> str:
        """The hint bar with the menu open: inside an item, or in the list."""
        if inside:
            return f"{self.back_label}: back, {self.menu_label}: exit menu"
        return f"Press {self.menu_label} to exit menu"

    def roles(self, buttons: frozenset) -> frozenset:
        """A pad's buttons held, as the roles they play: "halt", "menu", "enter", "back", and the d-pad's directions.
        One button plays two on the pads, "halt" and "back": whether the menu is open decides which counts (hand.py
        and controller.py)."""
        pairs = [("halt", self.halt_button), ("enter", self.enter_button), ("back", self.back_button)]
        pairs += [("menu", b) for b in self.menu_buttons]
        pairs += DPAD if self.dpad else ()
        return frozenset(r for r, b in pairs if b and b in buttons)


EIGHTBITDO = Profile("8BitDo Ultimate 2C", "joystick", "HOME", "B", frozenset({(0x2DC8, 0x310A)}), halt_button="b",
                     menu_buttons=("guide", "misc1"), enter_button="a", back_button="b", dpad=True)   # misc1: STAR
DUALSENSE = Profile("DualSense (PS5)", "joystick", "PS", "Circle", frozenset({(0x054C, 0x0CE6), (0x054C, 0x0DF2)}),
                    halt_button="b", menu_buttons=("guide",), enter_button="a", back_button="b",
                    dpad=True)   # SDL's b, guide and a: Circle, PS and Cross
KEYBOARD = Profile("keyboard", "keyboard", "Esc", "Backspace")
PADS = (EIGHTBITDO, DUALSENSE)


def pad_profile(ids: tuple[int, int]) -> Profile | None:
    """The profile for a pad with these USB ids, or None: a pad the app doesn't take."""
    return next((p for p in PADS if ids in p.ids), None)


def pad_ids() -> frozenset:
    """The USB ids of every pad the app takes, for the reader (pad.SdlGamepad), which ignores all others."""
    return frozenset().union(*(p.ids for p in PADS))
