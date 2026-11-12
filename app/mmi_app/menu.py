"""The menu: what the controls move instead of the stage while it's open. As the user's sketch has it, the items in a
column on the left and the chosen one's page on the right.

- **Opening it** (HOME on the 8BitDo, PS on the DualSense, Esc on the keyboard) stops the drive, with a halt if it's
  moving, and nothing drives while it's open. The same button closes it again, from wherever it is. The back button
  (B on the 8BitDo, Circle on the DualSense, Backspace) goes back a level: from inside an item to the list, then out
  of the menu. After that nothing moves until the controls have been let go (hand.py).
- **Moving about** (nav.py): up and down choose; right (or A / Enter) goes into the chosen item. Inside, up and down
  choose a row, left and right move its slider, fine or coarse, and A / Enter presses its switch.
- **Light calib.** sets the two channels by hand (api.md 9.1). It's the first step of the light calibration: set a
  level, measure it.
  - Going in switches the lights to its sliders' levels, and each move goes to the unit as it's made.
  - Coming out switches them off, whichever way it happens: back, the menu closed, or the app quitting.
  - The levels are kept for next time.
- **Temp calib.** drives the lid's Peltiers by hand (api.md 9.2); its page lists the lid's five temperatures, each
  the mean of the unit's readout and its SD, as they come (once a second).
  - **A switch above the duty slider** has to be turned on (A / Enter) before any duty goes out. Going in turns it
    off, and the unit's duty with it.
  - On, the slider's duty goes to the unit, and each move as it's made: -lid.duty_max (cooling) to +lid.duty_max
    (heating), 1 % a move, 5 % coarse.
  - Coming out turns it off and sends 0, whichever way it happens. So does the unit cutting the duty or refusing it
    (sync(): a board NTC at 60 °C, a medium probe at lid.medium_max, a sensor reading wrong ...), and the page says
    why.
  - The duty is kept for next time; the switch never is.
"""
from __future__ import annotations

from dataclasses import dataclass

FINE, COARSE = 0.1, 1.0              # % a move on Light calib.'s sliders: a fine one, a coarse one
DUTY_FINE, DUTY_COARSE = 1.0, 5.0    # ... on Temp calib.'s duty
DUTY_MAX = 50.0                      # lid.duty_max's default, until the unit has said its own
MEDIUM_MAX = 40.0                    # lid.medium_max's, likewise
FLOOR = 2.0                          # %: the unit sets a duty under this to 0


@dataclass(frozen=True)
class Item:
    key: str
    label: str


@dataclass
class Slider:
    key: str              # the light channel ("warm", "cool"), or "duty"
    label: str
    value: float = 0.0    # %


ITEMS = (Item("light", "Light calib."), Item("temp", "Temp calib."))
SLIDERS = (("warm", "2700K (warm)"), ("cool", "6500K (cool)"))   # Light calib.'s, by channel
CUTS = {"hot": "a board NTC reached 60 °C", "medium": "a medium probe reached {medium_max:g} °C (lid.medium_max)",
        "fault": "a sensor reads wrong", "absent": "the lid stopped answering", "closed": "the session closed",
        "link": "the link dropped", "replaced": "another client took the unit", "bye": "the session ended",
        "stuck": "the link stalled"}   # the unit's cut= reasons (api.md 9.2)


