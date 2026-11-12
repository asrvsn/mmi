"""The test server's stage: the axes' travel, their limit switches, and the firmware's rules for them (api.md 6.5),
so a client meets the limits as it would on the unit.

Positions are µm from the as-built pose (the stage joints' zero in mmi), in the unit's frame (api.md 6.2). The travel and the switches are
the CAD's (measured 2026-10-21), turned into that frame.
- **Fine x, y, z:** each stage's lo and hi switches, in series on one input, trip 0.4 mm inside its hard stops. As in
  the firmware, which end tripped is inferred from the way the axis was moving when its input opened. An input found
  open with nothing moving is `?`: both ways are allowed, at no more than `fine.vunk`, until it closes.
- **The rail:**
  - MAX, the carriage's switch at the bearing end, trips at r = +0.6 mm, 1.5 mm before the carriage's stop.
  - MIN, the effector's at the motor end, trips when rail + fine x reach -109.9 mm; the Y/Z stack would meet the
    motor mount at -111.
  - So MIN blocks -x on the rail and on fine x, and MAX blocks only the rail's +x.
- **Snap action:** a switch closes again only DIFF_UM back from where it opened.
- **A hard stop** can be reached only from `?`. Pushing it stalls the axis after 50 ms, and that way stays blocked
  until the axis has run the other way for 50 ms, as with the firmware's current check.
- **Not simulated:** ramps (speeds change at once), contact bounce (the firmware's own business: `firmware/test/`),
  and collisions inside the switches' travel (which would need soft limits).
"""
from __future__ import annotations

import math

AXES = "xyzr"
FINE_STOPS = {"x": (-14000.0, 0.0), "y": (-7500.0, 6500.0), "z": (-550.0, 13450.0)}   # µm: the hard stops (assembly_2's and mmi's kin: limits)
TRIP_INSIDE = 400.0            # µm: a fine stage's switches trip this far inside its stops
RAIL_STOPS = (-149400.0, 2100.0)   # the carriage's hard stops
MAX_AT = 600.0                 # MAX, the carriage's switch: +x, the bearing end (on r)
MIN_AT = -109900.0             # MIN, the effector's switch: -x, the motor end (on r + fine x)
DIFF_UM = 200.0                # snap action: an open switch closes this far back (assumed; not the D2F's datasheet)
STALL_S = 0.05                 # s pushing a hard stop to stall, and s running the other way to lift it
START = (-1000.0, 0.0, 0.0, 0.0)   # the as-built pose, but fine x 1 mm off its stop, where its switch would be open
SUBSTEP_UM = 2.0               # the furthest an axis moves between two looks at the switches


def sign(v: float) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def token(blocked, unknown: bool = False) -> str:
    """As the protocol writes it: '0', or '-', '+', '-+' for the ways blocked, then '?' for an end it can't tell."""
    s = ("-" if -1 in blocked else "") + ("+" if 1 in blocked else "") + ("?" if unknown else "")
    return s or "0"


class Switch:
    """Normally closed, snap action: open once the position reaches `at` going `towards` (+1 / -1), and closed again
    only DIFF_UM back."""

    def __init__(self, at: float, towards: int):
        self.at, self.towards = at, towards
        self.open = False

    def update(self, pos: float) -> bool:
        past = (pos - self.at) * self.towards
        self.open = past > -DIFF_UM if self.open else past >= 0
        return self.open


