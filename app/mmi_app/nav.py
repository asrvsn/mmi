"""The controls as a TV remote's: up, down, left, right and enter, for moving about the menu (menu.py).

- **Any control that points will do:** either stick, the d-pad, the arrow keys (W / S as well). Whichever points
  furthest counts, along its main direction.
- **A press moves once; held, it repeats** after `delay`, every `period`, as a remote's does.
- **Fine or coarse:** pointed part way, a move is fine; all the way (the d-pad, or Shift with the keys), coarse. The
  sliders step by it.
- **Whatever is held as the menu opens counts only once let go,** so the stick that was driving doesn't move the menu.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pad import PadState

ON, OFF = 0.4, 0.25    # pointed this far counts; back under this, it's let go (so a wobble doesn't repeat it)
COARSE = 0.75          # pointed this far (the d-pad, Shift with the keys): a coarse move


@dataclass(frozen=True)
class Move:
    direction: str          # "up", "down", "left", "right" or "enter"
    coarse: bool = False


def pointing(pad: PadState) -> tuple[str | None, float]:
    """Where the controls point, and how far: the furthest of the sticks and the d-pad (the keys come as the right
    stick and the left one's vertical), along its main direction."""
    b = pad.buttons
    v = max((pad.ry, pad.ly, 1.0 if "up" in b else 0.0, -1.0 if "down" in b else 0.0), key=abs)
    h = max((pad.rx, pad.lx, 1.0 if "right" in b else 0.0, -1.0 if "left" in b else 0.0), key=abs)
    if abs(v) >= abs(h):
        return (("up" if v > 0 else "down") if v else None), abs(v)
    return ("right" if h > 0 else "left"), abs(h)


class Navigator:
    def __init__(self, delay: float = 0.4, period: float = 0.08):
        self.delay, self.period = delay, period
        self._held: str | None = None    # the direction held, moving
        self._next = 0.0                 # when it repeats
        self._enter = False              # enter down
        self._blocked = False            # held since reset(): nothing until it's let go

    def reset(self, pad: PadState) -> None:
        """The menu has just opened: whatever is held now counts only once it's been let go."""
        d, m = pointing(pad)
        self._held = None
        self._blocked = d is not None and m >= OFF
        self._enter = "enter" in pad.buttons

    def moves(self, pad: PadState, now: float) -> list[Move]:
        """The moves since the last call (one pass of the input timer): at most an enter and one direction."""
        out: list[Move] = []
        enter = "enter" in pad.buttons
        if enter and not self._enter:
            out.append(Move("enter"))
        self._enter = enter
        d, m = pointing(pad)
        if self._blocked:
            if d is None or m < OFF:
                self._blocked = False
            return out
        if self._held is not None and (d != self._held or m < OFF):
            self._held = None            # let go, or pointed elsewhere
        if self._held is None:
            if d is not None and m >= ON:
                self._held, self._next = d, now + self.delay
                out.append(Move(d, m >= COARSE))
        elif now >= self._next:
            self._next = now + self.period
            out.append(Move(d, m >= COARSE))
        return out