class Menu:
    """The menu's state. Its moves (nav.Move) come from handle(); `client` is an MmiClient, whose light() and
    lid_duty() never wait (a mailbox), so this runs in the window's own thread."""

    def __init__(self, client):
        self.client = client
        self.is_open = False
        self.inside = False   # in the chosen item, not the list
        self.item = 0         # the chosen item (ITEMS)
        self.row = 0          # inside it: Light calib.'s slider; Temp calib.'s row (0 the switch, 1 the duty)
        self.sliders = [Slider(key, label) for key, label in SLIDERS]
        self.duty = Slider("duty", "Duty (+ heats, - cools)")
        self.heat_on = False  # Temp calib.'s switch
        self.heat_note = ""   # why it went off by itself (the unit cut or refused the duty), or couldn't go on

    @property
    def chosen(self) -> Item:
        return ITEMS[self.item]

    @property
    def lights_on(self) -> bool:
        """The lights are the calibration's: inside Light calib."""
        return self.is_open and self.inside and self.chosen.key == "light"

    @property
    def duty_max(self) -> float:
        """The unit's cap on a duty, either way (lid.duty_max, read on connecting)."""
        v = self.client.params.get("lid.duty_max", DUTY_MAX)
        return float(v) if isinstance(v, (int, float)) else DUTY_MAX

    @property
    def medium_max(self) -> float:
        """The medium temperature at which the unit cuts the duty (lid.medium_max, read on connecting)."""
        v = self.client.params.get("lid.medium_max", MEDIUM_MAX)
        return float(v) if isinstance(v, (int, float)) else MEDIUM_MAX

    def open(self) -> None:
        self.is_open, self.inside = True, False

    def back(self) -> bool:
        """The back button: out of an item to the list, or out of the menu from the list. True once closed."""
        if self.inside:
            self._leave()
            return False
        self.is_open = False
        return True

    def close(self) -> None:
        """Out of the menu from wherever it is (its button, or the app quitting): an item is left as by going back."""
        if self.inside:
            self._leave()
        self.is_open = False

    def handle(self, move) -> None:
        d = move.direction
        if not self.inside:
            if d == "up":
                self.item = max(0, self.item - 1)
            elif d == "down":
                self.item = min(len(ITEMS) - 1, self.item + 1)
            elif d in ("right", "enter"):
                self._enter()
            return
        if self.chosen.key == "light":
            self._handle_light(move)
        else:
            self._handle_temp(move)

    def sync(self) -> bool:
        """Temp calib.'s switch against what the unit has: off, and sending 0, once the unit has cut the duty, refused
        it, holds another (set elsewhere, or restarted), or the link has gone (the unit drops a duty with its
        session). Called on every pass of the input timer; True if it turned the switch off."""
        if not self.heat_on:
            return False
        c = self.client
        if not c.connected:
            return self._heat_off(f"off: {CUTS['link']}")
        if c.set_pending("lid"):
            return False                  # the reply isn't back: the mirror doesn't speak of it yet
        if c.set_errors.get("lid"):
            return self._heat_off(f"refused: {c.set_errors['lid']}")
        unit = c.lid
        if "duty" not in unit:
            return False                  # a new session's state is on its way
        cut = str(unit.get("cut", "0"))
        if cut != "0":
            return self._heat_off(f"cut by the unit: {CUTS.get(cut, cut).format(medium_max=self.medium_max)}")
        if abs(float(unit["duty"]) - self._asked()) > 0.05:
            return self._heat_off("off: the unit's duty changed (set elsewhere, or it restarted)")
        return False

    def reapply(self) -> None:
        """The link is back: the unit may have restarted (dark), so the lights are set again if they're ours. A lid
        duty isn't: the unit dropped it with the old session, and sync() turns the switch off."""
        if self.lights_on:
            self._send_all()

    def _handle_light(self, move) -> None:
        d = move.direction
        if d == "up":
            self.row = max(0, self.row - 1)
        elif d == "down":
            self.row = min(len(self.sliders) - 1, self.row + 1)
        elif d in ("left", "right"):
            s = self.sliders[self.row]
            step = (COARSE if move.coarse else FINE) * (1 if d == "right" else -1)
            v = min(100.0, max(0.0, round(s.value + step, 1)))
            if v != s.value:
                s.value = v
                self.client.light(**{s.key: v})

    def _handle_temp(self, move) -> None:
        d = move.direction
        if d == "up":
            self.row = 0
        elif d == "down":
            self.row = 1
        elif d == "enter" and self.row == 0:
            self._switch(not self.heat_on)
        elif d in ("left", "right") and self.row == 1:
            cap = self.duty_max
            step = (DUTY_COARSE if move.coarse else DUTY_FINE) * (1 if d == "right" else -1)
            v = min(cap, max(-cap, round(self.duty.value + step, 1)))
            if v != self.duty.value:
                self.duty.value = v
                if self.heat_on:
                    self.client.lid_duty(v)

    def _switch(self, on: bool) -> None:
        if on and not self.client.connected:
            self.heat_note = "not connected: the switch stays off"
            return
        cap = self.duty_max
        self.duty.value = min(cap, max(-cap, self.duty.value))   # a unit with a lower cap than the last
        self.heat_on, self.heat_note = on, ""
        self.client.lid_duty(self.duty.value if on else 0)

    def _heat_off(self, note: str) -> bool:
        self.heat_on, self.heat_note = False, note
        self.client.lid_duty(0)
        return True

    def _asked(self) -> float:
        """What the unit should be holding while the switch is on: the slider's duty, under the floor none."""
        return self.duty.value if abs(self.duty.value) >= FLOOR else 0.0

    def _enter(self) -> None:
        self.inside, self.row = True, 0   # at the top: the first slider, or the switch
        if self.chosen.key == "light":
            self._send_all()
        else:                 # the switch off, and the unit's duty with it, whatever it was
            self.heat_on, self.heat_note = False, ""
            self.client.lid_duty(0)

    def _leave(self) -> None:
        self.inside = False
        if self.chosen.key == "light":
            self.client.light(warm=0, cool=0)
        else:
            self.heat_on, self.heat_note = False, ""
            self.client.lid_duty(0)

    def _send_all(self) -> None:
        self.client.light(**{s.key: s.value for s in self.sliders})