class Stage:
    """`pos` (µm from the as-built pose) moves at the jog targets, as far as the switches and the firmware's rules let
    it: `step(targets, dt)` returns the events that sends. The state clients mirror is `limit(axis)` and
    `stall(axis)`; `snapshot()` is what a client is told on connecting."""

    def __init__(self, params: dict, start=START):
        self.params = params
        self.start = tuple(float(p) for p in start)
        self.pos = list(self.start)
        self.fine_sw = {a: (Switch(lo + TRIP_INSIDE, -1), Switch(hi - TRIP_INSIDE, 1))
                        for a, (lo, hi) in FINE_STOPS.items()}
        self.rail_sw = {-1: Switch(MIN_AT, -1), 1: Switch(MAX_AT, 1)}   # by the end each guards
        self.end: dict[str, int | str] = {a: 0 for a in "xyz"}   # a fine switch: 0 closed, +1 / -1 open there, '?'
        self.stalls: dict[str, set] = {a: set() for a in "xyz"}
        self.v = [0.0] * 4             # µm/s now, after the blocks and caps (the record's <axis>.v)
        self._push = dict.fromkeys("xyz", 0.0)
        self._away = dict.fromkeys("xyz", 0.0)
        self._lost = False
        self._look(None)               # as found at power-up: a fine switch open is '?'
        self.sent = self._state()      # what clients have been told

    # ---- the state clients mirror
    def limits(self, axis: str) -> set:
        """The ways (-1, +1) the limit switches block on this axis: the effector's switch (MIN) on fine x too."""
        if axis == "r":
            return {end for end, sw in self.rail_sw.items() if sw.open}
        out = {self.end[axis]} if self.end[axis] in (-1, 1) else set()
        if axis == "x" and self.rail_sw[-1].open:
            out.add(-1)
        return out

    def limit(self, axis: str) -> str:
        return token(self.limits(axis), axis != "r" and self.end[axis] == "?")

    def stall(self, axis: str) -> str:
        return token(self.stalls[axis]) if axis != "r" else "0"

    def blocked(self, axis: str) -> set:
        return self.limits(axis) | (self.stalls[axis] if axis != "r" else set())

    def counted(self) -> float:
        """The rail's r.pos: µm counted since power-up, as the firmware counts it."""
        return self.pos[3] - self.start[3]

    def snapshot(self) -> list[str]:
        """The lines that follow a !hello (without the "!"): every axis's limits, and any stall, as last sent."""
        out = [f"limit {a} {self.sent['limit ' + a]}" for a in "xyz"]
        out.append(f"limit r {self.sent['limit r']} pos={self.counted():.0f}")
        return out + [f"stall {a} {self.sent['stall ' + a]}" for a in "xyz" if self.sent["stall " + a] != "0"]

    # ---- moving it
    def velocities(self, targets) -> list[float]:
        """The targets (µm/s) as the firmware takes them: within vmax, within fine.vunk on a fine axis open at an
        unknown end, and 0 into a blocked half-space."""
        out = []
        for i, axis in enumerate(AXES):
            t = float(targets[i])
            vmax = _num(self.params.get(f"{axis}.vmax"))
            if vmax:
                t = max(-vmax, min(vmax, t))
            if axis != "r" and self.end[axis] == "?":
                vunk = _num(self.params.get("fine.vunk")) or 20.0
                t = max(-vunk, min(vunk, t))
            out.append(0.0 if sign(t) in self.blocked(axis) else t)
        return out

    def step(self, targets, dt: float) -> list[str]:
        """dt seconds at these jog targets; returns the events the firmware would send."""
        fastest = max(abs(v) for v in self.velocities(targets))
        n = max(1, math.ceil(fastest * dt / SUBSTEP_UM))
        h = dt / n
        for _ in range(n):   # small steps, so a switch stops its axis where it opens (as the firmware does at once)
            v = self.velocities(targets)
            for i in range(4):
                self.pos[i] += v[i] * h
            self._stops(v, h)
            self._look(v)
        self.v = self.velocities(targets)
        return self._publish()

    def place(self, **um: float) -> list[str]:
        """Puts axes where given (µm from the as-built pose), as if moved by hand: a fine switch it opens is '?'."""
        for axis, p in um.items():
            self.pos[AXES.index(axis)] = float(p)
        self._look(None)
        return self._publish()

    # ---- internals
    def _stops(self, v: list[float], h: float) -> None:
        """The hard stops: an axis can't pass them, and a fine axis pushing one stalls; a stall lifts once the axis
        has run the other way long enough."""
        for i, axis in enumerate("xyz"):
            lo, hi = FINE_STOPS[axis]
            d = sign(v[i])
            at_stop = (d > 0 and self.pos[i] >= hi) or (d < 0 and self.pos[i] <= lo)
            self.pos[i] = min(max(self.pos[i], lo), hi)
            self._push[axis] = self._push[axis] + h if at_stop else 0.0
            if self._push[axis] >= STALL_S:
                self.stalls[axis].add(d)
                self._push[axis] = 0.0
            away = d != 0 and -d in self.stalls[axis]
            self._away[axis] = self._away[axis] + h if away else 0.0
            if self._away[axis] >= STALL_S:
                self.stalls[axis].clear()
                self._away[axis] = 0.0
        self.pos[3] = min(max(self.pos[3], RAIL_STOPS[0]), RAIL_STOPS[1])

    def _look(self, v: list[float] | None) -> None:
        """The switches, and what the firmware makes of them. v: the velocities that brought the stage here (None:
        found as it stands). A fine input opening blocks the end the axis was heading for; with nothing moving, '?'."""
        for i, axis in enumerate("xyz"):
            lo, hi = self.fine_sw[axis]
            was = lo.open or hi.open
            now = lo.update(self.pos[i]) | hi.update(self.pos[i])   # both, always: | not `or`
            if now and not was:
                d = sign(v[i]) if v is not None else 0
                self.end[axis] = d if d else "?"
            elif was and not now:
                self.end[axis] = 0
        for end, sw in self.rail_sw.items():
            was = sw.open
            now = sw.update(self.pos[3] + (self.pos[0] if end < 0 else 0.0))   # MIN rides on the effector
            if now and not was and v is not None and sign(v[3]) == end:
                self._lost = True      # the rail stopped without a ramp: its count may be off

    def _state(self) -> dict[str, str]:
        out = {f"limit {a}": self.limit(a) for a in AXES}
        out.update({f"stall {a}": self.stall(a) for a in "xyz"})
        return out

    def _publish(self) -> list[str]:
        """The events for whatever changed since the last: each axis's limits, each fine axis's stall."""
        now, out = self._state(), []
        for key, tok in now.items():
            if tok != self.sent.get(key):
                out.append(f"{key} {tok}" + (f" pos={self.counted():.0f}" if key == "limit r" else ""))
        self.sent = now
        if self._lost:
            out.append("lost r limit")
            self._lost = False
        return out


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
